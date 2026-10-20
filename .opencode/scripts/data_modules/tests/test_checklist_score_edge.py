#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""写作清单评分在「空清单」与「无 required 项」时不该虚高。

`context_manager._compute_writing_checklist_score()` 的公式：

    completion_rate = (completed_items / total_items) if total_items > 0 else 1.0
    weighted_rate   = (completed_weight / total_weight) if total_weight > 0 else completion_rate
    required_rate   = (completed_required / required_items) if required_items > 0 else 1.0
    score = 100 * (0.5*weighted_rate + 0.3*required_rate + 0.2*completion_rate)

两处 `else 1.0` 都是「没有可检查的东西 → 当作全做到了」。

## 缺陷一：空清单得 100 分（实测）

    cm._compute_writing_checklist_score(chapter=1, checklist=[], reader_signal={})
    → score=100.0, total_items=0

什么都没检查，却是满分。

## 缺陷二：无 required 项时那 0.3 权重恒为满分（实测）

    checklist = [2 个 required=False 的项，一个都没完成]
    → score=30.0, required_completion_rate=1.0

30 分全部来自「0 个 required 项全部完成」。新书场景实测 `required_items=0`
（`build_writing_checklist(reader_signal={})` 产出 3 项全是 fallback），
也就是说**评分里有 30% 的权重对新书永远不起作用**——同样完成度下，新书与
老书的分数不可比。

## 修复取向

「没有 required 项」不等于「required 项全做到了」。把该权重按比例分给其余
两项，而不是白送满分。空清单则直接给 0（没有依据就不给分）。

## 本文件钉住

- 空清单不得得 100 分
- 无 required 项时不得因「0 个 required 全部完成」而虚高
- 有 required 项时行为不变（回归保护）
- 分数始终在 [0, 100] 内
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.context_manager import ContextManager  # noqa: E402


@pytest.fixture
def cm(tmp_path: Path) -> ContextManager:
    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    return ContextManager(cfg)


def _items(*specs):
    """`_items(("a", False), ("b", True))` → checklist。"""
    return [{"id": i, "weight": 1.0, "required": r, "source": "test",
             "label": i} for i, r in specs]


class TestEmptyChecklist:
    """核心缺陷一：空清单不得满分。"""

    def test_empty_is_not_100(self, cm):
        result = cm._compute_writing_checklist_score(
            chapter=1, checklist=[], reader_signal={})
        assert result["score"] < 100.0, (
            f"空清单（total_items=0）得到 {result['score']} 分。"
            f"什么都没检查却是满分——没有任何依据给出这个分数。"
        )

    def test_empty_is_zero(self, cm):
        result = cm._compute_writing_checklist_score(
            chapter=1, checklist=[], reader_signal={})
        assert result["score"] == pytest.approx(0.0), (
            f"空清单应给 0 分，实际 {result['score']}"
        )

    def test_empty_reports_zero_items(self, cm):
        result = cm._compute_writing_checklist_score(
            chapter=1, checklist=[], reader_signal={})
        assert result["total_items"] == 0


class TestNoRequiredItems:
    """核心缺陷二：无 required 项时 0.3 权重不该白送。"""

    def test_nothing_completed_is_zero(self, cm):
        result = cm._compute_writing_checklist_score(
            chapter=1,
            checklist=_items(("a", False), ("b", False), ("c", False)),
            reader_signal={},
        )
        assert result["score"] == pytest.approx(0.0), (
            f"三项全部未完成，却得到 {result['score']} 分。"
            f"其中 30 分来自「0 个 required 项全部完成」——"
            f"required_completion_rate={result['required_completion_rate']}。"
            f"新书场景 required_items=0，意味着 30% 的权重永远白送。"
        )

    def test_required_rate_not_one(self, cm):
        result = cm._compute_writing_checklist_score(
            chapter=1,
            checklist=_items(("a", False), ("b", False)),
            reader_signal={},
        )
        assert result["required_completion_rate"] != 1.0, (
            "无 required 项时 required_completion_rate 被报成 1.0（全部完成）——"
            "但根本没有 required 项可完成"
        )


class TestWithRequiredItemsUnchanged:
    """有 required 项时的既有语义不能改。"""

    def test_nothing_completed(self, cm):
        result = cm._compute_writing_checklist_score(
            chapter=1,
            checklist=_items(("r", True), ("a", False)),
            reader_signal={},
        )
        assert result["score"] == pytest.approx(0.0)

    def test_required_items_counted(self, cm):
        result = cm._compute_writing_checklist_score(
            chapter=1,
            checklist=_items(("r", True), ("a", False)),
            reader_signal={},
        )
        assert result["required_items"] == 1

    def test_structure_intact(self, cm):
        result = cm._compute_writing_checklist_score(
            chapter=1,
            checklist=_items(("r", True), ("a", False)),
            reader_signal={},
        )
        for key in ("score", "completion_rate", "weighted_completion_rate",
                    "required_completion_rate", "total_items", "required_items",
                    "completed_items", "completed_required", "total_weight",
                    "completed_weight", "pending_items"):
            assert key in result, f"返回结构缺 {key}"


class TestScoreBounds:
    """分数始终在 [0, 100]。"""

    @pytest.mark.parametrize("checklist", [
        [],
        _items(("a", False)),
        _items(("a", True)),
        _items(*[(f"x{i}", i % 2 == 0) for i in range(10)]),
    ])
    def test_in_range(self, cm, checklist):
        result = cm._compute_writing_checklist_score(
            chapter=1, checklist=checklist, reader_signal={})
        assert 0.0 <= result["score"] <= 100.0

    def test_all_completed_with_required_is_100(self, cm):
        """全部完成且有 required 项时仍应接近满分。

        注意：`is_checklist_item_completed` 对未知 source 返回 False，
        所以这里构造 source 让判定可控——用 methodology（恒为 True）。
        """
        checklist = [
            {"id": "m1", "weight": 1.0, "required": True, "source": "methodology.x"},
            {"id": "m2", "weight": 1.0, "required": False, "source": "methodology.x"},
        ]
        result = cm._compute_writing_checklist_score(
            chapter=1, checklist=checklist, reader_signal={})
        assert result["score"] == pytest.approx(100.0), (
            f"全部完成（methodology 源恒判完成）却是 {result['score']} 分"
        )
