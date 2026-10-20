#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""质量趋势报表里「required 完成率」在无 required 项时不该显示 100%。

`quality_trend_report._build_checklist_rows()`：

    required_items = _to_int(row.get("required_items"))
    completed_required = _to_int(row.get("completed_required"))
    if required_items > 0:
        required_rate = completed_required / required_items
    else:
        required_rate = 1.0          # ← 没有 required 项 → 报 100%

这与刚修的评分公式（`e44f1bb`）是同一缺陷：把「没有依据」当成「全部达成」。

实测（新书场景，`required_items=0`）：

    | 1 | 30.0 | 0.0% | 100.0% |
    | 2 | 50.0 | 50.0% | 100.0% |

「完成率 0%」旁边并排着「required 完成率 100%」——自相矛盾。而且新书场景
**永远**是这一列 100%（`build_writing_checklist(reader_signal={})` 产出的
全是 fallback 项，没有 required）。

报表是给作者看的。这一列恒为 100% 会让作者以为必做项都做到了，实际系统里
根本没有必做项——那是"没有约束"，不是"约束已满足"。

## 本文件钉住

- `required_items == 0` 时，required 列显示 **—（无数据）**，而不是 100%
- `required_items > 0` 时正常计算百分比
- 空记录列表的占位行不变
- 其他列的格式不变
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from quality_trend_report import _build_checklist_rows, _percent  # noqa: E402


def _row(**kw):
    base = {"chapter": 1, "score": 50.0, "completion_rate": 0.5,
            "required_items": 0, "completed_required": 0}
    base.update(kw)
    return base


class TestNoRequiredItems:
    """核心缺陷：无 required 项时不能显示 100%。"""

    def test_zero_required_not_100_percent(self):
        rows = _build_checklist_rows([_row(required_items=0, completed_required=0)])
        assert len(rows) == 1
        assert "100.0%" not in rows[0], (
            f"没有 required 项，required 列却显示 100%：{rows[0]}。"
            f"「没有必做项」不是「必做项已满足」。"
        )

    def test_zero_required_shows_placeholder(self):
        rows = _build_checklist_rows([_row(required_items=0)])
        assert "—" in rows[0] or "-" in rows[0].split("|")[-2], (
            f"应显示占位符（无数据），实际：{rows[0]}"
        )

    def test_mixed_records(self):
        """有的章有 required、有的没有——两种都要正确处理。"""
        records = [
            _row(chapter=1, required_items=0, completed_required=0),
            _row(chapter=2, required_items=2, completed_required=1),
            _row(chapter=3, required_items=4, completed_required=4),
        ]
        rows = _build_checklist_rows(records)
        assert len(rows) == 3
        # 第 1 章无 required → 不该是 100%
        assert "100.0%" not in rows[0]
        # 第 2 章 1/2 = 50%
        assert "50.0%" in rows[1]
        # 第 3 章 4/4 = 100%
        assert "100.0%" in rows[2]


class TestWithRequiredItemsUnchanged:
    """有 required 项时行为不变。"""

    @pytest.mark.parametrize("required,completed,expected", [
        (2, 1, "50.0%"),
        (4, 4, "100.0%"),
        (3, 0, "0.0%"),
        (1, 1, "100.0%"),
        (10, 3, "30.0%"),
    ])
    def test_rates(self, required, completed, expected):
        rows = _build_checklist_rows([_row(required_items=required,
                                           completed_required=completed)])
        assert expected in rows[0], f"{completed}/{required} 应为 {expected}：{rows[0]}"


class TestEmptyRecords:
    """空列表占位行不变。"""

    def test_placeholder(self):
        assert _build_checklist_rows([]) == ["| - | - | - | - |"]


class TestOtherColumnsUnchanged:
    """其他列格式不变。"""

    def test_chapter_and_score_and_completion(self):
        rows = _build_checklist_rows([
            _row(chapter=7, score=88.5, completion_rate=0.25,
                 required_items=1, completed_required=1)])
        cells = [c.strip() for c in rows[0].strip("|").split("|")]
        assert cells[0] == "7"
        assert cells[1] == "88.5"
        assert cells[2] == "25.0%"

    def test_percent_helper(self):
        assert _percent(0.0) == "0.0%"
        assert _percent(0.5) == "50.0%"
        assert _percent(1.0) == "100.0%"


class TestDirtyInputs:
    """脏输入不该崩。"""

    @pytest.mark.parametrize("value", [None, "", "abc", [], {}])
    def test_dirty_required_items(self, value):
        try:
            rows = _build_checklist_rows([_row(required_items=value)])
        except Exception as exc:
            pytest.fail(f"required_items={value!r} 让报表抛了 "
                        f"{type(exc).__name__}: {exc}")
        assert len(rows) == 1

    @pytest.mark.parametrize("value", [None, "", "abc"])
    def test_dirty_completed(self, value):
        try:
            _build_checklist_rows([_row(required_items=2, completed_required=value)])
        except Exception as exc:
            pytest.fail(f"completed_required={value!r} 抛了 {type(exc).__name__}: {exc}")

    def test_completed_exceeds_required(self):
        """完成数大于总数（脏数据）不该产出 >100% 的荒谬值。"""
        rows = _build_checklist_rows([_row(required_items=2, completed_required=5)])
        assert "250.0%" not in rows[0], f"产出超过 100%：{rows[0]}"
