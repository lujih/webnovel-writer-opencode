#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
审查结果 schema（v6）。

替代原 checker-output-schema.md 的评分制，改为结构化问题清单。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from security_utils import atomic_write_json
except ImportError:  # pragma: no cover
    from scripts.security_utils import atomic_write_json

VALID_SEVERITIES = {"critical", "high", "medium", "low"}

#: `_shape_issue()` 使用的固定 summary。下游用它识别"审查输出不可解析"——
#: 此时 CED 不应计算（解析失败不是一致性错误，否则趋势图出现虚假尖峰）。
#:
#: 不能用 category 判断：`review_output_malformed` 不在 VALID_CATEGORIES 里，
#: `ReviewIssue.__post_init__` 会把它降级成 `other`，`categories` 里查不到。
UNPARSEABLE_SUMMARY = "审查结果不可解析，已阻断提交。"

#: `_shape_issue()` 的合成 issue 的 category 与描述前缀——下游用它们识别
#: 「技术故障信号」而非 LLM 语义判定。category 会被 `__post_init__` 降级成
#: other（VALID_CATEGORIES 外），故靠前缀兜底。
MALFORMED_CATEGORY = "review_output_malformed"
MALFORMED_DESCRIPTION_PREFIX = "审查结果形状异常"
UNPARSEABLE_DESCRIPTION_PREFIX = "审查结果不可解析"

#: CED 加权口径用的 severity 权重。
#:
#: 为什么不用"severity 越严重值越小"的直观设计：这里是**计数权重**，
#: critical 计 4.0、low 计 1.0，所以同等字数下 4 个 low 与 1 个 critical
#: 等权。这让"少量严重问题"与"大量轻微问题"在同一坐标上可比，
#: 而 ced_blocking 单独负责"只看阻断项"的严格口径。
SEVERITY_WEIGHTS: Dict[str, float] = {
    "critical": 4.0,
    "high": 3.0,
    "medium": 2.0,
    "low": 1.0,
}

#: CED 的归一化基数：每 1 万字的一致性错误数。
#:
#: **中文口径**：`_count_chapter_words()` 用 `len(text)` 计的是**字符数**，
#: 即中文的"字数"；ConStory-Bench 的 CED 是"每万词"（英文 word count）。
#: 两者单位不同，不可直接横向比较数值，只能比较同一口径下的趋势。
CED_SCALE = 10000.0
VALID_CATEGORIES = {
    "continuity", "setting", "character", "timeline",
    "ai_flavor", "logic", "pacing", "other",
}
SCORE_CATEGORIES = (
    "continuity",
    "setting",
    "character",
    "timeline",
    "ai_flavor",
    "logic",
    "pacing",
    "other",
)
CONTENT_DIMENSIONS = {"continuity", "setting", "character", "timeline", "ai_flavor", "logic", "pacing"}
SYSTEM_DIMENSIONS = {"other"}

SEVERITY_PENALTIES = {
    "critical": 35.0,
    "high": 15.0,
    "medium": 6.0,
    "low": 2.0,
}


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(100.0, value)), 2)


def _issue_penalty(issue: "ReviewIssue") -> float:
    return float(SEVERITY_PENALTIES.get(issue.severity, SEVERITY_PENALTIES["medium"]))


