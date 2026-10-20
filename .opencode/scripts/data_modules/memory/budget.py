#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
长期记忆预算分配。
"""
from __future__ import annotations

from typing import Dict


DEFAULT_BUDGET = {
    "write": {"max_items": 30, "working_ratio": 0.45, "episodic_ratio": 0.30, "semantic_ratio": 0.25},
    "review": {"max_items": 40, "working_ratio": 0.35, "episodic_ratio": 0.35, "semantic_ratio": 0.30},
    "query": {"max_items": 25, "working_ratio": 0.30, "episodic_ratio": 0.45, "semantic_ratio": 0.25},
}


def get_budget(task_type: str = "write") -> Dict[str, float]:
    """按任务类型取预算配置。

    必须先 `strip()` 再 `lower()`：此前只有 `lower()`，于是
    `'  review  '` 这类带空白（或换行、制表符）的任务类型匹配不到
    `DEFAULT_BUDGET`，**静默回落到 write**——review 本该给 episodic 更高权重
    （0.35 vs 0.30），一个前导空格就让配置失效且不报错。
    """
    key = str(task_type or "write").strip().lower() or "write"
    return dict(DEFAULT_BUDGET.get(key, DEFAULT_BUDGET["write"]))


def allocate_limits(max_items: int, task_type: str = "write") -> Dict[str, int]:
    """按任务类型分配 working/episodic/semantic 的条目预算。"""
    max_items = max(1, int(max_items or 1))
    budget = get_budget(task_type)
    wr = float(budget.get("working_ratio", 0.45))
    er = float(budget.get("episodic_ratio", 0.30))
    sr = float(budget.get("semantic_ratio", 0.25))

    total_ratio = wr + er + sr
    if total_ratio <= 0:
        wr, er, sr = 0.45, 0.30, 0.25
        total_ratio = 1.0
    wr, er, sr = wr / total_ratio, er / total_ratio, sr / total_ratio

    w = int(max_items * wr)
    e = int(max_items * er)
    s = int(max_items * sr)
    used = w + e + s

    # 把余数按语义层优先分配，保证总和等于 max_items。
    while used < max_items:
        if s <= w:
            s += 1
        elif w <= e:
            w += 1
        else:
            e += 1
        used += 1

    return {"working": w, "episodic": e, "semantic": s}
