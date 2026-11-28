#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canon locks：作者钉死的设定 + 提交时的确定性冲突检测。

外部依据：NarraLume 把 "locked fact" 当作硬约束——AI 的建议与锁定设定冲突时
暂停任务等作者裁决，而不是静默改 canon。本项目落成更硬的等价位：
冲突即拒章（技术故障），因为 lock 是作者显式声明、可机判、零误判空间。

本文件钉住：
1. 增删改的幂等与覆盖语义（一个 (subject, field) 只允许一条锁）；
2. 锁定文件损坏时**写入中止**，不拿空集合覆盖作者硬约束；
3. 冲突检测只认精确相等（不做模糊匹配），且只覆盖两类有字段语义的事件；
4. `build_commit` 集成：冲突 → rejected + `canon_conflicts` 明细。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.canon_locks import (  # noqa: E402
    CanonLocksCorrupted,
    add_lock,
    detect_conflicts,
    format_conflicts,
    load_locks,
    lock_id,
    locks_path,
    remove_lock,
)
from data_modules.chapter_commit_service import ChapterCommitService  # noqa: E402


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"schema_version": "5.1", "progress": {}}), encoding="utf-8")
    return tmp_path


class TestCrud:
    def test_empty(self, tmp_path):
        assert load_locks(tmp_path) == []

    def test_add_then_list(self, tmp_path):
        lock = add_lock(tmp_path, "xiaoyan", "realm", "斗师", rationale="第12章", chapter=12)
        assert lock["created"] is True
        assert lock["id"] == lock_id("xiaoyan", "realm")
        locks = load_locks(tmp_path)
        assert len(locks) == 1
        assert locks[0]["value"] == "斗师"
        assert locks[0]["created_chapter"] == 12

    def test_add_is_idempotent_for_same_value(self, tmp_path):
        add_lock(tmp_path, "xiaoyan", "realm", "斗师")
        again = add_lock(tmp_path, "xiaoyan", "realm", "斗师")
        assert again["created"] is False
        assert len(load_locks(tmp_path)) == 1

    def test_override_keeps_single_row(self, tmp_path):
        add_lock(tmp_path, "xiaoyan", "realm", "斗师")
        over = add_lock(tmp_path, "xiaoyan", "realm", "斗圣")
        assert over["created"] is False
        locks = load_locks(tmp_path)
        assert len(locks) == 1
        assert locks[0]["value"] == "斗圣"

    def test_add_requires_all_fields(self, tmp_path):
        for args in [("", "realm", "斗师"), ("x", "", "斗师"), ("x", "realm", "")]:
            with pytest.raises(ValueError):
                add_lock(tmp_path, *args)

    def test_remove_by_id_and_dotted_and_subject(self, tmp_path):
        lock = add_lock(tmp_path, "xiaoyan", "realm", "斗师")
        assert remove_lock(tmp_path, lock["id"]) is True
        add_lock(tmp_path, "xiaoyan", "realm", "斗师")
        assert remove_lock(tmp_path, "xiaoyan.realm") is True
        add_lock(tmp_path, "xiaoyan", "realm", "斗师")
        assert remove_lock(tmp_path, "xiaoyan") is True
        assert load_locks(tmp_path) == []

    def test_remove_missing_returns_false(self, tmp_path):
        assert remove_lock(tmp_path, "nope") is False