@dataclass
class ReviewIssue:
    severity: str
    category: str = "other"
    location: str = ""
    description: str = ""
    evidence: str = ""
    fix_hint: str = ""
    blocking: Optional[bool] = None

    def __post_init__(self):
        if self.severity not in VALID_SEVERITIES:
            self.severity = "medium"
        if self.category not in VALID_CATEGORIES:
            self.category = "other"
        # critical 强制 blocking=True，不信任 LLM 输出的 blocking 字段
        if self.severity == "critical":
            self.blocking = True
        elif self.blocking is None:
            self.blocking = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewResult:
    chapter: int
    issues: List[ReviewIssue] = field(default_factory=list)
    summary: str = ""
    #: 审查输出是否可解析。`_shape_issue()` 会置 1 表示"载荷畸形"。
    #:
    #: 为什么要显式标记而不是靠 category/summary 推断：
    #: `review_output_malformed` 不在 VALID_CATEGORIES 里，会被
    #: `__post_init__` 降级成 `other`；`summary` 又不在 `to_metrics_dict()`
    #: 的输出里。两条间接路径都断过，故用显式字段。
    unparseable: bool = False
    #: 模型自己声明的阻断数。仅参与「取更严格一方」，不是权威值——
    #: 权威值始终是从 issues 推导的 `_derived_blocking_count`。
    declared_blocking_count: int = 0

    @property
    def issues_count(self) -> int:
        return len(self.issues)

    @property
    def _derived_blocking_count(self) -> int:
        return sum(1 for i in self.issues if i.blocking)

    @property
    def blocking_count(self) -> int:
        """阻断数 = max(从 issues 推导, 模型声明)。

        为什么要取 max：本项目已确立「不直接信任 LLM 原始值」——模型说
        `blocking_count: 0` 但列了 critical issue 时必须阻断，这个方向由
        issues 侧保证。但反方向同样要成立：模型报了 `blocking_count: 3`
        却没展开明细时，纯 issues 自算会得 0 并**放行这一章**。
        「无法判断具体问题、但已知有问题」按本仓库的 fail-closed 取向
        应当阻断，而不是当成没问题。
        """
        return max(self._derived_blocking_count, self.declared_blocking_count)

    @property
    def has_blocking(self) -> bool:
        return self.blocking_count > 0

    @property
    def severity_counts(self) -> Dict[str, int]:
        counts = {level: 0 for level in ("critical", "high", "medium", "low")}
        for issue in self.issues:
            severity = issue.severity if issue.severity in counts else "medium"
            counts[severity] += 1
        return counts

    @property
    def categories(self) -> List[str]:
        return sorted(set(i.category for i in self.issues))

    @property
    def critical_issues(self) -> List[str]:
        return [
            issue.description
            for issue in self.issues
            if issue.severity == "critical" and issue.description
        ]

    def _build_dimension_scores(self) -> Dict[str, float]:
        scores = {category: 100.0 for category in SCORE_CATEGORIES}
        for issue in self.issues:
            category = issue.category if issue.category in scores else "other"
            scores[category] = _clamp_score(scores[category] - _issue_penalty(issue))
        return scores

    def _build_notes(self, categories: List[str]) -> str:
        parts: List[str] = []
        if self.summary:
            parts.append(self.summary)
        parts.append(f"issues={self.issues_count}")
        parts.append(f"blocking={self.blocking_count}")
        if categories:
            parts.append("categories=" + ",".join(categories))
        return " | ".join(parts)

    def _calculate_overall_score(self) -> float:
        dim_scores = self._build_dimension_scores()
        content_scores = {d: s for d, s in dim_scores.items() if d in CONTENT_DIMENSIONS}
        if not content_scores:
            return 0.0
        return float(round(sum(content_scores.values()) / len(content_scores)))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "chapter": self.chapter,
            "issues": [i.to_dict() for i in self.issues],
            "issues_count": self.issues_count,
            "blocking_count": self.blocking_count,
            # 两个来源分开暴露，便于诊断「为什么阻断」与「计数从哪来」
            "blocking_count_derived": self._derived_blocking_count,
            "blocking_count_declared": self.declared_blocking_count,
            "has_blocking": self.has_blocking,
            "summary": self.summary,
        }

    def compute_ced_for(self, chapter_words: int) -> Dict[str, Any]:
        """按给定字数计算本结果的三个 CED 口径。

        只是 `compute_ced()` 的薄封装，让调用方写
        `result.compute_ced_for(words)` 而不必自己拆 issues。
        """
        ced = compute_ced(self.issues, chapter_words)
        ced.pop("chapter_words", None)      # 由调用方统一决定是否带上
        return ced

    def to_metrics_dict(self, report_file: str = "") -> Dict[str, Any]:
        categories = self.categories
        severity_counts = self.severity_counts
        return {
            "chapter": self.chapter,
            "start_chapter": self.chapter,
            "end_chapter": self.chapter,
            "overall_score": self._calculate_overall_score(),
            "dimension_scores": self._build_dimension_scores(),
            "severity_counts": severity_counts,
            "critical_issues": self.critical_issues,
            "report_file": report_file,
            "notes": self._build_notes(categories),
            "issues_count": self.issues_count,
            "blocking_count": self.blocking_count,
            "categories": categories,
            "review_parseable": 0 if self.unparseable else 1,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }


