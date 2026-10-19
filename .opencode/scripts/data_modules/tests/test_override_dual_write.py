#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`override_contract_engine` 的双写一致性。

`add_override()` 维护两处：

1. `.story-system/override_contracts.json`（人读、版本链、`superseded` 标记）
2. 事件日志 `.story-system/events/*.event.json`（`override_rule_added`）

写顺序是「先 `_write_overrides()` 落盘 → 再 `publish_event()`」。后者失败
时异常往上冒，但文件已经写好了：

    add_override(...)              # publish_event 抛异常
    → override_contracts.json 里已有 active 记录
    → 事件日志没有 override_rule_added
    → 下次 rebuild 按事件重放 ⇒ 这个覆盖不存在，旧规则回来

而 `rebuild_state_json` 确实消费 `override_rule_added`（`ssot_enforcer.py`
的 `elif etype == "override_rule_added"`），所以两侧真的会分叉。

这与上一笔修掉的 `chapter_delete_service` 是同一类缺陷：**派生/投影状态
先落盘，事实来源后失败**。既然事件日志被定义为 SSOT 的事实来源，它就该
先成功。

另外本文件覆盖版本链的基本语义——这是「版本化世界规则」的核心：
每次 add 应 supersede 上一版，`get_effective_rules()` 只能返回当前生效的那版。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import data_modules.override_contract_engine as engine  # noqa: E402
from data_modules.ssot_enforcer import read_events, rebuild_state_json  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _store(project: Path) -> dict:
    path = project / ".story-system" / "override_contracts.json"
    if not path.is_file():
        return {"contracts": {}, "history": []}
    return json.loads(path.read_text(encoding="utf-8"))


class TestVersionChain:
    """版本化世界规则的基本语义。"""

    def test_first_add_is_version_1(self, project):
        rec = engine.add_override(project, "flight", "不可飞行", "可飞行", "得珠", 5)
        assert rec["version"] == 1
        assert rec["status"] == "active"

    def test_second_add_supersedes_first(self, project):
        engine.add_override(project, "flight", "不可飞行", "可飞行", "得珠", 5)
        engine.add_override(project, "flight", "可飞行", "可御剑", "化神", 8)

        chain = _store(project)["contracts"]["flight"]
        assert [c["status"] for c in chain] == ["superseded", "active"]
        assert [c["version"] for c in chain] == [1, 2]

    def test_effective_rules_only_returns_active(self, project):
        engine.add_override(project, "flight", "不可飞行", "可飞行", "得珠", 5)
        engine.add_override(project, "flight", "可飞行", "可御剑", "化神", 8)

        effective = engine.get_effective_rules(project)
        rules = [r for r in effective if r.get("constraint_id") == "flight"]
        assert len(rules) == 1, f"应只返回当前生效版本，实际 {rules}"
        assert rules[0]["new_rule"] == "可御剑"

    def test_history_records_every_version(self, project):
        for i, rule in enumerate(["A", "B", "C"], start=1):
            engine.add_override(project, "x", f"旧{i}", rule, "理由", i)

        history = engine.get_rule_history(project, "x")
        assert [h["version"] for h in history] == [1, 2, 3]

    def test_different_constraints_are_independent(self, project):
        engine.add_override(project, "flight", "不可飞", "可飞", "r", 5)
        engine.add_override(project, "money", "无灵石", "有灵石", "r", 6)

        contracts = _store(project)["contracts"]
        assert contracts["flight"][0]["status"] == "active"
        assert contracts["money"][0]["status"] == "active"

    def test_domain_filter(self, project):
        engine.add_override(project, "a", "旧", "新", "r", 1, domain="world_rule")
        engine.add_override(project, "b", "旧", "新", "r", 2, domain="character")

        only_world = engine.get_effective_rules(project, domain="world_rule")
        ids = {r.get("constraint_id") for r in only_world}
        assert ids == {"a"}


class TestEventIsWritten:
    """每次覆盖都应留下事件，供 rebuild 重放。"""

    def test_event_published(self, project):
        engine.add_override(project, "flight", "不可飞", "可飞", "r", 5)
        events = read_events(project, event_type="override_rule_added")
        assert len(events) == 1
        assert events[0]["payload"]["constraint_id"] == "flight"

    def test_one_event_per_version(self, project):
        engine.add_override(project, "f", "旧1", "新1", "r", 1)
        engine.add_override(project, "f", "旧2", "新2", "r", 2)
        assert len(read_events(project, event_type="override_rule_added")) == 2

    def test_rebuild_replays_override_rules(self, project):
        engine.add_override(project, "flight", "不可飞", "可飞", "r", 5)
        state = rebuild_state_json(project)
        rules = state.get("override_rules") or []
        assert any(r.get("constraint_id") == "flight" for r in rules), (
            f"rebuild 没有重放出 override_rules：{rules}"
        )


class TestEventFailureMustNotLeaveStoreAhead:
    """核心：事件写失败时，override 存储不能单方面领先。"""

    def _add_with_failing_event(self, project, monkeypatch):
        monkeypatch.setattr(
            engine, "publish_event",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("event log down")),
        )
        with pytest.raises(RuntimeError):
            engine.add_override(project, "flight", "不可飞", "可飞", "r", 5)

    def test_store_not_modified(self, project, monkeypatch):
        self._add_with_failing_event(project, monkeypatch)

        events = read_events(project, event_type="override_rule_added")
        stored = _store(project)["contracts"]

        if not events:
            assert "flight" not in stored, (
                "事件日志里没有 override_rule_added，但 override_contracts.json "
                "已经记下了这个覆盖。下次 rebuild 按事件重放时它消失，"
                "旧规则回来——用户会看到设定『自己变回去了』。"
            )

    def test_existing_chain_not_corrupted(self, project, monkeypatch):
        """已有版本链时失败，不能把上一版错误地标成 superseded。"""
        engine.add_override(project, "flight", "不可飞", "可飞", "r", 5)
        before = _store(project)["contracts"]["flight"]

        monkeypatch.setattr(
            engine, "publish_event",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(RuntimeError):
            engine.add_override(project, "flight", "可飞", "可御剑", "r", 8)
        monkeypatch.undo()

        after = _store(project)["contracts"]["flight"]
        # 失败后链里不该多出条目，也不该把 v1 标成 superseded
        assert len(after) == len(before), (
            f"失败的 add 往链里写了条目：{after}"
        )
        assert after[0]["status"] == "active", (
            f"失败的 add 把已有版本标成了 {after[0]['status']}——"
            f"旧版本被误判为已废弃"
        )

    def test_retry_after_failure_succeeds(self, project, monkeypatch):
        """事件写失败后重试应成功，且版本号不跳号。"""
        monkeypatch.setattr(
            engine, "publish_event",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(RuntimeError):
            engine.add_override(project, "flight", "不可飞", "可飞", "r", 5)
        monkeypatch.undo()

        rec = engine.add_override(project, "flight", "不可飞", "可飞", "r", 5)
        assert rec["version"] == 1, f"失败的那次不该占用版本号，实际 v{rec['version']}"
