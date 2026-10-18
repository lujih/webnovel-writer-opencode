#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""state.json 损坏时写路径必须 fail-closed。

背景（2026-10-18 深度审查发现，P0）：

`security_utils.read_json_safe(path, default={})` 在**文件存在但解析失败**时
返回 `{}` 并只在 stderr 打一行警告。它被 10 处调用，其中 8 处是**只读**路径
（context 装配、健康检查、publisher、memory store…），在那里 fail-open 是
正确的：降级为 `{}` 只会让本次输出缺失，不会写回磁盘。

但 `state_manager` 的两处是**写路径**：

- `_load_state()` —— 构造时载入；
- `save_state()` 的锁内重读 —— 读到的 `disk_state` 会被
  `_ensure_state_schema()` 补齐默认值后 `atomic_write_json()` **写回磁盘**。

在这两处 fail-open 意味着：state.json 一旦损坏（截断、半截写入、磁盘满、
编辑器崩溃），整本书的 `progress` / `entities_v3` / `plot_threads` 会先被读成
`{}`，再被 `_ensure_state_schema` 补成一个"看起来合法"的空骨架，然后覆盖
掉磁盘上那份损坏但可能还可抢救的真实数据。65 章进度、全部实体与伏笔一次清零，
且**没有任何报错**——命令以 0 退出，skill 的 `&&` 链继续推进。

修复方式：`_read_state_strict()` 只替换 state_manager 的两处调用，保留
`read_json_safe` 原行为（8 个只读调用点依赖它的容错）。损坏时先按
`atomic_write_json` 同量级的退避重试（Windows 上 VSCode watcher/杀软的瞬时
占用会读到半截文件，那是可自愈的），穷尽后备份现场并以 `CorruptStateError`
中止，CLI 打印抢救步骤、非零退出。

本文件钉住：损坏时**不写盘**、原文件字节未被改动、报文含可操作指引、
退出码非零；以及反面——**未损坏**时行为和以前完全一致（不能把正常路径改炸）。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS_DIR = REPO_ROOT / ".opencode" / "scripts"


@pytest.fixture
def project(tmp_path):
    from data_modules.config import DataModulesConfig

    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    return cfg


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class TestLoadStateIsFailClosed:
    def test_corrupt_state_raises_instead_of_silently_empty(self, project):
        """损坏的 state.json 必须抛错，而不是静默变成 {}。"""
        from data_modules.state_manager import CorruptStateError, StateManager

        project.state_file.write_text('{"progress": {"current_chapter": 65', encoding="utf-8")

        with pytest.raises(CorruptStateError) as ei:
            StateManager(project, enable_sqlite_sync=False)
        assert "已损坏" in str(ei.value)

    def test_valid_state_still_loads(self, project):
        """反面：正常文件必须照旧加载（别把正常路径改炸）。"""
        from data_modules.state_manager import StateManager

        project.state_file.write_text(json.dumps({
            "progress": {"current_chapter": 7, "total_words": 12345},
            "plot_threads": {"foreshadowing": [{"id": "f1"}]},
        }, ensure_ascii=False), encoding="utf-8")

        manager = StateManager(project, enable_sqlite_sync=False)
        assert manager.get_current_chapter() == 7
        assert manager._state["plot_threads"]["foreshadowing"] == [{"id": "f1"}]

    def test_missing_state_is_still_first_run(self, project):
        """文件不存在 = 首次运行，必须照旧返回空骨架，不能抛错。"""
        from data_modules.state_manager import StateManager

        assert not project.state_file.exists()
        manager = StateManager(project, enable_sqlite_sync=False)
        assert manager.get_current_chapter() == 0

    def test_non_object_toplevel_raises(self, project):
        """合法 JSON 但顶层是数组 → 同样不能当空状态处理。"""
        from data_modules.state_manager import CorruptStateError, StateManager

        project.state_file.write_text("[1, 2, 3]", encoding="utf-8")

        with pytest.raises(CorruptStateError) as ei:
            StateManager(project, enable_sqlite_sync=False)
        assert "顶层必须是 JSON 对象" in str(ei.value)


