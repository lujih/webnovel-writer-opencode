#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for state_projection_renderer."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from data_modules.state_projection_renderer import (
    _render_world_state,
    _render_foreshadowing_panel,
    _render_character_matrix,
    _render_power_system,
    _render_chapter_index,
    render_all_projections,
)

MINIMAL_STATE = {
    "schema_version": "5.1",
    "progress": {"chapter_status": {"1": {"status": "committed"}}},
    "entities_v3": {
        "xiaoyan": {
            "name": "萧炎",
            "entity_type": "角色",
            "current_state": {"realm": "斗王", "emotion": "愤怒"},
        }
    },
    "foreshadowing": [
        {"content": "三年之约", "planted_chapter": 1, "urgency": 90, "status": "active"},
        {"content": "获取青莲地心火", "planted_chapter": 3, "urgency": 60, "status": "closed", "closed_chapter": 5},
    ],
    "plot_threads": {
        "foreshadowing": [
            {"content": "悬念甲", "status": "active", "planted_chapter": 2, "tier": "核心", "target_chapter": 10},
            {"content": "悬念乙", "status": "resolved", "planted_chapter": 1, "resolved_chapter": 4},
        ],
    },
    "relationships": [
        {"from": "xiaoyan", "to": "yunlanzong", "type": "敌对", "last_seen_chapter": 5},
    ],
    "protagonist_state": {"entity_id": "xiaoyan", "realm": "斗王", "location": {"current": "云岚宗"}},
    "world_rules": [
        {"rule_id": "rule_1", "description": "云岚宗禁地不可飞行", "status": "active", "revealed_chapter": 5},
        {"rule_id": "rule_2", "description": "斗王境界可短暂滞空", "status": "active", "revealed_chapter": 6},
    ],
    "reader_promises": [],
    "artifacts": [],
    "override_rules": [],
}


class TestWorldState:
    def test_renders_protagonist(self):
        result = _render_world_state(MINIMAL_STATE, Path("."))
        assert "萧炎" in result
        assert "斗王" in result

    def test_renders_world_rules(self):
        result = _render_world_state(MINIMAL_STATE, Path("."))
        assert "禁地不可飞行" in result

    def test_empty_state_no_crash(self):
        result = _render_world_state({}, Path("."))
        assert "暂无数据" in result


class TestForeshadowingPanel:
    def test_renders_active_and_closed(self):
        result = _render_foreshadowing_panel(MINIMAL_STATE, Path("."))
        # 数据源为 plot_threads.foreshadowing（SSOT writer 聚合路径）
        assert "悬念甲" in result
        assert "悬念乙" in result
        assert "活跃伏笔" in result
        assert "已闭合伏笔" in result

    def test_prefer_nested_plot_threads_over_legacy_top_level(self):
        result = _render_foreshadowing_panel(MINIMAL_STATE, Path("."))
        assert "三年之约" not in result  # 顶层旧占位被忽略

    def test_legacy_top_level_fallback_when_no_plot_threads(self):
        state = dict(MINIMAL_STATE)
        state.pop("plot_threads")
        result = _render_foreshadowing_panel(state, Path("."))
        assert "三年之约" in result
        assert "青莲地心火" in result

    def test_empty_foreshadowing_no_crash(self):
        result = _render_foreshadowing_panel({}, Path("."))
        assert "暂无活跃伏笔" in result


class TestCharacterMatrix:
    def test_renders_relationship_table(self):
        result = _render_character_matrix(MINIMAL_STATE, Path("."))
        assert "萧炎" in result
        assert "敌对" in result

    def test_empty_relationships_no_crash(self):
        result = _render_character_matrix({}, Path("."))
        assert "暂无关系数据" in result

    def test_dict_relationships_no_crash(self):
        """Test that dict-format relationships (from state.json) don't crash."""
        state = {
            "relationships": {
                "xiaoyan-yunlanzong": {"type": "敌对", "chapter": 5},
                "xiaoyan-xunillian": {"type": "师徒", "chapter": 1},
            },
            "entities_v3": {
                "xiaoyan": {"name": "萧炎", "entity_type": "角色"},
                "yunlanzong": {"name": "云岚宗", "entity_type": "势力"},
            },
        }
        result = _render_character_matrix(state, Path("."))
        assert "萧炎" in result
        assert "敌对" in result
        assert "师徒" in result


class TestPowerSystem:
    def test_renders_realms(self):
        result = _render_power_system(MINIMAL_STATE, Path("."))
        assert "斗王" in result

    def test_empty_no_crash(self):
        result = _render_power_system({}, Path("."))
        assert "暂无境界数据" in result


class TestChapterIndex:
    def test_renders_chapter_table(self):
        result = _render_chapter_index(MINIMAL_STATE, Path("."))
        assert "第1章" in result
        assert "committed" in result

    def test_empty_no_crash(self):
        result = _render_chapter_index({}, Path("."))
        assert "暂无章节记录" in result


class TestRenderAll:
    def test_renders_all_files(self, tmp_path):
        webnovel = tmp_path / ".webnovel"
        webnovel.mkdir()
        (webnovel / "state.json").write_text(json.dumps(MINIMAL_STATE), encoding="utf-8")
        results = render_all_projections(tmp_path)
        assert len(results) == 5
        for path in results.values():
            assert path.is_file()
            content = path.read_text(encoding="utf-8")
            assert "请勿手动编辑" in content

    def test_missing_state_json(self, tmp_path):
        results = render_all_projections(tmp_path)
        assert results == {}
