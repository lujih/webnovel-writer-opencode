#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scratchpad 压缩器。
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from .schema import CATEGORY_KEY_RULES, CATEGORY_TO_BUCKET, MemoryItem, ScratchpadData, memory_item_key, now_iso


def _key_for(item: MemoryItem) -> Tuple:
    return memory_item_key(item)


def _is_resolved_open_loop(item: MemoryItem) -> bool:
    if item.category != "open_loop":
        return False
    state = str((item.payload or {}).get("status", "") or "").strip().lower()
    return state in {"resolved", "closed", "done", "paid_off", "payoff"}


def collect_garbage(data: ScratchpadData) -> ScratchpadData:
    """清理 outdated 条目和已回收伏笔。每章写后调用，无容量门槛。"""
    # 1) 删除所有 outdated 条目
    for bucket in CATEGORY_TO_BUCKET.values():
        rows: List[MemoryItem] = list(getattr(data, bucket))
        cleaned = [row for row in rows if row.status != "outdated"]
        setattr(data, bucket, cleaned)

    # 2) 清理已回收伏笔
    data.open_loops = [row for row in data.open_loops if not _is_resolved_open_loop(row)]

    return data


def enforce_capacity(data: ScratchpadData, max_items: int = 500) -> ScratchpadData:
    """仅当条目数超过 max_items 时压缩 timeline + 全局截断。"""
    if data.count_items() <= max_items:
        return data

    # 3) 压缩过旧 timeline（与当前最新章节相距 50 章以上）
    timeline = sorted(data.timeline, key=lambda x: x.source_chapter)
    if timeline:
        latest_chapter = max(x.source_chapter for x in timeline)
        old = [x for x in timeline if (latest_chapter - x.source_chapter) > 50]
        fresh = [x for x in timeline if (latest_chapter - x.source_chapter) <= 50]
        if len(old) > 1:
            samples = []
            for row in old[:8]:
                label = row.value or row.subject or row.field or row.id
                if label:
                    samples.append(str(label))
            summary_text = "；".join(samples) if samples else "早期关键事件"
            summary_item = MemoryItem(
                id=f"timeline-summary-upto-{old[-1].source_chapter}",
                layer="semantic", category="story_fact",
                subject="timeline_summary",
                field=f"<=ch{old[-1].source_chapter}",
                value=f"早期事件摘要：{summary_text}",
                payload={
                    "from_chapter": old[0].source_chapter,
                    "to_chapter": old[-1].source_chapter,
                    "items_merged": len(old),
                },
                status="active",
                source_chapter=old[-1].source_chapter,
                evidence=["compactor:timeline"],
                updated_at=now_iso(),
            )
            replaced = False
            for i, row in enumerate(list(data.story_facts)):
                if row.subject == summary_item.subject and row.subject == "timeline_summary":
                    data.story_facts[i] = summary_item
                    replaced = True
                    break
            if not replaced:
                data.story_facts.append(summary_item)
            data.timeline = fresh
        elif len(old) == 1:
            # 只有一条旧事件时不摘要也不丢弃：摘要会把一条具体事实压成
            # "早期关键事件" 这类泛化文本，丢掉它的内容；而留着只多一行。
            # 此前 data.timeline = fresh 在 if len(old) > 1 之外无条件执行，
            # 这一条旧事件被静默删除，无摘要、无痕迹。
            pass
        else:
            data.timeline = fresh

    # 4) 若仍超限，全局截断
    if data.count_items() > max_items:
        ranked: List[Tuple[str, MemoryItem]] = []
        for bucket in CATEGORY_TO_BUCKET.values():
            for row in list(getattr(data, bucket)):
                ranked.append((bucket, row))
        ranked.sort(
            key=lambda item: (
                0 if item[1].status == "active" else 1,
                -int(item[1].source_chapter or 0),
                item[1].updated_at or "",
            )
        )
        keep = ranked[:max_items]
        kept_ids = {item.id for _, item in keep}
        for bucket in CATEGORY_TO_BUCKET.values():
            rows = [row for row in list(getattr(data, bucket)) if row.id in kept_ids]
            setattr(data, bucket, rows)

    data.meta = {**dict(data.meta or {}), "last_updated": now_iso(), "total_items": data.count_items()}
    return data


def compact_scratchpad(data: ScratchpadData, max_items: int = 500) -> ScratchpadData:
    """兼容旧调用：先 GC 再容量控制。"""
    data = collect_garbage(data)
    return enforce_capacity(data, max_items)
