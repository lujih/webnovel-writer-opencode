#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
伏笔债去重修复工具。

背景：`_sync_foreshadowing` 在章节被重复提交时会给同一条伏笔反复建债
（state / memory 投影侧按 content 去重，债务侧此前不去重）。真书项目实测：
26 条 open_loop_created 事件对应 141 条债，49 种 note 各重复一次。

本工具**只删可证冗余的行**：
- 同一 debt_type + 同一 note + 同为 active 的多条债，保留 id 最小的一条，
  其余判为重复建债产物；
- 完全没有 created 事件的债（无任何匹配文本，既销不掉也无法归类）单独
  报告，**不删**——它们可能来自旧版本或手工录入，需要人判断。

默认 dry-run：只打印将执行的操作，不写库。`--apply` 才真正执行。
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

# 判为"重复"的债务类型；其他类型不碰（追读债等语义不同，重复未必是 bug）
DEFAULT_DEBT_TYPES = ("foreshadowing",)


@dataclass
class DedupPlan:
    """一次去重修复的完整计划。dry-run 与 apply 共用同一份计划。"""

    total_foreshadowing: int = 0
    duplicate_groups: int = 0
    duplicate_rows: List[Dict[str, Any]] = field(default_factory=list)
    orphan_rows: List[Dict[str, Any]] = field(default_factory=list)
    kept_ids: List[int] = field(default_factory=list)

    @property
    def to_delete(self) -> List[int]:
        return [r["id"] for r in self.duplicate_rows]

    def to_dict(self) -> Dict[str, Any]:
        # 只给计数与 id 列表：完整 note 已在人类可读报告里，JSON 里再重复一遍
        # 会把 stdout 冲垮（真书有 50 行、上百字一条的 note）。
        return {
            "total_foreshadowing": self.total_foreshadowing,
            "duplicate_groups": self.duplicate_groups,
            "duplicate_count": len(self.duplicate_rows),
            "orphan_count": len(self.orphan_rows),
            "kept_count": len(self.kept_ids),
            "to_delete": self.to_delete,
        }


def _open_conn(index_db: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(index_db))


def plan_dedup(index_db: Path, debt_types: tuple[str, ...] = DEFAULT_DEBT_TYPES) -> DedupPlan:
    """只读地算出修复计划，不写库。"""
    plan = DedupPlan()
    with _open_conn(index_db) as conn:
        conn.row_factory = sqlite3.Row
        marks = ",".join("?" * len(debt_types))
        rows = list(conn.execute(
            f"""SELECT d.id, d.debt_type, d.source_chapter, d.status,
                       (SELECT e.note FROM debt_events e
                         WHERE e.debt_id = d.id AND e.event_type = 'created'
                         LIMIT 1) AS note
                FROM chase_debt d
                WHERE d.debt_type IN ({marks})""",
            debt_types,
        ))
    plan.total_foreshadowing = len(rows)

    by_note: Dict[str, List[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        if row["status"] != "active":
            continue
        note = (row["note"] or "").strip()
        if not note:
            # 无 created 事件 → 没有任何可匹配文本，既销不掉也无法归类。
            # 不自动删：可能来自旧版本或手工录入，交人判断。
            plan.orphan_rows.append({
                "id": row["id"], "debt_type": row["debt_type"],
                "source_chapter": row["source_chapter"],
            })
            continue
        by_note[note].append(row)

    for note, group in by_note.items():
        if len(group) < 2:
            plan.kept_ids.append(group[0]["id"])
            continue
        group.sort(key=lambda r: r["id"])
        keeper = group[0]
        plan.kept_ids.append(keeper["id"])
        plan.duplicate_groups += 1
        for extra in group[1:]:
            plan.duplicate_rows.append({
                "id": extra["id"],
                "kept_id": keeper["id"],
                "debt_type": extra["debt_type"],
                "source_chapter": extra["source_chapter"],
                "note": note,
            })
    return plan


def apply_dedup(index_db: Path, plan: DedupPlan) -> int:
    """执行计划：删除重复债及其 debt_events。返回**实际**删除条数。

    返回实际行数而非计划长度：重复执行（或计划已部分失效）时计划里仍列着
    已删除的 id，照数返回会谎报"又删了 N 条"。
    """
    ids = plan.to_delete
    if not ids:
        return 0
    with _open_conn(index_db) as conn:
        marks = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM debt_events WHERE debt_id IN ({marks})", ids)
        deleted = conn.execute(
            f"DELETE FROM chase_debt WHERE id IN ({marks})", ids
        ).rowcount
        conn.commit()
    return max(deleted, 0)


def repair(project_root: Path, *, apply: bool = False,
           debt_types: tuple[str, ...] = DEFAULT_DEBT_TYPES) -> DedupPlan:
    """算出计划；apply=True 时执行。返回计划供调用方打印。"""
    index_db = Path(project_root) / ".webnovel" / "index.db"
    if not index_db.is_file():
        raise FileNotFoundError(f"未找到 index.db: {index_db}")
    plan = plan_dedup(index_db, debt_types)
    if apply:
        apply_dedup(index_db, plan)
    return plan


def format_report(plan: DedupPlan, *, applied: bool) -> str:
    mode = "已执行" if applied else "演练（未写库）"
    lines = [
        f"伏笔债去重 —— {mode}",
        f"  伏笔债总数        : {plan.total_foreshadowing}",
        f"  重复组数          : {plan.duplicate_groups}",
        f"  将删除的重复债    : {len(plan.duplicate_rows)}",
        f"  保留的债          : {len(plan.kept_ids)}",
    ]
    if plan.orphan_rows:
        lines.append(
            f"  ⚠️ 无 created 事件的债: {len(plan.orphan_rows)} 条"
            "（销不掉，但可能来自旧版本/手工录入，本次不动）"
        )
    if plan.duplicate_rows:
        lines.append("")
        lines.append("  重复明细（保留 id → 删除 id）:")
        for row in plan.duplicate_rows[:20]:
            lines.append(
                f"    {row['kept_id']} → 删除 {row['id']}"
                f"  ch{row['source_chapter']}  {row['note'][:36]!r}"
            )
        if len(plan.duplicate_rows) > 20:
            lines.append(f"    … 另有 {len(plan.duplicate_rows) - 20} 条")
    if not applied and plan.duplicate_rows:
        lines.append("")
        lines.append("  加 --apply 执行删除。")
    return "\n".join(lines)