def compute_ced(
    issues: "List[ReviewIssue]",
    chapter_words: int,
) -> Dict[str, Any]:
    """计算三个口径的 CED（一致性错误密度）。

    口径定义（**唯一定义处**，`review_pipeline` 与文档都引用这里）：

    - ``ced_raw``       = len(issues) / chapter_words × 10000
                          每个 issue 计 1，与 ConStory-Bench 的 CED 同义
    - ``ced_weighted``  = Σ(severity 权重) / chapter_words × 10000
                          critical 4.0 / high 3.0 / medium 2.0 / low 1.0
    - ``ced_blocking``  = blocking 的 issue 数 / chapter_words × 10000

    返回 ``{"chapter_words", "ced_raw", "ced_weighted", "ced_blocking"}``。

    **返回 None 而不是 0 的情况**（关键区分）：
    - ``chapter_words <= 0`` → 取不到有效分母（章节文件缺失、空文件、
      纯 markdown 语法文件）。此时三个口径全为 None——不得除以 0，
      也不得伪造一个 0（那会被读成"这一章完美无问题"）。
    - ``issues`` 为空且分母有效 → 三个口径都是 0.0（真的没发现问题）。

    区分这两者对趋势图至关重要：不可计算的章节应该不画点，
    而不是画在 0 位上。
    """
    words = int(chapter_words or 0)
    if words <= 0:
        return {
            "chapter_words": 0,
            "ced_raw": None,
            "ced_weighted": None,
            "ced_blocking": None,
        }

    total_issues = len(issues)
    weighted = sum(SEVERITY_WEIGHTS.get(str(i.severity), 1.0) for i in issues)
    blocking = sum(1 for i in issues if i.blocking)

    return {
        "chapter_words": words,
        "ced_raw": round(total_issues / words * CED_SCALE, 4),
        "ced_weighted": round(weighted / words * CED_SCALE, 4),
        "ced_blocking": round(blocking / words * CED_SCALE, 4),
    }


def _shape_issue(chapter: int, detail: str) -> ReviewResult:
    """审查载荷形状异常时的 fail-closed 结果。

    形状异常意味着「无法判断这一章有没有问题」，而不是「没有问题」。旧实现把
    非 dict 元素静默 continue 掉，于是 issues 为空 ⇒ has_blocking=False、
    overall_score=100.0 ⇒ 被审查否掉的章节以满分通过身份写进 SSOT 事件日志，
    且两个闸门（review_pipeline.build_review_artifacts 与
    chapter_commit_service.build_commit）都只读这一个对象，无从发现。

    这里合成一条 critical + blocking 的问题，把判定交回人工/修复循环。
    """
    return ReviewResult(
        chapter=chapter,
        unparseable=True,
        issues=[ReviewIssue(
            severity="critical",
            category="review_output_malformed",
            description=f"审查结果形状异常，无法判定本章是否存在问题：{detail}。已按阻断处理，请人工复核。",
            blocking=True,
        )],
        summary=UNPARSEABLE_SUMMARY,
    )


