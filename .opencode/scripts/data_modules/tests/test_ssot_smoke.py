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

    def test_publish_event_is_serialized_by_filelock(self, tmp_path):
        """P0：并发 publish 不得争抢同一 seq（.events.lock 串行化）。

        模拟 8 线程并发 publish，断言 8 个事件 seq 互不重复且全部落盘。
        """
        import threading
        from data_modules.ssot_enforcer import publish_event, read_events

        n = 8
        errors = []

        def worker(i):
            try:
                publish_event(tmp_path, f"evt_{i}", {"i": i}, chapter=i + 1)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, errors
        events = read_events(tmp_path)
        assert len(events) == n, f"concurrent publish lost events: {len(events)}/{n}"
        seqs = [e["seq"] for e in events]
        assert len(set(seqs)) == n, f"seq collision: {seqs}"

    def test_publish_event_cleans_orphan_tmp(self, tmp_path):
        """P0/P2：publish 前清理崩溃遗留的 .tmp.* 事件文件（seq 不复用）。"""
        from data_modules.ssot_enforcer import _event_log_dir, publish_event, read_events

        log_dir = _event_log_dir(tmp_path)
        log_dir.mkdir(parents=True, exist_ok=True)
        # 模拟崩溃遗留：tmp 文件对应 seq 3，但 000003.event.json 不存在
        (log_dir / ".tmp.000003.99999").write_text("{}", encoding="utf-8")

        publish_event(tmp_path, "event_a", {}, chapter=1)
        publish_event(tmp_path, "event_b", {}, chapter=2)

        # 孤儿 tmp 已被清理
        assert not list(log_dir.glob(".tmp.*")), "orphan .tmp not cleaned"
        # 新事件 seq 从 1 开始，不复用孤儿 tmp 的 seq 3
        events = read_events(tmp_path)
        assert [e["seq"] for e in events] == [1, 2]

    def test_read_events_logs_corrupt_event_files(self, tmp_path):
        """P1：坏事件文件（半写 JSON）被跳过且打 error 日志，不静默。"""
        import logging
        from data_modules.ssot_enforcer import publish_event, read_events, _event_log_dir

        publish_event(tmp_path, "good_event", {}, chapter=1)
        log_dir = _event_log_dir(tmp_path)
        # 制造一个 seq 4 的坏事件文件（模拟崩溃半写）
        (log_dir / "000004.event.json").write_text('{"broken": ', encoding="utf-8")

        with logging_capture() as records:
            events = read_events(tmp_path)
        assert len(events) == 1  # 好事件保留
        assert any("解析" in r.message or "corrupt" in r.message.lower() or r.levelno >= logging.ERROR
                   for r in records if "ssot" in r.getMessage().lower()), \
            f"corrupt event file not logged: {[r.getMessage() for r in records]}"

    def test_rebuild_chapter_status_uses_string_shape(self, tmp_path):
        """P1：rebuild 产出的 chapter_status 值必须是字符串（与增量
        writer 同形状），并维护 progress.chapter_status_seq 事件序。

        legacy dict 形状 {"status":...} 会让 doctor / _project_total_words
        的 `v == "chapter_committed"` 比较全部失效。
        """
        from data_modules.ssot_enforcer import publish_event, rebuild_state_json

        publish_event(tmp_path, "chapter_status_changed",
                      {"status": "committed"}, chapter=1)
        publish_event(tmp_path, "chapter_status_changed",
                      {"status": "rejected"}, chapter=2)

        state = rebuild_state_json(tmp_path)
        cs = state["progress"]["chapter_status"]
        assert cs["1"] == "chapter_committed", f"shape must be str, got {cs['1']!r}"
        assert cs["2"] == "chapter_rejected", f"shape must be str, got {cs['2']!r}"
        seqs = state["progress"]["chapter_status_seq"]
        assert seqs["1"] == 1 and seqs["2"] == 2

    def test_verify_flags_legacy_dict_chapter_status(self, tmp_path):
        """P1：state.json 残留 legacy dict 形状 chapter_status 时，verify
        必须报值形状不一致（缺口/超集语义之外的新增防护）。"""
        from data_modules.ssot_enforcer import publish_event, verify_consistency

        publish_event(tmp_path, "chapter_status_changed",
                      {"status": "committed"}, chapter=1)
        (tmp_path / ".webnovel").mkdir(exist_ok=True)
        # legacy dict 形状（旧版 rebuild 产物）
        (tmp_path / ".webnovel" / "state.json").write_text(
            json.dumps({"progress": {
                "current_chapter": 1,
                "chapter_status": {"1": {"status": "committed", "last_event_seq": 1}},
            }}, ensure_ascii=False), encoding="utf-8")
        drifts = verify_consistency(tmp_path)
        assert any(d.get("field") == "progress.chapter_status"
                   and "legacy dict 形状" in d.get("detail", "") for d in drifts), drifts


# 轻量 logging 捕获（避免引入 caplog fixture 依赖）
import logging as _logging
from contextlib import contextmanager


@contextmanager
def logging_capture():
    records = []

    class _Handler(_logging.Handler):
        def emit(self, record):
            records.append(record)

    h = _Handler()
    logger = _logging.getLogger("data_modules.ssot_enforcer")
    old_level = logger.level
    logger.setLevel(_logging.DEBUG)
    logger.addHandler(h)
    try:
        yield records
    finally:
        logger.removeHandler(h)
        logger.setLevel(old_level)
