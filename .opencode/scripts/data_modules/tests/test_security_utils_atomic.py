#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""atomic_write_json 对 Windows WinError 5（目标被瞬时占用）的退避重试测试。

上游 lingfengQAQ/webnovel-writer issue #125（commit f75e913）。
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

import security_utils
from security_utils import AtomicWriteError, atomic_write_json, read_json_safe


def test_atomic_write_retries_transient_permission_error(tmp_path, monkeypatch):
    """瞬时占用（WinError 5）在退避重试窗口内自愈——issue #125 主场景。"""
    target = tmp_path / "memory_scratchpad.json"
    target.write_text('{"old": true}', encoding="utf-8")

    real_replace = security_utils.os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(5, "拒绝访问。")
        return real_replace(src, dst)

    sleeps: list[float] = []
    monkeypatch.setattr(security_utils.os, "replace", flaky_replace)
    monkeypatch.setattr(security_utils.time, "sleep", sleeps.append)

    atomic_write_json(target, {"new": 1}, use_lock=False, backup=False)

    assert read_json_safe(target) == {"new": 1}
    assert calls["n"] == 3
    assert sleeps == [0.02, 0.04]  # 指数退避
    assert not list(tmp_path.glob("*.tmp"))  # 成功后无临时文件残留


def test_atomic_write_raises_when_target_stays_locked(tmp_path, monkeypatch):
    """持续占用：重试穷尽后如实抛 AtomicWriteError（生产模式），原文件不被破坏。"""
    monkeypatch.delenv("WEBNOVEL_TEST_RELAX_ATOMIC_REPLACE", raising=False)
    target = tmp_path / "state.json"
    target.write_text("{}", encoding="utf-8")

    def always_denied(src, dst):
        raise PermissionError(5, "拒绝访问。")

    monkeypatch.setattr(security_utils.os, "replace", always_denied)
    monkeypatch.setattr(security_utils.time, "sleep", lambda _s: None)

    with pytest.raises(AtomicWriteError):
        atomic_write_json(target, {"x": 1}, use_lock=False, backup=False)

    assert read_json_safe(target) == {}
    assert not list(tmp_path.glob("*.tmp"))  # 失败路径清理了临时文件


def test_atomic_write_relaxed_fallback_still_writes(tmp_path, monkeypatch):
    """测试沙箱降级分支（WEBNOVEL_TEST_RELAX_ATOMIC_REPLACE=1）行为保持：穷尽后覆写成功。"""
    monkeypatch.setenv("WEBNOVEL_TEST_RELAX_ATOMIC_REPLACE", "1")
    target = tmp_path / "state.json"
    target.write_text("{}", encoding="utf-8")

    def always_denied(src, dst):
        raise PermissionError(5, "拒绝访问。")

    monkeypatch.setattr(security_utils.os, "replace", always_denied)
    monkeypatch.setattr(security_utils.time, "sleep", lambda _s: None)

    atomic_write_json(target, {"x": 1}, use_lock=False, backup=False)

    assert read_json_safe(target) == {"x": 1}


@pytest.mark.skipif(sys.platform != "win32", reason="Windows 独有的 replace 共享冲突")
def test_atomic_write_survives_real_windows_file_hold(tmp_path):
    """真实复现 issue #125：另一线程 open 持有目标文件（无 FILE_SHARE_DELETE），
    句柄释放前 os.replace 报 WinError 5，退避重试窗口内自愈。"""
    target = tmp_path / "memory_scratchpad.json"
    target.write_text('{"old": true}', encoding="utf-8")

    opened = threading.Event()

    def hold():
        with open(target, "r", encoding="utf-8"):
            opened.set()
            time.sleep(0.15)

    t = threading.Thread(target=hold)
    t.start()
    try:
        assert opened.wait(timeout=2)
        atomic_write_json(target, {"new": 1}, use_lock=False, backup=False)
    finally:
        t.join()

    assert read_json_safe(target) == {"new": 1}
