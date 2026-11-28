#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""章节状态变更的可读审计（`webnovel.py changes list|diff`）。

外部依据：NarraLume「AI 的建议不是已保存的决定」——故事变更应当先被作者看见。
本项目 accepted 章节的 state_deltas 直接投影进 state.json，中间没有可读中间态；
本模块把"这一章改了什么"变成一条命令，是后续候选闸门（G3 第二阶段）的前置。

本文件钉住：
1. `collect_changes()` 与 `StateProjectionWriter._collect_state_deltas()` **输出一致**
   ——审计看到的字段必须就是实际投影的字段，否则两者会各自漂移；
2. 回退链完整（`new`/`to`/`new_value`/`new_state`、`old`/`from`/`old_value`/`previous_state`）；
3. 同 `(entity_id, field)` 去重，且 `state_deltas` 优先于事件补充；
4. `diff_changes()` 的 changed / added / removed 三态；
5. CLI `list` 与 `diff` 端到端。
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

from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.state_change_log import (  # noqa: E402
    annotate_changes,
    audit_changes,
    collect_changes,
    commit_path,
    diff_changes,
    detect_value_regressions,
    format_audit,
    format_candidates,
    format_preview,
    format_trace,
    format_changes,
    format_diff,
    latest_chapter,
    load_commit,
    risk_of,
    list_lock_candidates,
    preview_payload,
    trace_changes,
)
from data_modules.state_projection_writer import StateProjectionWriter  # noqa: E402


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".story-system" / "commits").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"progress": {}}), encoding="utf-8")
    return tmp_path


def _write_commit(root: Path, chapter: int, payload: dict) -> Path:
    path = root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _payload(**kwargs) -> dict:
    base = {"meta": {"chapter": 1, "status": "accepted"}, "state_deltas": [],
            "accepted_events": []}
    base.update(kwargs)
    return base


class TestCollect:
    def test_state_deltas_taken_first(self):
        rows = collect_changes(_payload(state_deltas=[
            {"entity_id": "xiaoyan", "field": "realm", "old": "斗者", "new": "斗师"},
        ]))
        assert rows == [{
            "entity_id": "xiaoyan", "field": "realm", "old": "斗者",
            "new": "斗师", "source": "state_deltas",
        }]

    def test_events_fill_the_gap(self):
        rows = collect_changes(_payload(accepted_events=[
            {"event_type": "character_state_changed", "subject": "xiaoyan",
             "payload": {"field": "location", "to": "乌坦城", "from": "魔兽山脉"}},
        ]))
        assert rows[0]["field"] == "location"
        assert rows[0]["new"] == "乌坦城"          # `to` 回退必须生效
        assert rows[0]["old"] == "魔兽山脉"        # `from` 回退必须生效
        assert rows[0]["source"] == "event:character_state_changed"

    def test_power_breakthrough_defaults_to_realm(self):
        rows = collect_changes(_payload(accepted_events=[
            {"event_type": "power_breakthrough", "subject": "xiaoyan",
             "payload": {"new_realm": "斗师"}},
        ]))
        assert rows[0]["field"] == "realm"
        assert rows[0]["new"] == "斗师"

    def test_dedup_prefers_state_deltas(self):
        rows = collect_changes(_payload(
            state_deltas=[{"entity_id": "a", "field": "realm", "new": "斗师"}],
            accepted_events=[{"event_type": "power_breakthrough", "subject": "a",
                              "payload": {"new_realm": "斗宗"}}],
        ))
        assert len(rows) == 1
        assert rows[0]["new"] == "斗师"
        assert rows[0]["source"] == "state_deltas"

    def test_empty_payload(self):
        assert collect_changes(None) == []
        assert collect_changes({}) == []


