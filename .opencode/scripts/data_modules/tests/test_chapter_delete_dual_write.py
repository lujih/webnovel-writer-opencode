#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删除章节的双写一致性。

`chapter_delete_service.cmd_delete_chapters()` 顺序是：

    1. unlink 章节文件
    2. _clean_state_json()      ← 改 state.json（删 chapter_status、回退 current_chapter）
    3. _clean_memory()
    4. publish_event("chapter_deleted")   ← 包在 try/except: pass 里

第 4 步失败时异常被静默吞掉，而第 2 步已经落盘。结果是：

    state.json  : 第 5 章已删除
    事件日志    : 没有 chapter_deleted

而 SSOT 的既定语义是「事件日志是不可变真相，state.json 是它的投影」。
于是下一次 `ssot rebuild` 按事件重放，会把已删除的第 5 章**复活**：

    $ webnovel.py delete-chapters 5          # 第 4 步偶发失败（磁盘满/锁超时）
      Deleted 1 chapter(s).
    $ webnovel.py ssot rebuild
      Rebuilt state.json: ... → 5 chapters    ← 第 5 章回来了

这是 SSOT 架构里最不该出现的失败模式：用户的删除被静默回滚，而且是在
**另一个命令**里发生的，与删除操作毫无关联，极难归因。

本文件钉住：事件必须成为删除的事实来源，或者至少在写事件失败时
**不要让 state 单方面提前落盘**。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import data_modules.chapter_delete_service as svc  # noqa: E402
from data_modules.ssot_enforcer import read_events, rebuild_state_json  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / "正文").mkdir()
    for n in (1, 2, 5):
        (tmp_path / "正文" / f"第{n:04d}章.md").write_text("正文" * 60, encoding="utf-8")
    state = {
        "progress": {
            "current_chapter": 5,
            "chapter_status": {
                "1": "chapter_committed",
                "2": "chapter_committed",
                "5": "chapter_committed",
            },
        }
    }
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps(state), encoding="utf-8"
    )
    return tmp_path


class TestNormalDeletion:
    """正常路径：state 与事件都更新。"""

    def test_state_is_cleaned(self, project):
        svc._clean_state_json(project, [5], dry_run=False)
        state = json.loads((project / ".webnovel" / "state.json").read_text(encoding="utf-8"))
        assert "5" not in state["progress"]["chapter_status"]
        assert state["progress"]["current_chapter"] == 2

    def test_dry_run_does_not_touch_state(self, project):
        before = (project / ".webnovel" / "state.json").read_text(encoding="utf-8")
        svc._clean_state_json(project, [5], dry_run=True)
        after = (project / ".webnovel" / "state.json").read_text(encoding="utf-8")
        assert before == after


class TestEventFailureMustNotLeaveStateAhead:
    """核心：事件写失败时，不能让 state 单方面领先于事件日志。"""

    def _run_with_failing_publish(self, project, monkeypatch):
        monkeypatch.setattr(
            svc, "publish_event",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("event log down")),
        )
        # 走完整命令路径（含 unlink / clean / publish）
        class Args:
            chapters = "5"
            project_root = str(project)
            dry_run = False
        return svc.cmd_delete_chapters(Args())

    def test_state_not_modified_when_event_fails(self, project, monkeypatch):
        before = json.loads((project / ".webnovel" / "state.json").read_text(encoding="utf-8"))
        self._run_with_failing_publish(project, monkeypatch)
        after = json.loads((project / ".webnovel" / "state.json").read_text(encoding="utf-8"))

        events = read_events(project, event_type="chapter_deleted")
        if not events:
            assert before == after, (
                "事件日志里没有 chapter_deleted，但 state.json 已经被改写。"
                "下一次 ssot rebuild 会按事件重放，把删掉的章节复活。"
            )

    def test_rebuild_after_normal_delete_consistent(self, project, monkeypatch):
        """正常删除（事件成功写入）后 rebuild，状态应保持一致。"""
        class Args:
            chapters = "5"
            project_root = str(project)
            dry_run = False
        svc.cmd_delete_chapters(Args())

        events = read_events(project, event_type="chapter_deleted")
        assert events, "正常删除应写入 chapter_deleted 事件"

        # rebuild 会从空状态重放所有事件；由于删除事件存在，
        # 第 5 章不应出现在 chapter_status 里
        rebuilt = rebuild_state_json(project)
        assert "5" not in (rebuilt.get("progress", {}).get("chapter_status") or {})

    def test_command_reports_failure_to_caller(self, project, monkeypatch):
        """事件写失败时命令不能返回 0——那等于告诉 skill『删干净了』。"""
        rc = self._run_with_failing_publish(project, monkeypatch)
        assert rc != 0, (
            "publish_event 失败但命令返回 0；调用方（skill）会认为删除完整成功"
        )
