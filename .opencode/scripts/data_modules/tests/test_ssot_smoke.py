"""Smoke tests for SSOT enforcer event log."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest


class TestSSOTEventLog:
    def test_publish_and_read_events(self, tmp_path):
        from data_modules.ssot_enforcer import publish_event, read_events

        publish_event(tmp_path, "test_event", {"key": "value"}, chapter=1)
        events = read_events(tmp_path)
        assert len(events) == 1
        assert events[0]["event_type"] == "test_event"
        assert events[0]["chapter"] == 1
        assert events[0]["payload"]["key"] == "value"

    def test_publish_multiple_events_sequential_seq(self, tmp_path):
        from data_modules.ssot_enforcer import publish_event, read_events

        publish_event(tmp_path, "event_a", {}, chapter=1)
        publish_event(tmp_path, "event_b", {}, chapter=2)
        events = read_events(tmp_path)
        assert len(events) == 2
        assert events[0]["seq"] == 1
        assert events[1]["seq"] == 2

    def test_read_events_filter_by_type(self, tmp_path):
        from data_modules.ssot_enforcer import publish_event, read_events

        publish_event(tmp_path, "type_a", {}, chapter=1)
        publish_event(tmp_path, "type_b", {}, chapter=2)

        a_events = read_events(tmp_path, event_type="type_a")
        assert len(a_events) == 1
        assert a_events[0]["event_type"] == "type_a"

    def test_read_events_filter_by_chapter(self, tmp_path):
        from data_modules.ssot_enforcer import publish_event, read_events

        publish_event(tmp_path, "event", {}, chapter=1)
        publish_event(tmp_path, "event", {}, chapter=5)

        ch1 = read_events(tmp_path, chapter=1)
        assert len(ch1) == 1
        assert ch1[0]["chapter"] == 1

    def test_rebuild_state_json_from_events(self, tmp_path):
        from data_modules.ssot_enforcer import publish_event, rebuild_state_json

        publish_event(tmp_path, "chapter_status_changed",
                      {"status": "committed"}, chapter=1)
        publish_event(tmp_path, "chapter_status_changed",
                      {"status": "committed"}, chapter=2)
        publish_event(tmp_path, "entity_created",
                      {"entity_id": "萧炎", "entity_type": "角色", "entity_name": "萧炎"},
                      chapter=1)

        state = rebuild_state_json(tmp_path)
        ch_status = state["progress"]["chapter_status"]
        assert "1" in ch_status
        assert "2" in ch_status
        assert state["progress"]["current_chapter"] == 2
        assert "萧炎" in state["entities_v3"]

    def test_rebuild_populates_plot_threads_foreshadowing_from_open_loop_events(self, tmp_path):
        """open_loop 事件必须同时聚合进 plot_threads.foreshadowing（与
        StateProjectionWriter 增量路径同构），否则 ssot rebuild 会冲掉
        dashboard 消费的 plot_threads.foreshadowing。"""
        from data_modules.ssot_enforcer import publish_event, rebuild_state_json

        publish_event(
            tmp_path, "open_loop_created",
            {"content": "黑色棺材的来历", "tier": "major",
             "target_chapter": 30, "_subject": "narrator"},
            chapter=7,
        )
        publish_event(
            tmp_path, "open_loop_closed",
            {"content": "黑色棺材的来历", "resolution": "揭棺"},
            chapter=28,
        )

        state = rebuild_state_json(tmp_path)
        rows = state.get("plot_threads", {}).get("foreshadowing")
        assert isinstance(rows, list), "ssot rebuild 必须产出 plot_threads.foreshadowing"
        assert len(rows) == 1
        assert rows[0]["content"] == "黑色棺材的来历"
        assert rows[0]["status"] == "resolved"
        assert rows[0]["planted_chapter"] == 7
        assert rows[0]["resolved_chapter"] == 28
        # 顶层 foreshadowing 旧路径仍保留（向后兼容）
        assert len(state.get("foreshadowing", [])) == 1

    def test_rebuild_open_loop_content_matches_coerce_rule(self, tmp_path):
        """结构化事件（loop_type + description，无 content）必须提取出
        与 memory 侧 _coerce_loop_content 相同的 content 字符串。"""
        from data_modules.ssot_enforcer import publish_event, rebuild_state_json, _loop_content

        payload = {"loop_type": "信息悬疑", "description": "芯片设计者身份",
                   "_subject": "chen_sheng"}
        publish_event(tmp_path, "open_loop_created", payload, chapter=3)

        state = rebuild_state_json(tmp_path)
        rows = state.get("plot_threads", {}).get("foreshadowing", [])
        assert rows and rows[0]["content"] == "信息悬疑：芯片设计者身份"
        assert _loop_content(payload) == "信息悬疑：芯片设计者身份"

    def test_rebuild_open_loop_orphan_close_keeps_record(self, tmp_path):
        """孤儿 closed 事件（无对应 created）保留为 resolved 记录，不丢数据。"""
        from data_modules.ssot_enforcer import publish_event, rebuild_state_json

        publish_event(
            tmp_path, "open_loop_closed",
            {"content": "从未登记过的旧约"},
            chapter=9,
        )

        rows = rebuild_state_json(tmp_path).get("plot_threads", {}).get("foreshadowing", [])
        assert len(rows) == 1
        assert rows[0]["status"] == "resolved"
        assert rows[0]["resolved_chapter"] == 9

    def test_verify_consistency_clean(self, tmp_path):
        """rebuild 产物 + P0 合并后的 state.json 通过 verify（真实 CLI：先 rebuild 再 verify）。"""
        from data_modules.ssot_enforcer import publish_event, rebuild_projections, verify_consistency

        publish_event(tmp_path, "chapter_status_changed",
                      {"status": "committed"}, chapter=1)
        publish_event(tmp_path, "chapter_status_changed",
                      {"status": "committed"}, chapter=2)
        rebuild_projections(tmp_path)
        result = verify_consistency(tmp_path)
        assert all(d["severity"] == "info" for d in result), result

    def test_verify_detects_chapter_status_gap(self, tmp_path):
        """事件日志有而 state 缺的章节（缺口）= 真漂移，必须报 warning。

        注：rebuild_state_json 只消费 chapter_status_changed（14 种事件类型
        之一），chapter_committed 是 chapter_commit_service 发布的别名事件，
        由事件日志的 chapter_status_changed 路径消费。"""
        from data_modules.ssot_enforcer import publish_event, verify_consistency

        publish_event(tmp_path, "chapter_status_changed",
                      {"status": "committed"}, chapter=7)
        (tmp_path / ".webnovel").mkdir(exist_ok=True)
        (tmp_path / ".webnovel" / "state.json").write_text(
            json.dumps({
                "progress": {"current_chapter": 1,
                             "chapter_status": {"1": "chapter_committed"}},
            }, ensure_ascii=False), encoding="utf-8")
        drifts = verify_consistency(tmp_path)
        assert "progress.chapter_status" in [d.get("field") or "" for d in drifts], drifts

    def test_verify_state_superset_is_not_drift(self, tmp_path):
        """state 超集（增量 chapter-commit 累积 + P0 合并保留章节）不是缺口，不报 drift。"""
        from data_modules.ssot_enforcer import publish_event, verify_consistency

        publish_event(tmp_path, "chapter_status_changed",
                      {"status": "committed"}, chapter=1)
        (tmp_path / ".webnovel").mkdir(exist_ok=True)
        (tmp_path / ".webnovel" / "state.json").write_text(
            json.dumps({
                "progress": {"current_chapter": 3,
                             "chapter_status": {
                                 "1": "chapter_committed",
                                 "2": "chapter_committed",
                                 "3": "chapter_committed",
                             }},
            }, ensure_ascii=False), encoding="utf-8")
        drifts = verify_consistency(tmp_path)
        assert "progress.chapter_status" not in [d.get("field") or "" for d in drifts], drifts
