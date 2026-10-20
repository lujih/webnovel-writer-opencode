#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 3 审查结果处理。

读取 reviewer agent 的原始输出 JSON，解析为 ReviewResult，
生成 metrics 用于 index.db 沉淀。
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, List

from runtime_compat import enable_windows_utf8_stdio

from data_modules.review_schema import SYSTEM_DIMENSIONS, _shape_issue

logger = logging.getLogger(__name__)


def _ensure_scripts_path() -> None:
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


_ensure_scripts_path()

from data_modules.review_schema import (
    append_ai_flavor_anti_patterns,
    parse_review_output,
    CONTENT_DIMENSIONS,
    SYSTEM_DIMENSIONS,
)


def _sanitize_json_text(raw: str, aggressive: bool = False) -> str:
    """Normalize non-ASCII quotes and strip BOM before JSON parse.

    In normal mode, curly quotes become corner brackets 「」 (safe content preservation).
    In aggressive mode, all non-ASCII double quotes become ASCII \" (JSON delimiter fix).
    """
    sanitized = raw.replace("“", "「").replace("”", "」")  # curly → corner
    if aggressive:
        # 注意：不转换「」——它们是 line 43 从 curly quotes 安全转换来的，
        # 转回 ASCII " 会破坏 JSON 字符串值中的内容。
        for ch in ("“", "”", "＂"):
            sanitized = sanitized.replace(ch, '"')
    sanitized = sanitized.lstrip("﻿")
    return sanitized


def clean_reviewer_output(raw: str) -> dict:
    """Extract pure JSON from reviewer agent output and parse it.

    Handles:
    1. Markdown code block: ```json ... ```
    2. Prefix text: \"Here is the review: {...}\"
    3. Suffix text: \"{...} That's the review\"
    4. Pure JSON: \"{...}\"
    5. Bare ASCII \" inside CJK text values (sanitized as fallback)
    """
    if not raw or not raw.strip():
        raise ValueError("reviewer output is empty")

    # Try markdown code block first (greedy match: first opening to LAST closing, handles nested ``` in JSON values)
    m = re.search(r'```(?:json)?\s*\n(.*)```', raw, re.DOTALL)
    if m:
        json_str = m.group(1).strip()
    else:
        # Find first { and last }
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            json_str = raw[start : end + 1]
        else:
            raise ValueError("no valid JSON found in reviewer output")

    json_str = _sanitize_json_text(json_str)
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        json_str = _sanitize_json_text(json_str, aggressive=True)
        try:
            return json.loads(json_str)
        except json.JSONDecodeError as e:
            preview = json_str[:500] + "..." if len(json_str) > 500 else json_str
            raise ValueError(f"JSON解析失败，raw前500字符: {preview}") from e


def _assemble_artifacts(
    *,
    self_project_root: Path,
    chapter: int,
    result,
    report_file: str,
) -> Dict[str, Any]:
    """把 ReviewResult 组装成 build_review_artifacts 的返回结构。

    抽成函数是因为 fail-closed 与正常两条路径都要走这同一段组装逻辑——
    此前各写一遍，改 CED 字段时容易漏掉其中一条。
    """
    anti_patterns_added = append_ai_flavor_anti_patterns(self_project_root, result)
    metrics = result.to_metrics_dict(report_file=report_file)
    metrics["chapter_words"] = _count_chapter_words_for(self_project_root, chapter)
    metrics.update(result.compute_ced_for(metrics["chapter_words"]))

    dim_scores = metrics.get("dimension_scores", {})
    system_health = {d: dim_scores.get(d, 0) for d in SYSTEM_DIMENSIONS}
    return {
        "chapter": chapter,
        "review_result": result.to_dict(),
        "metrics": metrics,
        "score": metrics["overall_score"],
        "overall_score": metrics["overall_score"],
        "system_health": system_health,
        "anti_patterns_added": anti_patterns_added,
    }


def _count_chapter_words_for(project_root: Path, chapter: int) -> int:
    """取本章正文字数；取不到返回 0。

    复用 `state_projection_writer._count_chapter_words()` 的口径（去 markdown
    语法后 `len()`），与 `progress.total_words` 累加用的是同一个函数——
    CED 的分母因此与既有字数统计完全一致，不会出现"两套字数"。

    定位不到文件时返回 0（不是猜一个值）：CED 由 `compute_ced()` 判空。
    """
    if chapter <= 0:
        return 0
    try:
        from chapter_paths import find_chapter_file

        chapter_file = find_chapter_file(Path(project_root), int(chapter))
        if chapter_file is None or not chapter_file.is_file():
            return 0
        content = chapter_file.read_text(encoding="utf-8")

        from data_modules.state_projection_writer import StateProjectionWriter
        return StateProjectionWriter._count_chapter_words(content)
    except (OSError, ValueError, ImportError) as exc:
        logger.warning("无法读取第 %s 章正文以计算字数: %s", chapter, exc)
        return 0


