#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""新功能的效果指标：审查标记误判率、叙事预报与 CED 的关系。

## 为什么需要它

v3.0 的三项能力落地后，只有「有没有运行」的可观测性（覆盖率、待仲裁数），
没有「有没有用」的度量。两个具体问题一直只能靠猜：

1. **门禁分层把 LLM 的 critical 降级成观察标记——那这些标记到底有多准？**
   `flag-triage` 的 confirm/dismiss 比例就是**实测误判率**：dismiss 率高说明
   旧的「critical 即拒章」在大量误杀好章节；confirm 率高说明旧门禁多数时候
   是对的。这个数字是判断「门禁分层这一步走得对不对」的唯一依据。

2. **叙事预报选出的分支，写出来的章是否更少一致性错误？**
   对比「有 forecast 且已选支」的章 与「无 forecast」的章的 CED。

## 两条「不伪造」原则（与 CED 的口径一致）

- **没仲裁过 → 误判率是 None，不是 0**。0 会被读成「LLM 永远正确」，而真相是
  「还没人裁过」。同理 `confirmed+dismissed == 0` 时不产出比例。
- **样本不足 → status=insufficient_data，不产出均值**。两侧各 < 5 章时的均值
  差异没有意义，直接给数字会诱导读者过度解读。

## 与其它模块的边界

