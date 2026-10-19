#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`rebuild_state_json` 对未知事件类型的处理。

SSOT 的核心承诺是：事件日志是**不可变真相**，`rebuild_state_json()` 能
确定性地重放出全部投影状态。这个承诺有一个隐性前提——重放器认识每一种
写进日志的事件类型。

实测缺陷：`ssot_enforcer.rebuild_state_json()` 的事件分发是一长串
`elif etype == "..."`，**没有 `else`**。遇到不认识的类型就静默 `pass`：

    publish_event(root, "open_loop_created", ...)        # 认识
    publish_event(root, "some_future_event_type", ...)   # 不认识
    rebuild_state_json(root)
    → 那个事件的 payload 在重建结果里零落点，无警告、无计数、无占位

这不是假想的场景：

1. 事件日志是 append-only 且**跨版本累积**的。用户升级前写入的事件，
   若新版重命名/删除了某个类型，rebuild 会静默丢掉它的效果。
2. 插件或外部工具往日志里写自定类型时，重放静默无视。
3. 最危险的是「用户以为 rebuild 能恢复一切」——他是拿 rebuild 当
   灾难恢复手段用的，而它可能恢复出一个**看起来完整、实则缺数据**的状态。

这与本仓库其它地方的取向一致：`review_pipeline` 对形状异常的载荷合成
critical 阻断项，理由是「无法判断有没有问题 ≠ 没有问题」；本处同理，
「不认识这个事件 ≠ 这个事件没有效果」。