class TestCorruption:
    def _write_bad(self, tmp_path: Path) -> Path:
        path = locks_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{ broken", encoding="utf-8")
        return path

    def test_read_degrades(self, tmp_path):
        self._write_bad(tmp_path)
        assert load_locks(tmp_path) == []

    def test_strict_raises(self, tmp_path):
        self._write_bad(tmp_path)
        with pytest.raises(CanonLocksCorrupted):
            load_locks(tmp_path, strict=True)

    def test_write_refuses_to_clobber_bad_file(self, tmp_path):
        """坏文件 + add_lock → 抛错，且**原文不变**（作者的锁不能被一次读失败抹掉）。"""
        path = self._write_bad(tmp_path)
        with pytest.raises(CanonLocksCorrupted):
            add_lock(tmp_path, "xiaoyan", "realm", "斗师")
        assert path.read_text(encoding="utf-8") == "{ broken"


class TestConflictDetection:
    def _locks(self, tmp_path):
        add_lock(tmp_path, "xiaoyan", "realm", "斗师", rationale="第12章")
        return load_locks(tmp_path)

    def test_same_value_is_not_conflict(self, tmp_path):
        locks = self._locks(tmp_path)
        events = [{"event_type": "character_state_changed", "subject": "xiaoyan",
                   "payload": {"field": "realm", "new": "斗师"}}]
        assert detect_conflicts(locks, events) == []

    def test_different_value_conflicts(self, tmp_path):
        locks = self._locks(tmp_path)
        events = [{"event_type": "character_state_changed", "subject": "xiaoyan",
                   "payload": {"field": "realm", "new": "斗尊"}}]
        conflicts = detect_conflicts(locks, events)
        assert len(conflicts) == 1
        assert conflicts[0]["locked_value"] == "斗师"
        assert conflicts[0]["new_value"] == "斗尊"
        assert conflicts[0]["rationale"] == "第12章"

    def test_unlocked_subject_ignored(self, tmp_path):
        locks = self._locks(tmp_path)
        events = [{"event_type": "character_state_changed", "subject": "yaolao",
                   "payload": {"field": "realm", "new": "斗宗"}}]
        assert detect_conflicts(locks, events) == []

    def test_power_breakthrough_defaults_to_realm(self, tmp_path):
        locks = self._locks(tmp_path)
        events = [{"event_type": "power_breakthrough", "subject": "xiaoyan",
                   "payload": {"new_realm": "斗皇"}}]
        conflicts = detect_conflicts(locks, events)
        assert conflicts and conflicts[0]["field"] == "realm"

    def test_field_path_alias_hits(self, tmp_path):
        locks = self._locks(tmp_path)
        events = [{"event_type": "character_state_changed", "subject": "xiaoyan",
                   "payload": {"field_path": "realm", "new_value": "斗王"}}]
        assert len(detect_conflicts(locks, events)) == 1

    def test_events_without_new_value_skipped(self, tmp_path):
        locks = self._locks(tmp_path)
        events = [{"event_type": "character_state_changed", "subject": "xiaoyan",
                   "payload": {"field": "realm"}}]
        assert detect_conflicts(locks, events) == []

    def test_no_fuzzy_matching(self, tmp_path):
        """「斗师」与「斗师巅峰」是两个值——是否等价由作者决定。"""
        locks = self._locks(tmp_path)
        events = [{"event_type": "character_state_changed", "subject": "xiaoyan",
                   "payload": {"field": "realm", "new": "斗师巅峰"}}]
        assert len(detect_conflicts(locks, events)) == 1

    def test_unrelated_event_types_ignored(self, tmp_path):
        locks = self._locks(tmp_path)
        events = [{"event_type": "open_loop_created", "subject": "xiaoyan",
                   "payload": {"field": "realm", "new": "斗圣"}}]
        assert detect_conflicts(locks, events) == []

    def test_format_conflicts_readable(self, tmp_path):
        locks = self._locks(tmp_path)
        conflicts = detect_conflicts(locks, [{
            "event_type": "character_state_changed", "subject": "xiaoyan",
            "payload": {"field": "realm", "new": "斗尊"},
        }])
        text = format_conflicts(conflicts)
        assert "xiaoyan.realm" in text and "斗师" in text and "斗尊" in text


class TestBuildCommitIntegration:
    def _payload_args(self, extraction_events):
        return dict(
            chapter=3,
            review_result={"blocking_count": 0, "issues": []},
            fulfillment_result={"planned_nodes": [], "covered_nodes": [], "missed_nodes": []},
            disambiguation_result={"pending": []},
            extraction_result={
                "state_deltas": [], "entity_deltas": [],
                "accepted_events": extraction_events, "ambiguous_mentions": [],
            },
        )

    def test_conflict_rejects_commit(self, tmp_path):
        root = _project(tmp_path)
        add_lock(root, "xiaoyan", "realm", "斗师")
        service = ChapterCommitService(root)
        payload = service.build_commit(**self._payload_args([{
            "event_type": "character_state_changed",
            "subject": "xiaoyan",
            "payload": {"field": "realm", "new": "斗尊"},
        }]))
        assert payload["meta"]["status"] == "rejected"
        assert payload["canon_conflicts"][0]["new_value"] == "斗尊"

    def test_matching_value_keeps_accepted(self, tmp_path):
        root = _project(tmp_path)
        add_lock(root, "xiaoyan", "realm", "斗师")
        service = ChapterCommitService(root)
        payload = service.build_commit(**self._payload_args([{
            "event_type": "character_state_changed",
            "subject": "xiaoyan",
            "payload": {"field": "realm", "new": "斗师"},
        }]))
        assert payload["meta"]["status"] == "accepted"
        assert payload["canon_conflicts"] == []

    def test_no_locks_means_no_check(self, tmp_path):
        root = _project(tmp_path)
        service = ChapterCommitService(root)
        payload = service.build_commit(**self._payload_args([{
            "event_type": "character_state_changed",
            "subject": "xiaoyan",
            "payload": {"field": "realm", "new": "斗尊"},
        }]))
        assert payload["meta"]["status"] == "accepted"
        assert payload["canon_conflicts"] == []


class TestCli:
    """CLI 必须真的可用——锁定是作者的决定，不能只靠手工编辑 JSON。"""

    def _run(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "webnovel.py"), "canon", *args,
             "--project-root", str(root)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=env,
        )

    def test_lock_list_unlock_roundtrip(self, tmp_path):
        root = _project(tmp_path)

        locked = self._run(root, "lock", "--subject", "xiaoyan",
                           "--field", "realm", "--value", "斗师", "--chapter", "12")
        assert locked.returncode == 0, locked.stderr
        assert "xiaoyan.realm" in locked.stdout

        listed = self._run(root, "list")
        assert listed.returncode == 0
        assert "斗师" in listed.stdout and "第 12 章" in listed.stdout

        unlocked = self._run(root, "unlock", "--id", "xiaoyan.realm")
        assert unlocked.returncode == 0
        assert load_locks(root) == []

    def test_unlock_missing_exits_nonzero(self, tmp_path):
        root = _project(tmp_path)
        result = self._run(root, "unlock", "--id", "nope")
        assert result.returncode != 0

    def test_list_empty(self, tmp_path):
        root = _project(tmp_path)
        result = self._run(root, "list")
        assert result.returncode == 0
        assert "没有锁定" in result.stdout
