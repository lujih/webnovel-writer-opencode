#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`TEMPLATE_WEIGHTS` 必须真的决定每段占多少位置（字符预算）。

审计发现：权重此前只当布尔用 —— `if weight > 0` 决定"这一段要不要进 payload"，
而 0.25 与 0.50 的段最终拿到**同样多**的内容；`max_chars` 完全没有引用，
全程没有任何体积限制。也就是说「token 预算」只是文档里的一个词。

修法：`context_section_char_budget`（默认 24000 字符）× 段权重 = 该段预算，
按预算裁剪（字符串截断、列表/字典裁条目），并把实际预算记进
`meta.section_char_budgets` 便于事后核对。
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


def _manager(tmp_path: Path, budget: int) -> ContextManager:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    cfg = DataModulesConfig.from_project_root(str(tmp_path))
    cfg.context_section_char_budget = budget
    return ContextManager(cfg)


class TestBudgetIsApplied:
    def test_short_content_untouched(self, tmp_path):
        manager = _manager(tmp_path, 24000)
        pack = {"meta": {"chapter": 3}, "core": "短内容", "scene": {}, "global": "全局"}
        payload = manager._assemble_json_payload(pack, template="plot")
        assert payload["core"] == "短内容"
        assert payload["global"] == "全局"
        # 预算被应用过 → 留下记录。**不要写死权重**：`_resolve_template_weights`
        # 会按章节阶段（early/mid/late）调整权重，第 3 章拿到的是 early 档。
        budgets = payload["meta"]["section_char_budgets"]
        assert budgets["core"] > 0
        assert budgets["core"] > budgets["global"], "core 权重必须高于 global"

    def test_long_string_is_truncated_with_marker(self, tmp_path):
        manager = _manager(tmp_path, 100)
        pack = {"meta": {"chapter": 3}, "core": "字" * 500}
        payload = manager._assemble_json_payload(pack, template="plot")
        assert len(payload["core"]) < 200
        assert manager._CLAMP_MARKER.strip() in payload["core"]
        # 截断点 = 预算（40 字符）
        assert payload["core"].startswith("字" * 40)

    def test_list_items_dropped_over_budget(self, tmp_path):
        manager = _manager(tmp_path, 100)  # scene 预算 = 35 字符
        pack = {
            "meta": {"chapter": 3},
            "scene": ["a" * 20, "b" * 20, "c" * 20],
        }
        payload = manager._assemble_json_payload(pack, template="plot")
        assert payload["scene"] == ["a" * 20], "超预算的条目必须被裁掉"

    def test_dict_values_dropped_over_budget(self, tmp_path):
        manager = _manager(tmp_path, 100)
        pack = {
            "meta": {"chapter": 3},
            "scene": {"small": "x", "big": "y" * 500},
        }
        payload = manager._assemble_json_payload(pack, template="plot")
        assert "small" in payload["scene"]
        assert "big" not in payload["scene"]

    def test_zero_budget_disables_clamping(self, tmp_path):
        """`0` = 关闭裁剪（旧行为），且不写 meta 键。"""
        manager = _manager(tmp_path, 0)
        pack = {"meta": {"chapter": 3}, "core": "长" * 5000}
        payload = manager._assemble_json_payload(pack, template="plot")
        assert len(payload["core"]) == 5000
        assert "section_char_budgets" not in payload["meta"]

    def test_weight_decides_relative_size(self, tmp_path):
        """权重差异必须体现在**实际体积**上——这是修复的核心语义。"""
        manager = _manager(tmp_path, 1000)
        pack = {
            "meta": {"chapter": 3},
            "core": "核" * 2000,
            "scene": "景" * 2000,
            "global": "界" * 2000,
        }
        payload = manager._assemble_json_payload(pack, template="plot")
        core, glob = payload["core"], payload["global"]
        budgets = payload["meta"]["section_char_budgets"]
        marker = len(manager._CLAMP_MARKER)
        # 每段都被裁到自己的预算上（预算 + 标记 = 上限）
        assert len(core) <= budgets["core"] + marker
        assert len(glob) <= budgets["global"] + marker
        # 权重顺序必须体现在预算与实际长度上（不写死具体系数：阶段权重会变）
        assert budgets["core"] > budgets["global"]
        assert len(core) > len(glob)