class TestParityWithProjectionWriter:
    """审计与投影必须对同一份 payload 得出同一组 (entity_id, field, new)。"""

    @pytest.mark.parametrize("payload", [
        _payload(state_deltas=[{"entity_id": "a", "field": "realm", "new": "x"}]),
        _payload(accepted_events=[
            {"event_type": "character_state_changed", "subject": "b",
             "payload": {"field": "state", "new_value": "重伤"}},
            {"event_type": "power_breakthrough", "subject": "c",
             "payload": {"new_realm": "斗王"}},
        ]),
        _payload(
            state_deltas=[{"entity_id": "d", "field": "realm", "new": "1"}],
            accepted_events=[{"event_type": "power_breakthrough", "subject": "d",
                              "payload": {"new_realm": "2"}}],
        ),
    ])
    def test_same_fields(self, tmp_path, payload):
        (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
        writer = StateProjectionWriter(tmp_path)
        projected = writer._collect_state_deltas(payload)
        audited = collect_changes(payload)
        key = lambda rows: [(r["entity_id"], r["field"], str(r.get("new"))) for r in rows]
        assert key(projected) == key(audited)


class TestDiff:
    def test_changed_added_removed(self):
        left = [
            {"entity_id": "a", "field": "realm", "old": "1", "new": "2", "source": "x"},
            {"entity_id": "a", "field": "state", "old": "", "new": "健康", "source": "x"},
            {"entity_id": "b", "field": "realm", "old": "", "new": "斗者", "source": "x"},
        ]
        right = [
            {"entity_id": "a", "field": "realm", "old": "2", "new": "3", "source": "x"},
            {"entity_id": "a", "field": "state", "old": "健康", "new": "健康", "source": "x"},
            {"entity_id": "c", "field": "realm", "old": "", "new": "斗师", "source": "x"},
        ]
        diff = diff_changes(left, right)
        assert [(r["entity_id"], r["field"]) for r in diff["changed"]] == [("a", "realm")]
        assert [r["entity_id"] for r in diff["added"]] == ["c"]
        assert [r["entity_id"] for r in diff["removed"]] == ["b"]

    def test_same_values_are_not_changed(self):
        rows = [{"entity_id": "a", "field": "realm", "old": "1", "new": "2", "source": "x"}]
        assert diff_changes(rows, rows)["changed"] == []

    def test_format_helpers(self):
        assert "没有状态变更" in format_changes([], 3)
        assert "完全一致" in format_diff({"changed": [], "added": [], "removed": []})
        text = format_changes([{"entity_id": "a", "field": "realm", "old": None,
                                "new": "斗师", "source": "state_deltas"}], 2)
        assert "a.realm" in text and "斗师" in text


class TestCommitLocation:
    def test_three_and_four_digit(self, tmp_path):
        root = _project(tmp_path)
        three = _write_commit(root, 7, _payload())
        assert commit_path(root, 7) == three
        base = root / ".story-system" / "commits"
        three.unlink()
        four = base / "chapter_0007.commit.json"
        four.write_text("{}", encoding="utf-8")
        assert commit_path(root, 7) == four

    def test_missing_commit(self, tmp_path):
        assert load_commit(_project(tmp_path), 99) is None

    def test_broken_commit(self, tmp_path):
        root = _project(tmp_path)
        path = root / ".story-system" / "commits" / "chapter_005.commit.json"
        path.write_text("{broken", encoding="utf-8")
        assert load_commit(root, 5) is None


class TestCli:
    def _run(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "webnovel.py"), "changes", *args,
             "--project-root", str(root)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=env,
        )

    def test_list(self, tmp_path):
        root = _project(tmp_path)
        _write_commit(root, 3, _payload(state_deltas=[
            {"entity_id": "xiaoyan", "field": "realm", "old": "斗者", "new": "斗师"},
        ]))
        result = self._run(root, "list", "--chapter", "3")
        assert result.returncode == 0, result.stderr
        assert "xiaoyan.realm" in result.stdout
        assert "斗师" in result.stdout

    def test_list_missing_chapter(self, tmp_path):
        result = self._run(_project(tmp_path), "list", "--chapter", "9")
        assert result.returncode != 0

    def test_diff(self, tmp_path):
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "realm", "old": "1", "new": "2"},
        ]))
        _write_commit(root, 2, _payload(state_deltas=[
            {"entity_id": "a", "field": "realm", "old": "2", "new": "3"},
        ]))
        result = self._run(root, "diff", "--from", "1", "--to", "2")
        assert result.returncode == 0, result.stderr
        assert "a.realm" in result.stdout and "2 → 3" in result.stdout

    def test_json(self, tmp_path):
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "realm", "new": "x"},
        ]))
        result = self._run(root, "list", "--chapter", "1", "--json")
        assert json.loads(result.stdout)[0]["field"] == "realm"