def _resolve_report_path(project_root: Path, report_file: str) -> Path:
    root = project_root.expanduser().resolve()
    report_path = Path(report_file).expanduser()
    if not report_path.is_absolute():
        report_path = root / report_path
    report_path = report_path.resolve()
    try:
        report_path.relative_to(root)
    except ValueError as exc:
        raise ValueError("report-file 必须位于 project_root 目录内") from exc
    return report_path


def _format_issue(issue: Dict[str, Any], index: int) -> List[str]:
    description = str(issue.get("description") or "未填写问题描述")
    severity = str(issue.get("severity") or "medium")
    category = str(issue.get("category") or "other")
    location = str(issue.get("location") or "未标注位置")
    evidence = str(issue.get("evidence") or "未提供证据")
    fix_hint = str(issue.get("fix_hint") or "未提供修复方向")
    blocking = "是" if issue.get("blocking") else "否"

    return [
        f"{index}. **{description}**",
        f"   - 严重级别：{severity}",
        f"   - 分类：{category}",
        f"   - 位置：{location}",
        f"   - 阻断：{blocking}",
        f"   - 证据：{evidence}",
        f"   - 修复方向：{fix_hint}",
    ]


def render_review_report(payload: Dict[str, Any]) -> str:
    result = payload["review_result"]
    metrics = payload["metrics"]
    issues = list(result.get("issues", []))
    blocking_issues = [issue for issue in issues if issue.get("blocking")]
    non_blocking_issues = [issue for issue in issues if not issue.get("blocking")]
    severity_counts = metrics.get("severity_counts", {})

    lines: List[str] = [
        f"# 第{payload['chapter']}章审查报告",
        "",
        "## 总览",
        "",
        f"- 问题数：{result.get('issues_count', 0)}",
        f"- 阻断数：{result.get('blocking_count', 0)}",
        f"- 结论：{'需修复后重审' if result.get('has_blocking') else '无阻断问题'}",
    ]
    summary = str(result.get("summary") or "").strip()
    if summary:
        lines.append(f"- 摘要：{summary}")
    if severity_counts:
        ordered = [
            f"{level}={severity_counts.get(level, 0)}"
            for level in ("critical", "high", "medium", "low")
        ]
        lines.append(f"- 严重级别统计：{', '.join(ordered)}")

    lines.extend(["", "## 阻断问题", ""])
    if blocking_issues:
        for index, issue in enumerate(blocking_issues, start=1):
            lines.extend(_format_issue(issue, index))
            lines.append("")
    else:
        lines.append("无。")
        lines.append("")

    lines.extend(["## 其他问题", ""])
    if non_blocking_issues:
        for index, issue in enumerate(non_blocking_issues, start=1):
            lines.extend(_format_issue(issue, index))
            lines.append("")
    else:
        lines.append("无。")
        lines.append("")

    lines.extend(["## 修复方向", ""])
    if issues:
        ordered_issues = [*blocking_issues, *non_blocking_issues]
        for index, issue in enumerate(ordered_issues, start=1):
            description = str(issue.get("description") or "未填写问题描述")
            fix_hint = str(issue.get("fix_hint") or "未提供修复方向")
            lines.append(f"{index}. {description}：{fix_hint}")
    else:
        lines.append("暂无需要修复的问题。")

    return "\n".join(lines).rstrip() + "\n"


def write_review_report(project_root: Path, report_file: str, payload: Dict[str, Any]) -> Path:
    report_path = _resolve_report_path(project_root, report_file)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_review_report(payload), encoding="utf-8")
    return report_path


#: 审查输出不可解析时的 summary 文案（由 review_schema 唯一定义）。
#: 用它识别"这一章的 CED 不该被计算"——一次性解析失败不是一致性错误，
#: 照常算会在趋势图上制造无法解释的尖峰。