修复方向（不改变已知类型的重放结果）：把未知类型**留在状态里可查**，
并让 `rebuild_projections()` 把它报出来，而不是静默丢弃。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.ssot_enforcer import (  # noqa: E402
    publish_event,
    read_events,
    rebuild_projections,
    rebuild_state_json,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return tmp_path


class TestKnownEventsStillReplay:
    """先把已知类型的行为钉住，避免修未知分支时改坏已知的。"""

    def test_open_loop_created(self, project):
        publish_event(project, "open_loop_created", {"content": "伏笔A"}, chapter=1)
        state = rebuild_state_json(project)
        assert [l["content"] for l in state["foreshadowing"]] == ["伏笔A"]

    def test_chapter_status_changed(self, project):
        publish_event(project, "chapter_status_changed",
                      {"status": "committed"}, chapter=3)
        state = rebuild_state_json(project)
        assert state["progress"]["chapter_status"]["3"] == "chapter_committed"
        assert state["progress"]["current_chapter"] == 3

    def test_entity_created(self, project):
        publish_event(project, "entity_created",
                      {"entity_id": "e1", "entity_name": "萧炎",
                       "entity_type": "角色"}, chapter=1)
        state = rebuild_state_json(project)
        assert "e1" in str(state.get("entities_v3"))

    def test_multiple_known_types_together(self, project):
        publish_event(project, "open_loop_created", {"content": "A"}, chapter=1)
        publish_event(project, "chapter_status_changed",
                      {"status": "committed"}, chapter=1)
        publish_event(project, "open_loop_created", {"content": "B"}, chapter=2)

        state = rebuild_state_json(project)
        assert len(state["foreshadowing"]) == 2
        # current_chapter 只由 chapter_status_changed 推进；第 2 章只有
        # open_loop_created，不回退也不前进 —— 停在 1 是正确的。
        assert state["progress"]["current_chapter"] == 1
        assert state["progress"]["chapter_status"]["1"] == "chapter_committed"


class TestUnknownEventTypesAreNotSilentlyDropped:
    """核心断言：不认识的事件必须留下可查的痕迹。"""

    def test_unknown_type_is_recorded(self, project):
        publish_event(project, "some_future_event_type",
                      {"important": "data"}, chapter=5)
        state = rebuild_state_json(project)

        # 允许放在任何合理的字段名里；核心是「能被发现」，不是具体叫法
        recorded = _find_unknown_records(state)
        assert recorded, (
            "未知事件类型被完全静默丢弃——重建结果里找不到任何痕迹。"
            "用户拿 rebuild 当灾难恢复手段时，会恢复出一个"
            "『看起来完整、实则缺数据』的状态。"
        )

    def test_unknown_record_carries_identifying_info(self, project):
        publish_event(project, "some_future_event_type",
                      {"important": "data"}, chapter=5)
        state = rebuild_state_json(project)
        recorded = _find_unknown_records(state)
        assert recorded, "未记录未知事件"

        blob = str(recorded)
        assert "some_future_event_type" in blob, "记录里缺事件类型"
        assert "5" in blob, "记录里缺章节号"
        assert "important" in blob or "data" in blob, "记录里缺 payload"

    def test_known_events_do_not_produce_unknown_records(self, project):
        publish_event(project, "open_loop_created", {"content": "A"}, chapter=1)
        publish_event(project, "chapter_status_changed",
                      {"status": "committed"}, chapter=1)
        state = rebuild_state_json(project)
        assert not _find_unknown_records(state), (
            "已知类型不该被记成未知——那会让告警失去意义"
        )

    def test_unknown_events_do_not_break_replay(self, project):
        """未知事件夹在已知事件之间，不能让重放中断或错序。"""
        publish_event(project, "open_loop_created", {"content": "A"}, chapter=1)
        publish_event(project, "totally_unknown", {"x": 1}, chapter=1)
        publish_event(project, "open_loop_created", {"content": "B"}, chapter=2)

        state = rebuild_state_json(project)
        assert [l["content"] for l in state["foreshadowing"]] == ["A", "B"]


class TestRebuildProjectionsSurfacesUnknownEvents:
    """`rebuild_projections()` 是 CLI/流程入口，应把未知事件报给调用方。"""

    def test_summary_mentions_unknown_events(self, project):
        publish_event(project, "some_future_event_type", {"a": 1}, chapter=2)
        summary = rebuild_projections(project)

        blob = str(summary)
        assert "unknown" in blob.lower() or "未知" in blob, (
            f"rebuild_projections 的返回值没有提到未知事件：{summary}"
        )

    def test_no_false_positive_when_all_known(self, project):
        publish_event(project, "open_loop_created", {"content": "A"}, chapter=1)
        summary = rebuild_projections(project)
        blob = str(summary).lower()
        assert "unknown_event" not in blob, f"已知事件被误报为未知：{summary}"


# ── helpers ─────────────────────────────────────────────────────────────

def _find_unknown_records(state: dict) -> list:
    """在重建结果里找「未知事件」的记录，不绑定具体字段名。

    接受任意键名包含 unknown（或中文「未知」）的列表/字典；也接受
    `_unknown_events` 这类前缀命名的字段。
    """
    if not isinstance(state, dict):
        return []
    out = []
    for key, value in state.items():
        name = str(key).lower()
        if "unknown" in name or "未知" in str(key) or "unhandled" in name:
            if value:
                out.extend(value if isinstance(value, list) else [value])
    return out


class TestKnownTypeRegistryMatchesDispatch:
    """`KNOWN_EVENT_TYPES` 与 `rebuild_state_json` 里的 elif 链必须一致。

    两处是同一份知识的两个副本：一个是数据（集合），一个是代码（if/elif）。
    只改一处就会静默错位——
    - 集合多一项 → 该类型走 `continue` 被跳过，**永远不被重放**
    - 集合少一项 → 已实现的分支永远不执行，事件被记成"未知"

    两种都不会报错。这条用例把源码里的分支解析出来比对。
    """

    @staticmethod
    def _dispatch_types() -> set:
        import ast

        src = (SCRIPTS / "data_modules" / "ssot_enforcer.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        func = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "rebuild_state_json"
        )
        found = set()
        for node in ast.walk(func):
            if not isinstance(node, ast.Compare):
                continue
            # etype == "xxx"
            left = node.left
            if not (isinstance(left, ast.Name) and left.id == "etype"):
                continue
            for cmp in node.comparators:
                if isinstance(cmp, ast.Constant) and isinstance(cmp.value, str):
                    found.add(cmp.value)
        return found

    def test_registry_equals_dispatch(self):
        from data_modules.ssot_enforcer import KNOWN_EVENT_TYPES

        dispatch = self._dispatch_types()
        assert dispatch, "没能解析出任何 elif 分支——解析逻辑需更新"

        missing_in_registry = dispatch - set(KNOWN_EVENT_TYPES)
        missing_in_dispatch = set(KNOWN_EVENT_TYPES) - dispatch

        assert not missing_in_registry, (
            f"这些类型有重放分支但不在 KNOWN_EVENT_TYPES 里，"
            f"会被当成未知事件且永不重放: {sorted(missing_in_registry)}"
        )
        assert not missing_in_dispatch, (
            f"这些类型在 KNOWN_EVENT_TYPES 里但没有重放分支，"
            f"会被静默跳过: {sorted(missing_in_dispatch)}"
        )

    def test_every_recognized_type_is_not_reported_unknown(self, project):
        """登记为「认识」的类型（含无状态效果的标记类）都不该被报未知。"""
        from data_modules.ssot_enforcer import RECOGNIZED_EVENT_TYPES

        for etype in sorted(RECOGNIZED_EVENT_TYPES):
            publish_event(project, etype, {"_probe": True}, chapter=1)

        state = rebuild_state_json(project)
        assert not state.get("_unknown_events"), (
            f"已登记的类型仍被报成未知: {state.get('_unknown_events')}"
        )

    def test_types_without_state_effect_have_no_dispatch_branch(self):
        """无状态效果的类型不该有重放分支——否则两套语义会打架。"""
        from data_modules.ssot_enforcer import EVENT_TYPES_WITHOUT_STATE_EFFECT

        dispatch = self._dispatch_types()
        overlap = dispatch & set(EVENT_TYPES_WITHOUT_STATE_EFFECT)
        assert not overlap, (
            f"这些类型被声明为『无状态效果』却又写了重放分支: {sorted(overlap)}"
        )


class TestCliExitCode:
    """`ssot rebuild` 用 rebuild 做灾难恢复时，必须让人知道结果不完整。"""

    def _run(self, project: Path):
        import subprocess

        return subprocess.run(
            [sys.executable, str(SCRIPTS / "webnovel.py"),
             "ssot", "rebuild", "--project-root", str(project)],
            capture_output=True, text=True, timeout=180,
            cwd=str(SCRIPTS.parent),
        )

    def test_unknown_events_make_rebuild_exit_nonzero(self, project):
        publish_event(project, "future_plugin_event", {"a": 1}, chapter=2)
        r = self._run(project)
        assert r.returncode != 0, (
            "存在无法重放的事件时，rebuild 仍以 0 退出——"
            "脚本链会把它当成功，用户不知道状态不完整"
        )
        assert "future_plugin_event" in (r.stdout + r.stderr)

    def test_all_known_events_exit_zero(self, project):
        publish_event(project, "open_loop_created", {"content": "A"}, chapter=1)
        r = self._run(project)
        assert r.returncode == 0, f"已知事件不该失败：{r.stdout}{r.stderr}"


class TestVerifySurfacesUnknownEvents:
    """`ssot verify` 是用户检查一致性的入口，不能对缺数据的项目说"一致"。"""

    def test_verify_reports_warning(self, project):
        from data_modules.ssot_enforcer import verify_consistency

        publish_event(project, "future_plugin_event", {"a": 1}, chapter=2)
        drifts = verify_consistency(project)

        blob = str(drifts)
        assert "future_plugin_event" in blob, (
            f"verify_consistency 没有报告未知事件：{drifts}"
        )
        assert any(d.get("severity") in ("warning", "error") for d in drifts), (
            f"未知事件只以 info 报告，选项检查不会失败：{drifts}"
        )

    def test_verify_clean_project_has_no_unknown_warning(self, project):
        from data_modules.ssot_enforcer import verify_consistency

        publish_event(project, "open_loop_created", {"content": "A"}, chapter=1)
        drifts = verify_consistency(project)
        assert not any("unknown" in str(d).lower() or "无法重放" in str(d)
                       for d in drifts), f"已知事件被误报：{drifts}"

    def test_verify_cli_exit_code(self, project):
        """CLI 层面：有未知事件时 verify 必须非零退出。"""
        import subprocess

        publish_event(project, "future_plugin_event", {"a": 1}, chapter=2)
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "webnovel.py"),
             "ssot", "verify", "--project-root", str(project)],
            capture_output=True, text=True, timeout=180,
            cwd=str(SCRIPTS.parent),
        )
        assert r.returncode != 0, f"有未知事件但 verify 返回 0：{r.stdout}"
        assert "future_plugin_event" in r.stdout + r.stderr