class TestRisk:
    def test_high_risk_fields(self):
        assert risk_of("realm") == "high"
        assert risk_of("protagonist.realm") == "high"   # 点路径取末段
        assert risk_of("status") == "high"
        assert risk_of("is_dead") == "high"

    def test_medium_risk_prefixes(self):
        assert risk_of("location") == "medium"
        assert risk_of("location.current") == "medium"
        assert risk_of("relationship.sect") == "medium"

    def test_low_risk(self):
        assert risk_of("hair_color") == "low"
        assert risk_of("") == "low"

    def test_annotate_sets_risk(self):
        rows = annotate_changes([
            {"entity_id": "a", "field": "realm", "new": "斗师", "source": "x"},
            {"entity_id": "a", "field": "hair_color", "new": "黑", "source": "x"},
        ])
        assert [r["risk"] for r in rows] == ["high", "low"]

    def test_annotate_flags_lock_violation(self):
        rows = annotate_changes(
            [{"entity_id": "a", "field": "realm", "new": "斗宗", "source": "x"}],
            locks=[{"subject": "a", "field": "realm", "value": "斗师"}],
        )
        assert rows[0]["locked_value"] == "斗师"

    def test_annotate_no_flag_when_value_matches_lock(self):
        rows = annotate_changes(
            [{"entity_id": "a", "field": "realm", "new": "斗师", "source": "x"}],
            locks=[{"subject": "a", "field": "realm", "value": "斗师"}],
        )
        assert "locked_value" not in rows[0]

    def test_annotate_ignores_unrelated_lock(self):
        rows = annotate_changes(
            [{"entity_id": "b", "field": "realm", "new": "斗宗", "source": "x"}],
            locks=[{"subject": "a", "field": "realm", "value": "斗师"}],
        )
        assert "locked_value" not in rows[0]


class TestAudit:
    def test_latest_chapter(self, tmp_path):
        root = _project(tmp_path)
        assert latest_chapter(root) == 0
        _write_commit(root, 3, _payload())
        _write_commit(root, 11, _payload())
        assert latest_chapter(root) == 11

    def test_only_high_risk_and_lock_violations(self, tmp_path):
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "hair_color", "new": "黑"},      # low
            {"entity_id": "a", "field": "realm", "new": "斗师"},          # high
        ]))
        _write_commit(root, 2, _payload(state_deltas=[
            {"entity_id": "a", "field": "mood", "new": "怒"},             # low
        ]))
        findings = audit_changes(root, limit=10, locks=[])
        assert [f["field"] for f in findings] == ["realm"]
        assert findings[0]["chapter"] == 1

    def test_limit_window(self, tmp_path):
        root = _project(tmp_path)
        for chapter in (1, 2, 3):
            _write_commit(root, chapter, _payload(state_deltas=[
                {"entity_id": "a", "field": "realm", "new": f"L{chapter}"},
            ]))
        findings = audit_changes(root, limit=1, locks=[])
        assert [f["chapter"] for f in findings] == [3]

    def test_reports_lock_violation_on_low_risk_field(self, tmp_path):
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "hair_color", "new": "白"},
        ]))
        findings = audit_changes(
            root, limit=10,
            locks=[{"subject": "a", "field": "hair_color", "value": "黑"}],
        )
        assert len(findings) == 1
        assert findings[0]["locked_value"] == "黑"

    def test_empty_format(self):
        assert "没有高危" in format_audit([])

    def test_merges_identical_changes_across_chapters(self, tmp_path):
        """真书验证发现的：同一变更在两章重复出现，通常是投影重复应用了同一份 delta。

        合并而不是并列两行——否则「重复」这个信号会被两条一模一样的行掩盖。
        """
        root = _project(tmp_path)
        same = {"entity_id": "a", "field": "status", "old": "存活", "new": "死亡"}
        _write_commit(root, 19, _payload(state_deltas=[dict(same)]))
        _write_commit(root, 20, _payload(state_deltas=[dict(same)]))
        findings = audit_changes(root, limit=10, locks=[])
        assert len(findings) == 1, findings
        assert findings[0]["occurrences"] == 2
        assert findings[0]["chapters"] == [19, 20]
        assert findings[0]["chapter"] == 19

    def test_keeps_distinct_new_values_separate(self, tmp_path):
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "realm", "old": "斗者", "new": "斗师"},
        ]))
        _write_commit(root, 2, _payload(state_deltas=[
            {"entity_id": "a", "field": "realm", "old": "斗师", "new": "斗灵"},
        ]))
        findings = audit_changes(root, limit=10, locks=[])
        assert [f["new"] for f in findings] == ["斗师", "斗灵"]
        assert all(f["occurrences"] == 1 for f in findings)

    def test_repeat_marker_in_text(self, tmp_path):
        root = _project(tmp_path)
        same = {"entity_id": "a", "field": "status", "old": "存活", "new": "死亡"}
        _write_commit(root, 1, _payload(state_deltas=[dict(same)]))
        _write_commit(root, 2, _payload(state_deltas=[dict(same)]))
        text = format_audit(audit_changes(root, limit=10, locks=[]))
        assert "重复 2 次" in text and "第 1,2 章" in text