def _build_review_metrics_record(metrics: Dict[str, Any]):
    """把 metrics dict 转成 ReviewMetrics 记录。

    CED 三个口径与字数的语义见 `review_schema.compute_ced()` 的 docstring
    （唯一定义处）。这里只负责搬运，并处理一个特例：

    **不可解析的审查输出不产出 CED。** `_shape_issue()` 会合成一条 critical
    issue，于是 issues_count=1。若照常算，这一章的 CED 会是
    "1 / 字数 × 10000"——一个纯粹的解析失败被算成了"发现一致性问题"，
    在趋势图上是个无法解释的尖峰。故用 `review_parseable=0` 标记并清空 CED。

    parseable 的来源是 `review_schema.to_metrics_dict()` 直接输出的
    `review_parseable` 字段（底层是 `ReviewResult.unparseable`）。
    不用 category 或 summary 推断：前者会被 `__post_init__` 降级成
    `other`，后者根本不在 to_metrics_dict 的输出里——两条间接路径都断过。
    """
    from data_modules.index_manager import ReviewMetrics

    # 注意：不能写成 `or 1`——`review_parseable=0` 是合法值（审查输出不可
    # 解析），`0 or 1` 会把它吃成 1。本会话已在 budget.strip() 与
    # hybrid_search 的 top_k 上踩过同一个 falsy 陷阱。
    if metrics.get("review_parseable") is None:
        parseable = 1
    else:
        parseable = int(metrics.get("review_parseable") or 0)
    chapter_words = int(metrics.get("chapter_words") or 0)

    if parseable == 0:
        # 解析失败：保留字数（分母是客观事实），但不给 CED
        ced_raw = ced_weighted = ced_blocking = None
    else:
        ced_raw = metrics.get("ced_raw")
        ced_weighted = metrics.get("ced_weighted")
        ced_blocking = metrics.get("ced_blocking")

    return ReviewMetrics(
        start_chapter=int(metrics["start_chapter"]),
        end_chapter=int(metrics["end_chapter"]),
        overall_score=float(metrics.get("overall_score", 0.0)),
        dimension_scores=dict(metrics.get("dimension_scores", {})),
        severity_counts=dict(metrics.get("severity_counts", {})),
        critical_issues=list(metrics.get("critical_issues", [])),
        report_file=str(metrics.get("report_file", "")),
        notes=str(metrics.get("notes", "")),
        chapter_words=chapter_words,
        ced_raw=ced_raw,
        ced_weighted=ced_weighted,
        ced_blocking=ced_blocking,
        review_parseable=parseable,
    )


def build_review_artifacts(
    project_root: Path,
    chapter: int,
    review_results_path: Path,
    report_file: str = "",
) -> Dict[str, Any]:
    raw_text = review_results_path.read_text(encoding="utf-8-sig")

    # 不可解析的审查输出必须走 **fail-closed**，与「JSON 畸形」同一条路径。
    #
    # 此前 clean_reviewer_output 抛 ValueError 就一路冒到调用方：审查流程
    # 中断、不产出 review_result，而下游 chapter-commit 需要它——整章卡死，
    # 用户看到的是 "no valid JSON found in reviewer output" 这种内部术语。
    #
    # 这与本仓库已确立的立场不一致。`review_schema._shape_issue` 的 docstring
    # 写得很清楚：「形状异常意味着『无法判断这一章有没有问题』，而不是
    # 『没有问题』……合成一条 critical + blocking 的问题，把判定交回人工/
    # 修复循环」。非 JSON 输出是同一问题的更极端版本——模型可能回了散文、
    # 可能被截断、可能忘了给 JSON，这些在真实使用里都很常见。
    try:
        raw = clean_reviewer_output(raw_text)
    except ValueError as exc:
        logger.warning("reviewer output 不可解析（chapter %s）：%s", chapter, exc)
        # 直接复用 `_shape_issue()`，不在这里另造一份 raw dict。
        #
        # 此前这里自己构造 `{"issues": [...], "summary": ...}`，虽然同样是
        # fail-closed，但 `category: review_output_malformed` 会被
        # `ReviewIssue.__post_init__` 降级成 `other`、summary 又不进
        # `to_metrics_dict()`——下游**没有一个可靠信号**能识别"这一章的审查
        # 输出其实没解析成功"。CED 集成需要这个标记来区分"纯解析失败"与
        # "真的发现了一致性问题"，否则趋势图出现虚假尖峰。
        result = _shape_issue(chapter, f"审查输出无法解析为 JSON：{exc}")
        return _assemble_artifacts(
            self_project_root=project_root,
            chapter=chapter,
            result=result,
            report_file=report_file,
        )

    result = parse_review_output(chapter=chapter, raw=raw)
    return _assemble_artifacts(
        self_project_root=project_root,
        chapter=chapter,
        result=result,
        report_file=report_file,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Review pipeline v6")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--review-results", required=True)
    parser.add_argument("--metrics-out", default="")
    parser.add_argument("--report-file", default="")
    parser.add_argument("--save-metrics", action="store_true",
                        help="直接写入 index.db，省去单独调用 save-review-metrics")

    args = parser.parse_args()
    project_root = Path(args.project_root)
    review_results_path = Path(args.review_results)

    payload = build_review_artifacts(
        project_root=project_root,
        chapter=args.chapter,
        review_results_path=review_results_path,
        report_file=args.report_file,
    )

    if args.metrics_out:
        out_path = Path(args.metrics_out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(payload["metrics"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    if args.report_file:
        write_review_report(
            project_root=project_root,
            report_file=args.report_file,
            payload=payload,
        )

    if args.save_metrics:
        from data_modules.config import DataModulesConfig
        from data_modules.index_manager import IndexManager
        config = DataModulesConfig.from_project_root(project_root)
        manager = IndexManager(config)
        manager.save_review_metrics(_build_review_metrics_record(payload["metrics"]))

    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
