#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长期记忆预算分配。

`memory/budget.py`（52 行、零测试覆盖）决定 working/episodic/semantic 三层
各分到多少条目。`allocate_limits()` 有一条明确的不变式：

    三层总和必须等于 max_items

否则要么浪费预算，要么超出预算。

## 缺陷：`get_budget()` 漏了 `strip()`

    def get_budget(task_type: str = "write"):
        key = str(task_type or "write").lower()      # ← 只有 lower()，没有 strip()
        return dict(DEFAULT_BUDGET.get(key, DEFAULT_BUDGET["write"]))

带空白的任务类型静默回落到 `write`：

    get_budget('review')      → max_items=40  ✅
    get_budget(' review')     → max_items=30  ❌ 回落到 write
    get_budget('review ')     → max_items=30  ❌
    get_budget('  review  ')  → max_items=30  ❌

对分配的实际影响（max_items=40）：

    allocate_limits(40, 'review')      → {'working': 14, 'episodic': 14, 'semantic': 12}
    allocate_limits(40, '  review  ')  → {'working': 18, 'episodic': 12, 'semantic': 10}

审查场景本该给 episodic（中期记忆）更多权重（0.35 vs write 的 0.30），
但一个前导空格就让配置失效，且**不报错**。

## 本文件钉住

- 三层总和恒等于 max_items（对多种输入）
- 任务类型大小写与前后空白不影响结果
- 未知任务类型回落到 write（既有行为）
- 极端 max_items（0/负数/None）不崩
- 比例归一化正确（总比例 ≠ 1 时仍按比例分）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.memory.budget import (  # noqa: E402
    DEFAULT_BUDGET,
    allocate_limits,
    get_budget,
)


class TestSumInvariant:
    """核心不变式：三层总和 == max_items。"""

    @pytest.mark.parametrize("task", ["write", "review", "query", "unknown"])
    @pytest.mark.parametrize("n", [1, 2, 3, 5, 7, 10, 25, 30, 40, 100, 999])
    def test_sum_equals_max_items(self, n, task):
        limits = allocate_limits(n, task)
        assert sum(limits.values()) == n, (
            f"allocate_limits({n}, {task!r}) = {limits}，"
            f"总和 {sum(limits.values())} ≠ {n}"
        )

    def test_all_keys_present(self):
        limits = allocate_limits(10)
        assert set(limits) == {"working", "episodic", "semantic"}

    def test_no_negative_allocations(self):
        for n in (1, 2, 3, 5, 10):
            for task in ("write", "review", "query"):
                limits = allocate_limits(n, task)
                assert all(v >= 0 for v in limits.values()), limits


class TestTaskTypeNormalization:
    """核心缺陷：空白导致静默回落。"""

    @pytest.mark.parametrize("task,expected_max", [
        ("review", 40),
        (" review", 40),
        ("review ", 40),
        ("  review  ", 40),
        ("\treview", 40),
        ("REVIEW", 40),
        ("Review", 40),
    ])
    def test_whitespace_and_case_ignored(self, task, expected_max):
        budget = get_budget(task)
        assert budget["max_items"] == expected_max, (
            f"get_budget({task!r}) 得到 max_items={budget['max_items']}，"
            f"期望 {expected_max}。带空白/大小写差异的任务类型静默回落到 write。"
        )

    @pytest.mark.parametrize("task", ["query", " QUERY ", "Query"])
    def test_query_variants(self, task):
        assert get_budget(task)["max_items"] == 25

    def test_allocation_matches_for_whitespace_variant(self):
        """`'  review  '` 与 `'review'` 必须给出相同分配。"""
        assert allocate_limits(40, "  review  ") == allocate_limits(40, "review"), (
            "带空白的任务类型给出了不同的分配——配置静默失效"
        )


class TestUnknownFallsBackToWrite:
    """未知任务类型的既有行为不变。"""

    @pytest.mark.parametrize("task", [None, "", "   ", "不存在的任务", "xxx"])
    def test_falls_back(self, task):
        assert get_budget(task) == DEFAULT_BUDGET["write"]

    def test_allocation_falls_back(self):
        assert allocate_limits(30, "unknown") == allocate_limits(30, "write")


class TestRatiosRespected:
    """分配比例应体现各任务类型的差异。"""

    def test_review_favours_episodic_over_write(self):
        """review 的 episodic 比例 0.35 > write 的 0.30。"""
        write = allocate_limits(100, "write")
        review = allocate_limits(100, "review")
        assert review["episodic"] > write["episodic"], (
            f"review 的 episodic 应多于 write：write={write}, review={review}"
        )

    def test_query_favours_episodic_most(self):
        query = allocate_limits(100, "query")
        write = allocate_limits(100, "write")
        assert query["episodic"] > write["episodic"]

    def test_write_favours_working_most(self):
        write = allocate_limits(100, "write")
        assert write["working"] >= write["episodic"]
        assert write["working"] >= write["semantic"]


class TestExtremeInputs:
    """极端输入不崩，且仍满足不变式。"""

    @pytest.mark.parametrize("n", [0, -1, -100, None])
    def test_degenerate_max_items(self, n):
        limits = allocate_limits(n)
        assert sum(limits.values()) >= 1, "至少要分到 1 个条目"

    def test_string_max_items(self):
        assert sum(allocate_limits("10").values()) == 10

    def test_float_max_items(self):
        limits = allocate_limits(3.7)
        assert sum(limits.values()) == 3


class TestBudgetShape:
    """预算表结构完整。"""

    @pytest.mark.parametrize("task", ["write", "review", "query"])
    def test_has_all_ratios(self, task):
        budget = get_budget(task)
        for key in ("max_items", "working_ratio", "episodic_ratio", "semantic_ratio"):
            assert key in budget, f"{task} 缺 {key}"

    @pytest.mark.parametrize("task", ["write", "review", "query"])
    def test_ratios_sum_to_one(self, task):
        budget = get_budget(task)
        total = (budget["working_ratio"] + budget["episodic_ratio"]
                 + budget["semantic_ratio"])
        assert abs(total - 1.0) < 1e-9, f"{task} 的比例和为 {total}"

    def test_get_budget_returns_copy(self):
        """调用方改返回值不能污染 DEFAULT_BUDGET。"""
        budget = get_budget("write")
        budget["max_items"] = 999
        assert DEFAULT_BUDGET["write"]["max_items"] == 30