class TestTrace:
    """`changes trace`：某 (实体, 字段) 的跨章时间线 + 值回退检测。

    这个命令的存在理由来自真书验证：`canon lock` 的价值取决于「锁哪个字段」，
    而判断要基于事实（改过几次、有没有值回退），不是凭印象。真书里
    `chip.remaining_uses` 有 13 次变更、其中 5 条是 `29 → 29` 这种空变更——
    那是投影把未变字段也记成 delta 的噪声，既不该算回退，也值得单独标出来。
    """

    def test_collects_only_target_field(self, tmp_path):
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "realm", "old": "斗者", "new": "斗师"},
            {"entity_id": "a", "field": "mood", "old": "平静", "new": "愤怒"},
            {"entity_id": "b", "field": "realm", "old": "斗师", "new": "斗灵"},
        ]))
        rows = trace_changes(root, "a", "realm", locks=[])
        assert [r["new"] for r in rows] == ["斗师"]
        assert rows[0]["chapter"] == 1

    def test_detects_value_regression(self, tmp_path):
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "remaining", "old": "30", "new": "29"}]))
        _write_commit(root, 2, _payload(state_deltas=[
            {"entity_id": "a", "field": "remaining", "old": "29", "new": "28"}]))
        _write_commit(root, 3, _payload(state_deltas=[
            {"entity_id": "a", "field": "remaining", "old": "28", "new": "29"}]))
        rows = trace_changes(root, "a", "remaining", locks=[])
        regressions = detect_value_regressions(rows)
        assert [r["chapter"] for r in regressions] == [3]
        assert regressions[0]["previously_at"] == 1

    def test_noop_change_is_not_a_regression(self, tmp_path):
        """`old == new` 是空变更，不是回退——真书里这种噪声占 5/13。"""
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "remaining", "old": "29", "new": "29"}]))
        _write_commit(root, 2, _payload(state_deltas=[
            {"entity_id": "a", "field": "remaining", "old": "29", "new": "29"}]))
        rows = trace_changes(root, "a", "remaining", locks=[])
        assert detect_value_regressions(rows) == []

    def test_format_marks_noop_and_regression(self, tmp_path):
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "remaining", "old": "30", "new": "29"}]))
        _write_commit(root, 2, _payload(state_deltas=[
            {"entity_id": "a", "field": "remaining", "old": "29", "new": "29"}]))
        rows = trace_changes(root, "a", "remaining", locks=[])
        text = format_trace(rows, "a", "remaining",
                            regressions=detect_value_regressions(rows))
        assert "≈无变化" in text
        assert "1 条「无变化的空变更」" in text

    def test_empty_trace(self, tmp_path):
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "realm", "new": "斗师"}]))
        assert format_trace([], "x", "y") == "x.y：没有找到任何变更记录。"


