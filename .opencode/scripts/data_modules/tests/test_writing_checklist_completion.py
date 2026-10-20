#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""写作检查清单的「完成」判定不能无条件为真。

`writing_guidance_builder.is_checklist_item_completed()` 决定每一项是否算
"已完成"，而这个判定直接进入 `context_manager._build_checklist_score()`：

    completed = is_checklist_item_completed(item, reader_signal)
    ...
    completion_rate = completed_items / total_items
    score = 100 * (0.5*weighted_rate + 0.3*required_rate + 0.2*completion_rate)

得分会 `_persist_writing_checklist_score()` 写进 `writing_checklist_scores`
表，并作为后续章节的基线（`get_writing_checklist_score_trend().score_avg`）。

## 缺陷

函数末尾有三处无条件 `return True`：

    if item_id == "genre_anchor_consistency":
        return True
    source = str(item.get("source") or "")
    if source.startswith("fallback"):
        return True
    if source.startswith("methodology."):
        # 方法论条目当前作为软提示，仅做观察与引导，不参与扣分。
        return True

`methodology.` 那处**有注释说明理由**（软提示、不参与扣分），`fallback` 和
`genre_anchor_consistency` 那两处**没有任何说明**——是不对称的，更像是遗漏。

实测后果（新书第一章，读者信号全空）：

    build_writing_checklist(reader_signal={}, min_items=5, max_items=6)
    → 3 项，全部 source=fallback
    → is_checklist_item_completed 全部返回 True
    → completion_rate = 100%
    → score = 100.0

也就是说：**一个刚开局、还没有任何读者数据、什么检查都无从验证的章节，
写作清单得分是满分 100**。这个分数还会落库成为后续章节的比较基线，
把整条趋势线抬到虚高位。

## 本文件钉住的语义

- `fallback` 项是**通用建议**（"开篇有冲突"、"段末留钩"），不是"已经做到
  的事"——无从验证时应当算**未完成**，由作者/agent 实际检查后确认。
- 有真实数据可判定的项（`hook_diversification` 等）保持原逻辑。
- `methodology.` 的"软提示不扣分"是**有意设计**（有注释），保持不变。
- `genre_anchor_consistency` 无条件为真同样没有依据，改为按信号判定。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.writing_guidance_builder import (  # noqa: E402
    build_writing_checklist,
    is_checklist_item_completed,
)


def _item(item_id: str, source: str = "writing_guidance") -> dict:
    return {"id": item_id, "source": source, "weight": 1.0, "required": False}


class TestFallbackIsNotAutoCompleted:
    """核心：通用建议项不能无条件算完成。"""

    def test_fallback_not_completed_without_signal(self):
        assert is_checklist_item_completed(_item("ending_hook", "fallback"), {}) is False, (
            "fallback 项在没有任何读者信号时被判为已完成——"
            "它是「通用建议」，不是「已做到的事」"
        )

    def test_all_fallback_items_not_completed(self):
        for item_id in ("opening_conflict", "scene_goal_block", "ending_hook"):
            assert is_checklist_item_completed(_item(item_id, "fallback"), {}) is False

    def test_fallback_source_with_prefix_still_not_completed(self):
        """`source.startswith("fallback")` 的其它写法同样不能豁免。"""
        assert is_checklist_item_completed(_item("x", "fallback.extra"), {}) is False


class TestEmptySignalDoesNotScore100:
    """端到端：新书第一章不该拿满分。"""

    def _score(self, checklist, reader_signal):
        total = len(checklist)
        done = sum(1 for i in checklist
                   if is_checklist_item_completed(i, reader_signal))
        rate = done / total if total else 1.0
        return 100 * (0.5 * rate + 0.3 * 1.0 + 0.2 * rate)

    def test_new_book_first_chapter_not_perfect(self):
        checklist = build_writing_checklist(
            guidance_items=[], reader_signal={}, genre_profile={},
            min_items=5, max_items=6, default_weight=1.0,
        )
        assert checklist, "该场景应生成 fallback 项"
        score = self._score(checklist, {})
        assert score < 100.0, (
            f"读者信号为空、检查项全是通用建议时，清单得分仍是 {score:.1f}。"
            f"这个分数会落库成为后续章节的比较基线，把趋势线整体抬高。"
        )


class TestRealSignalsStillWork:
    """有真实数据可判定的项保持原逻辑（别把功能改坏）。"""

    def test_hook_diversification_needs_two_types(self):
        item = _item("hook_diversification")
        assert is_checklist_item_completed(item, {"hook_type_usage": {"悬念": 3}}) is False
        assert is_checklist_item_completed(
            item, {"hook_type_usage": {"悬念": 3, "冲突": 2}}) is True

    def test_coolpoint_combo_needs_two_patterns(self):
        item = _item("coolpoint_combo")
        assert is_checklist_item_completed(item, {"pattern_usage": {"打脸": 2}}) is False
        assert is_checklist_item_completed(
            item, {"pattern_usage": {"打脸": 2, "装逼": 1}}) is True

    def test_low_score_range_uses_review_trend(self):
        item = _item("fix_low_score_range")
        assert is_checklist_item_completed(item, {"review_trend": {"overall_avg": 60}}) is False
        assert is_checklist_item_completed(item, {"review_trend": {"overall_avg": 80}}) is True

    def test_unknown_item_not_completed(self):
        assert is_checklist_item_completed(_item("some_new_thing"), {}) is False


class TestMethodologyStaysSoft:
    """`methodology.` 的无条件为真是**有意设计**（有注释），保持不变。"""

    def test_methodology_never_penalised(self):
        assert is_checklist_item_completed(_item("m1", "methodology.foo"), {}) is True

    def test_methodology_without_signal_still_true(self):
        assert is_checklist_item_completed(_item("m2", "methodology."), {}) is True


class TestGenreAnchorIsSignalBased:
    """`genre_anchor_consistency` 无条件为真同样没有依据。"""

    def test_not_completed_without_signal(self):
        assert is_checklist_item_completed(_item("genre_anchor_consistency"), {}) is False, (
            "题材锚点一致性在没有任何信号时被判为已完成——无从验证的事情"
            "不该算做到"
        )

    def test_completed_with_matching_signal(self):
        signal = {"genre_anchor": {"consistent": True}}
        assert is_checklist_item_completed(
            _item("genre_anchor_consistency"), signal) is True

    def test_not_completed_when_explicitly_inconsistent(self):
        signal = {"genre_anchor": {"consistent": False}}
        assert is_checklist_item_completed(
            _item("genre_anchor_consistency"), signal) is False
