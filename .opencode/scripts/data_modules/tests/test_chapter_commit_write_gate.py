#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""precommit 门禁 → `WriteGateRejected` 的接线测试。

审计背景：`persist_commit` 里的 ``raise WriteGateRejected(...)`` 是全仓
**唯一**会拒绝提交的代码路径，而 168 个测试文件对它零引用 ——
`test_write_gates.py` 只测三个 gate 函数本身，`test_write_gates_wiring.py`
只做源码字符串断言。结果是「把三个 `_check_*` 改成永远返回 `[]`」这件事
在测试上完全不可见，门禁可以静默失效而全部变绿。

本文件钉住四件事：
1. gate 不 ok → 抛 `WriteGateRejected`，**且 commit 文件不落盘**；
2. gate 报告被写进 `payload["write_gates"]["precommit"]`（事后可审计）；
3. gate ok → 正常落盘，且不写 `write_gates`（别把空报告当证据留下）；
4. rejected 章节**不经过**门禁 —— rejected 的产物本就可能是残缺的，
   那正是被否的原因，拦它没有意义。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from data_modules.chapter_commit_service import ChapterCommitService, WriteGateRejected


def _make_project(tmp_path: Path) -> Path:
    """构造 `ChapterCommitService` 能接受的最小项目根（只要求 state.json）。"""
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _payload(chapter: int = 1, status: str = "accepted") -> dict:
    return {
        "meta": {"chapter": chapter, "status": status},
        "review": {"issues": [], "blocking_count": 0},
        "projection_status": {
            "state": "pending", "index": "pending", "summary": "pending",
            "memory": "pending", "vector": "pending",
        },
    }


def _gate(ok: bool, code: str = "chapter_file_missing") -> dict:
    return {
        "schema_version": "write-gate/v1",
        "stage": "precommit",
        "ok": ok,
        "errors": [] if ok else [{
            "code": code,
            "severity": "error",
            "message": "第 1 章文件不存在",
        }],
        "warnings": [],
        "error_count": 0 if ok else 1,
        "warning_count": 0,
    }


class TestPrecommitGateWiring:
    def test_raises_when_gate_not_ok(self, tmp_path, monkeypatch):
        root = _make_project(tmp_path)
        monkeypatch.setattr(
            "data_modules.chapter_commit_service.run_write_gate",
            lambda stage, project_root, chapter: _gate(False),
        )
        service = ChapterCommitService(root)
        payload = _payload()

        with pytest.raises(WriteGateRejected) as excinfo:
            service.persist_commit(payload)

        # 异常要能被人读懂：带上 gate 的 code 与 message
        assert "第 1 章文件不存在" in str(excinfo.value)
        assert "chapter_file_missing" in str(excinfo.value)
        # 结构化 gate 挂在异常上，CLI 才能原样转达修复建议
        assert excinfo.value.gate["ok"] is False
        assert excinfo.value.gate["stage"] == "precommit"

    def test_blocked_commit_does_not_touch_disk(self, tmp_path, monkeypatch):
        root = _make_project(tmp_path)
        monkeypatch.setattr(
            "data_modules.chapter_commit_service.run_write_gate",
            lambda stage, project_root, chapter: _gate(False),
        )
        service = ChapterCommitService(root)

        with pytest.raises(WriteGateRejected):
            service.persist_commit(_payload(chapter=3))

        # 门禁的意义就是「不落盘」：commits/ 目录都不该被建出来
        assert not (root / ".story-system" / "commits" / "chapter_003.commit.json").exists()

    def test_gate_report_embedded_for_audit(self, tmp_path, monkeypatch):
        root = _make_project(tmp_path)
        monkeypatch.setattr(
            "data_modules.chapter_commit_service.run_write_gate",
            lambda stage, project_root, chapter: _gate(False, code="chapter_file_empty"),
        )
        service = ChapterCommitService(root)
        payload = _payload()

        with pytest.raises(WriteGateRejected):
            service.persist_commit(payload)

        embedded = payload.get("write_gates", {}).get("precommit")
        assert embedded is not None, "被拦下的 payload 应留下 gate 报告，便于事后追责"
        assert embedded["errors"][0]["code"] == "chapter_file_empty"

    def test_accepts_when_gate_ok(self, tmp_path, monkeypatch):
        root = _make_project(tmp_path)
        called = []

        def _ok_gate(stage, project_root, chapter):
            called.append((stage, chapter))
            return _gate(True)

        monkeypatch.setattr(
            "data_modules.chapter_commit_service.run_write_gate", _ok_gate,
        )
        service = ChapterCommitService(root)
        payload = _payload(chapter=2)

        path = service.persist_commit(payload)

        assert path.is_file()
        assert called == [("precommit", 2)], "accepted 章节必须真的过一遍 precommit gate"
        written = json.loads(path.read_text(encoding="utf-8"))
        assert written["meta"]["chapter"] == 2
        # ok 的 gate 不该在 payload 里留痕（只有被拦下才需要证据）
        assert "write_gates" not in written

    def test_rejected_chapter_skips_gate(self, tmp_path, monkeypatch):
        root = _make_project(tmp_path)
        called = []

        def _should_not_run(stage, project_root, chapter):
            called.append(stage)
            return _gate(False)

        monkeypatch.setattr(
            "data_modules.chapter_commit_service.run_write_gate", _should_not_run,
        )
        service = ChapterCommitService(root)

        path = service.persist_commit(_payload(chapter=4, status="rejected"))

        assert called == [], "rejected 章节的产物本就残缺，不该再被 precommit gate 拦"
        assert path.is_file()

    def test_exception_type_is_not_plain_value_error(self):
        """调用方靠类型区分「门禁拦下」与「代码 bug」，不能被并进 ValueError。"""
        exc = WriteGateRejected("boom", gate={"ok": False})
        assert isinstance(exc, RuntimeError)
        assert not isinstance(exc, ValueError)
        assert exc.gate == {"ok": False}
        # 无 gate 入参时也要能构造（CLI 兜底路径）
        assert WriteGateRejected("boom").gate == {}