class TestLockCandidates:
    """`changes candidates`：全书扫描「值得考虑交给 canon lock」的字段。

    只给客观统计（变更次数 / 回退次数 / 空变更次数），不做语义推断、不写 lock。
    真书第一次跑给出 4 个候选，其中 `sun_bai.status`（第 31–63 章改 5 次）
    与 `tianyan_zhang_supervisor.status`（有 1 次回退）正是"角色已死却还在改状态"
    的典型——这类字段锁住能立刻拦住后续误改。
    """

    def test_only_high_risk_meeting_threshold(self, tmp_path):
        root = _project(tmp_path)
        for chapter in (1, 2, 3):
            _write_commit(root, chapter, _payload(state_deltas=[
                {"entity_id": "a", "field": "status", "old": f"s{chapter}", "new": f"s{chapter + 1}"},
                {"entity_id": "a", "field": "mood", "old": f"m{chapter}", "new": f"m{chapter + 1}"},
            ]))
        candidates = list_lock_candidates(root, min_changes=3, locks=[])
        assert [c["field"] for c in candidates] == ["status"], candidates

    def test_counts_regressions_and_noops(self, tmp_path):
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "status", "old": "存活", "new": "受伤"}]))
        _write_commit(root, 2, _payload(state_deltas=[
            {"entity_id": "a", "field": "status", "old": "受伤", "new": "受伤"}]))   # 空变更
        _write_commit(root, 3, _payload(state_deltas=[
            {"entity_id": "a", "field": "status", "old": "受伤", "new": "存活"}]))
        _write_commit(root, 4, _payload(state_deltas=[
            {"entity_id": "a", "field": "status", "old": "存活", "new": "受伤"}]))   # 值回退
        candidate = list_lock_candidates(root, min_changes=4, locks=[])[0]
        assert candidate["changes"] == 4
        assert candidate["regressions"] == 1
        assert candidate["noops"] == 1

    def test_empty_change_does_not_overwrite_latest_value(self, tmp_path):
        """真书 `chen_sheng.realm` 的最后一条是 `— → —`（old/new 全缺）。

        若无条件取最后一行，「最新值」会显示 `—`，看起来像"境界变成空了"——纯误导。
        """
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "realm", "old": "斗者", "new": "斗师"}]))
        _write_commit(root, 2, _payload(state_deltas=[
            {"entity_id": "a", "field": "realm", "old": "斗师", "new": "斗灵"}]))
        _write_commit(root, 3, _payload(state_deltas=[
            {"entity_id": "a", "field": "realm"}]))          # 空 delta
        candidate = list_lock_candidates(root, min_changes=3, locks=[])[0]
        assert candidate["latest_value"] == "斗灵"
        assert candidate["noops"] == 1

    def test_flags_already_locked(self, tmp_path):
        root = _project(tmp_path)
        for chapter in (1, 2, 3):
            _write_commit(root, chapter, _payload(state_deltas=[
                {"entity_id": "a", "field": "realm", "old": f"x{chapter}", "new": f"x{chapter + 1}"}]))
        candidates = list_lock_candidates(
            root, min_changes=3, locks=[{"subject": "a", "field": "realm", "value": "x1"}])
        assert candidates[0]["already_locked"] is True

    def test_format_when_empty(self):
        assert "没有符合条件的候选字段" in format_candidates([])