def parse_review_output(chapter: int, raw: Dict[str, Any]) -> ReviewResult:
    if not isinstance(raw, dict):
        return _shape_issue(chapter, f"顶层载荷为 {type(raw).__name__}，预期为对象")

    raw_issues = raw.get("issues", [])
    if raw_issues is None:
        raw_issues = []
    if not isinstance(raw_issues, list):
        # 对象/字符串被迭代会产出键或字符，逐元素 isinstance(dict) 全部落空
        return _shape_issue(chapter, f"issues 为 {type(raw_issues).__name__}，预期为数组")

    issues = []
    malformed = 0
    empty = 0
    for item in raw_issues:
        if not isinstance(item, dict):
            malformed += 1
            continue
        # 完全空的 issue 对象：模型占了个位却说不出任何内容。
        # 与「issues 非数组」同类——都是「报了问题但无法判断」，
        # 按 fail-closed 不应放行。空对象此前被归一成
        # severity=medium / description="" 的合法 issue，于是很容易通过。
        if not any(str(item.get(k) or "").strip() for k in
                   ("severity", "description", "evidence", "fix_hint")):
            empty += 1
            continue
        issues.append(ReviewIssue(
            severity=str(item.get("severity", "medium")),
            category=str(item.get("category", "other")),
            location=str(item.get("location", "")),
            description=str(item.get("description", "")),
            evidence=str(item.get("evidence", "")),
            fix_hint=str(item.get("fix_hint", "")),
            blocking=item.get("blocking"),
        ))
    if malformed:
        # 保留已解析出的有效问题（不丢信息），并补一条 critical 阻断项：
        # 无法确认被丢弃的部分里是否藏着更严重的问题。
        issues.append(ReviewIssue(
            severity="critical",
            category="review_output_malformed",
            description=(f"issues 数组中有 {malformed} 个元素不是对象，其内容未被解析。"
                        "已按阻断处理，请人工复核。"),
            blocking=True,
        ))
    if empty:
        issues.append(ReviewIssue(
            severity="critical",
            category="review_output_malformed",
            description=(f"issues 数组中有 {empty} 个元素是空对象，没有任何可判读的内容。"
                        "已按阻断处理，请人工复核。"),
            blocking=True,
        ))
    # unparseable 只能由 `review_parseable` 字段（0 = 不可解析）显式声明——
    # 这是 review_pipeline._shape_issue 路径写进 metrics 的标记。
    # 不能用 `issues 里有没有 review_output_malformed` 来推：
    # ① 模型自己也可能输出该 category 的 issue（误用）；
    # ② _shape_issue 合成的 critical 是 fail-closed 技术信号，
    #    与「LLM 判了 critical」是两回事，必须靠显式字段区分。
    unparseable = int(raw.get("review_parseable", 1)) == 0
    return ReviewResult(
        chapter=chapter,
        issues=issues,
        summary=str(raw.get("summary", "")),
        declared_blocking_count=_coerce_blocking_count(raw.get("blocking_count")),
        unparseable=unparseable,
    )


def _coerce_blocking_count(value: Any) -> int:
    """把模型声明的 blocking_count 归一成非负整数。

    模型常把它写成字符串（`"3"`）或浮点（`3.7`），负数与垃圾值都按 0 处理
    （「-1 个阻断」没有意义，不该反而增加阻断数）。
    """
    if value is None or isinstance(value, bool):
        return 0
    try:
        count = int(float(value))
    except (TypeError, ValueError):
        return 0
    return max(0, count)


def _read_json_if_exists(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Bad JSON in {path}") from exc


def _write_json(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload, backup=True)


def append_ai_flavor_anti_patterns(project_root: str | Path, result: ReviewResult) -> int:
    root = Path(project_root).expanduser().resolve()
    path = root / ".story-system" / "anti_patterns.json"
    existing = _read_json_if_exists(path) or []
    if not isinstance(existing, list):
        existing = []

    seen_texts = {str(item.get("text") or "").strip() for item in existing if isinstance(item, dict)}
    additions: List[Dict[str, Any]] = []
    for index, issue in enumerate(result.issues, start=1):
        if issue.category != "ai_flavor" or issue.severity not in {"medium", "high", "critical"}:
            continue
        text = (issue.evidence or issue.description or "").strip()[:200]
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        additions.append(
            {
                "text": text,
                "source_table": "review_extracted",
                "source_id": f"ch{int(result.chapter):04d}_issue_{index}",
                "category": issue.category,
                "added_at": datetime.now().isoformat(timespec="seconds"),
            }
        )

    if additions:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_json(path, [*existing, *additions])
    return len(additions)