class TestSaveStateDoesNotClobber:
    def test_corrupt_disk_state_blocks_save_and_preserves_bytes(self, project):
        """核心用例：损坏时 save_state 必须中止，且原文件一个字节都不能变。

        旧行为是这里静默成功，把 65 章进度换成空骨架。
        """
        from data_modules.state_manager import CorruptStateError, EntityState, StateManager

        # 先把状态搞坏，模拟截断
        broken = '{"progress": {"current_chapter": 65}, "entities_v3": {'
        project.state_file.write_text(broken, encoding="utf-8")
        before = project.state_file.read_bytes()

        # 构造会被 _load_state 拦下——所以这里绕过构造，直接造实例来测
        # save_state 的锁内重读分支（它才是真正会写盘的那一步）。
        manager = StateManager.__new__(StateManager)
        manager.config = project
        manager._state = {}
        manager._lock_path = project.state_file.with_suffix(
            project.state_file.suffix + ".lock")
        manager._enable_sqlite_sync = False
        manager._sql_state_manager = None
        manager._pending_entity_patches = {}
        manager._pending_alias_entries = {}
        manager._pending_state_changes = []
        manager._pending_structured_relationships = []
        manager._pending_disambiguation_warnings = []
        manager._pending_disambiguation_pending = []
        manager._pending_chapter_meta = {}
        manager._pending_progress_chapter = 66
        manager._pending_progress_words_delta = 0
        manager._pending_chapter_status = {}
        manager._pending_sqlite_data = {}

        with pytest.raises(CorruptStateError):
            manager.save_state()

        assert project.state_file.read_bytes() == before, (
            "损坏的 state.json 被改写了——用户的原始数据已丢失")
        assert "current_chapter" in project.state_file.read_text(encoding="utf-8")

    def test_corrupt_file_backup_is_written(self, project):
        """抛错前必须先留现场，否则用户来不及抢救。"""
        from data_modules.state_manager import CorruptStateError, StateManager

        broken = '{"progress": {'
        project.state_file.write_text(broken, encoding="utf-8")

        with pytest.raises(CorruptStateError) as ei:
            StateManager(project, enable_sqlite_sync=False)

        backups = list(project.state_file.parent.glob("state.corrupt_*.json"))
        assert backups, f"没有生成损坏备份，报文自称有备份：{ei.value}"
        assert backups[0].read_text(encoding="utf-8") == broken
        # 报文里给出的路径必须是真实存在的那个
        assert backups[0].name in str(ei.value)


class TestCliSurfacesCorruptState:
    def test_cli_exits_nonzero_with_actionable_message(self, project):
        """命令行必须非零退出，并把抢救步骤打出来（而不是 traceback）。"""
        project.state_file.write_text('{"progress": ', encoding="utf-8")

        r = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "webnovel.py"), "state",
             "get-progress", "--project-root", str(project.project_root)],
            capture_output=True, text=True, timeout=120,
            cwd=str(project.project_root),
        )
        combined = r.stdout + r.stderr
        assert r.returncode != 0, "state.json 损坏却成功退出——skill 的 && 链会继续推进"
        assert "STATE_CORRUPT" in combined, f"没有结构化错误码：{combined[-600:]}"
        assert "Traceback" not in combined, f"以 traceback 收场：{combined[-600:]}"
        assert "ssot rebuild" in combined, "没告诉用户怎么重建"
        assert "state.corrupt_" in combined, "没告诉用户备份在哪"


class TestReadJsonSafeUntouched:
    def test_read_json_safe_still_fails_open(self, tmp_path):
        """只读路径依赖 read_json_safe 的容错，**不能**顺手把它改严格。"""
        from security_utils import read_json_safe

        target = tmp_path / "x.json"
        target.write_text("{broken", encoding="utf-8")
        assert read_json_safe(target, default={}) == {}, (
            "read_json_safe 被改成 fail-closed 了——context 装配/健康检查/"
            "publisher 等 8 个只读调用点会在状态损坏时直接崩掉")

    def test_read_json_safe_missing_returns_default(self, tmp_path):
        from security_utils import read_json_safe

        assert read_json_safe(tmp_path / "nope.json", default={"a": 1}) == {"a": 1}