class TestPreview:
    """`changes preview`：对**尚未提交**的 payload 预演变更。

    这是 G3「候选闸门」的不阻断版本——闸门要在提交链插一道人工确认，会打断
    `orchestrate write "1-50"`；而"事前可见"只需让作者在 commit 之前知道
    「会改 N 项、其中 M 项高危、有没有撞锁」，不用拦。
    """

    def test_counts_high_risk(self):
        report = preview_payload({"state_deltas": [
            {"entity_id": "a", "field": "realm", "old": "斗者", "new": "斗师"},
            {"entity_id": "a", "field": "hair_color", "old": "黑", "new": "白"},
        ]})
        assert report["count"] == 2
        assert report["high_risk_count"] == 1
        assert report["would_be_rejected"] is False

    def test_detects_lock_conflict(self):
        report = preview_payload(
            {"state_deltas": [
                {"entity_id": "a", "field": "realm", "old": "斗师", "new": "斗宗"},
            ]},
            locks=[{"subject": "a", "field": "realm", "value": "斗师"}],
        )
        assert report["would_be_rejected"] is True
        assert report["lock_conflicts"][0]["entity_id"] == "a"

    def test_accepts_nested_extraction_shape(self):
        """`extraction_result.json` 的 `state_deltas` 在顶层；commit 的在嵌套里。

        `collect_changes` 用 `extraction_list()` 取，两种形状都认——这条钉住它。
        """
        top_level = preview_payload({"state_deltas": [
            {"entity_id": "a", "field": "status", "old": "存活", "new": "死亡"}]})
        nested = preview_payload({"extraction_result": {"state_deltas": [
            {"entity_id": "a", "field": "status", "old": "存活", "new": "死亡"}]}})
        assert top_level["count"] == nested["count"] == 1
        assert top_level["high_risk_count"] == nested["high_risk_count"] == 1

    def test_empty_payload(self):
        assert "不产生任何状态变更" in format_preview(preview_payload({}))

    def test_format_shows_conflict_and_high_risk(self):
        report = preview_payload(
            {"state_deltas": [
                {"entity_id": "a", "field": "status", "old": "存活", "new": "死亡"},
                {"entity_id": "a", "field": "realm", "old": "斗师", "new": "斗宗"},
            ]},
            locks=[{"subject": "a", "field": "realm", "value": "斗师"}],
        )
        text = format_preview(report)
        assert "会因违背 canon lock 被拒章" in text
        assert "高危变更" in text


class TestValueClipping:
    """终端输出截断，JSON 不截断。

    真书的 `status` 常是整段叙述（「受伤（右肩与上臂间能量刃切口…）」），
    不截断时一条变更占满整屏，几十项的高危清单根本扫不动。但机器读的
    `--json` 必须拿完整值，否则下游会拿到被截断的数据。
    """

    def test_clip_truncates(self):
        from data_modules.state_change_log import VALUE_MAX_CHARS, _clip

        long_text = "伤" * (VALUE_MAX_CHARS + 20)
        clipped = _clip(long_text)
        assert len(clipped) == VALUE_MAX_CHARS + 1
        assert clipped.endswith("…")

    def test_clip_keeps_short_values(self):
        from data_modules.state_change_log import _clip

        assert _clip("斗师") == "斗师"
        assert _clip(None) == "—"

    def test_audit_text_is_clipped(self, tmp_path):
        from data_modules.state_change_log import VALUE_MAX_CHARS

        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "status", "old": "存活", "new": "死" * 200},
        ]))
        text = format_audit(audit_changes(root, limit=5, locks=[]))
        assert "死" * (VALUE_MAX_CHARS + 1) not in text
        assert "…" in text

    def test_json_output_keeps_full_value(self, tmp_path):
        """CLI `--json` 不做截断——机器读的场合要完整值。"""
        root = _project(tmp_path)
        long_new = "死" * 200
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "status", "old": "存活", "new": long_new},
        ]))
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "webnovel.py"), "changes", "audit",
             "--project-root", str(root), "--limit", "5", "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=env,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload[0]["new"] == long_new


class TestCliRiskFlags:
    def _run(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "webnovel.py"), "changes", *args,
             "--project-root", str(root)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=env,
        )

    def test_high_risk_filter(self, tmp_path):
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "realm", "new": "斗师"},
            {"entity_id": "a", "field": "hair_color", "new": "黑"},
        ]))
        result = self._run(root, "list", "--chapter", "1", "--high-risk")
        assert result.returncode == 0, result.stderr
        assert "realm" in result.stdout
        assert "hair_color" not in result.stdout

    def test_audit_command(self, tmp_path):
        root = _project(tmp_path)
        _write_commit(root, 1, _payload(state_deltas=[
            {"entity_id": "a", "field": "realm", "new": "斗师"},
        ]))
        result = self._run(root, "audit", "--limit", "5")
        assert result.returncode == 0, result.stderr
        assert "realm" in result.stdout and "高危" in result.stdout