本模块**只读**（commit JSON / review_metrics / forecasts 目录），不写任何东西。
写 CED 的是 `review_pipeline`，写 flags 的是 `chapter_commit_service`，
写 forecast 的是 `narrative_forecast`——统计不反向影响它们。
"""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

#: 每侧最少章节数。低于此值不给均值——5 章以下的 CED 差异基本是噪声
MIN_CHAPTERS_PER_SIDE = 5

_CHAPTER_DIR_RE = re.compile(r"chapter_(\d{3,})$")
_CED_KEYS = ("ced_raw", "ced_weighted", "ced_blocking")


def _commits_dir(project_root: Path) -> Path:
    return Path(project_root) / ".story-system" / "commits"


def _forecasts_dir(project_root: Path) -> Path:
    return Path(project_root) / ".story-system" / "forecasts"


def _iter_commit_flags(project_root: Path):
    """逐章产出 (chapter, flags)。读不出来的章跳过（不阻断统计）。"""
    commits = _commits_dir(project_root)
    if not commits.is_dir():
        return
    for path in sorted(commits.glob("chapter_*.commit.json")):
        match = re.search(r"chapter_(\d+)", path.name)
        if not match:
            continue
        chapter = int(match.group(1))
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            continue
        flags = payload.get("review_flags")
        if isinstance(flags, list):
            yield chapter, [f for f in flags if isinstance(f, dict)]


def flag_triage_stats(project_root: Path) -> Dict[str, Any]:
    """审查标记的仲裁统计 —— dismiss 率即 LLM 审查的实测误判率。"""
    counts = {"confirmed": 0, "dismissed": 0, "pending": 0}
    by_severity: Dict[str, Dict[str, int]] = {}
    chapters_scanned = 0
    chapters_with_flags = 0

    for _chapter, flags in _iter_commit_flags(project_root):
        chapters_scanned += 1
        if flags:
            chapters_with_flags += 1
        for flag in flags:
            triage = flag.get("triage")
            if triage == "confirmed":
                key = "confirmed"
            elif triage == "dismissed":
                key = "dismissed"
            else:
                key = "pending"
            counts[key] += 1

            severity = str(flag.get("severity") or "unknown")
            bucket = by_severity.setdefault(
                severity, {"confirmed": 0, "dismissed": 0, "pending": 0})
            bucket[key] += 1

    triaged = counts["confirmed"] + counts["dismissed"]
    # 没有仲裁过 → None。0 会被读成「LLM 永远正确」，而真相是「还没人裁过」。
    dismiss_rate: Optional[float] = (
        round(counts["dismissed"] / triaged, 4) if triaged else None
    )

    return {
        "total_flags": counts["confirmed"] + counts["dismissed"] + counts["pending"],
        **counts,
        "triaged": triaged,
        "dismiss_rate": dismiss_rate,
        "by_severity": by_severity,
        "chapters_scanned": chapters_scanned,
        "chapters_with_flags": chapters_with_flags,
    }


def _chapter_ced(project_root: Path) -> Dict[int, Dict[str, Optional[float]]]:
    """从 `review_metrics` 取每章的有效 CED（同章多条时取最后一条）。

    `review_parseable = 0` 的章记为全 None——审查输出不可解析时 CED 无意义，
    把它当 0 会污染基线。返回 None 与「0 个错误」是两件事。
    """
    db = Path(project_root) / ".webnovel" / "index.db"
    if not db.is_file():
        return {}

    out: Dict[int, Dict[str, Optional[float]]] = {}
    try:
        conn = sqlite3.connect(str(db))
    except sqlite3.Error:
        return {}
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT end_chapter, ced_raw, ced_weighted, ced_blocking, review_parseable
            FROM review_metrics
            ORDER BY end_chapter ASC, rowid ASC
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    finally:
        conn.close()

    for row in rows:
        chapter = int(row["end_chapter"] or 0)
        if chapter <= 0:
            continue
        if int(row["review_parseable"] or 0) == 0:
            out[chapter] = {key: None for key in _CED_KEYS}
            continue
        out[chapter] = {
            key: (float(row[key]) if row[key] is not None else None)
            for key in _CED_KEYS
        }
    return out


def _chapters_with_selection(project_root: Path) -> set:
    """已选支的章号集合（有 selected.json 才算）。"""
    base = _forecasts_dir(project_root)
    if not base.is_dir():
        return set()
    selected = set()
    for child in base.iterdir():
        if not child.is_dir():
            continue
        match = _CHAPTER_DIR_RE.match(child.name)
        if not match:
            continue
        if (child / "selected.json").is_file():
            selected.add(int(match.group(1)))
    return selected


def _mean(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def forecast_effectiveness(project_root: Path) -> Dict[str, Any]:
    """对比「已选支的章」与「未选支的章」的 CED。

    样本不足（任一侧 < MIN_CHAPTERS_PER_SIDE）时返回 `insufficient_data`，
    **不给均值**——5 章以下的差异基本是噪声，给数字会诱导过度解读。
    """
    ced_by_chapter = _chapter_ced(project_root)
    selected = _chapters_with_selection(project_root)

    groups: Dict[str, Dict[str, List[float]]] = {
        "forecast": {k: [] for k in _CED_KEYS},
        "baseline": {k: [] for k in _CED_KEYS},
    }
    counted = {"forecast": 0, "baseline": 0}

    for chapter, row in ced_by_chapter.items():
        bucket = "forecast" if chapter in selected else "baseline"
        counted[bucket] += 1
        for key in _CED_KEYS:
            value = row.get(key)
            if isinstance(value, (int, float)):
                groups[bucket][key].append(float(value))

    result: Dict[str, Any] = {
        "threshold": MIN_CHAPTERS_PER_SIDE,
        "forecast": {
            "chapters": counted["forecast"],
            **{key: _mean(groups["forecast"][key]) for key in _CED_KEYS},
        },
        "baseline": {
            "chapters": counted["baseline"],
            **{key: _mean(groups["baseline"][key]) for key in _CED_KEYS},
        },
    }

    if (counted["forecast"] < MIN_CHAPTERS_PER_SIDE
            or counted["baseline"] < MIN_CHAPTERS_PER_SIDE):
        result["status"] = "insufficient_data"
        result["reason"] = (
            f"需两侧各至少 {MIN_CHAPTERS_PER_SIDE} 章才给对比："
            f"已选支 {counted['forecast']} 章、未选支 {counted['baseline']} 章。"
        )
        result["delta"] = {key: None for key in _CED_KEYS}
        return result

    result["status"] = "ok"
    result["delta"] = {}
    for key in _CED_KEYS:
        a = result["forecast"][key]
        b = result["baseline"][key]
        # 差值 = 已选支 − 未选支；负数表示「用了预报的章错误更少」
        result["delta"][key] = round(a - b, 4) if (a is not None and b is not None) else None
    return result
