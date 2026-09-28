"""process-chapter 必须在 SQLite 同步失败时报错并非零退出（上游 3af192d）。

`save_state()` 返回的 `sqlite_sync_ok` 此前从未被任何调用点消费：state.json
已写盘、SQLite 侧 pending 已恢复成快照（数据没丢），但 CLI 仍然报
「chapter_processed」成功。调用方据此以为章节已入库，而 dashboard / RAG
读的是 SQLite——那一章就是不存在。静默成功比报错更难排查。
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
ENTRY = REPO_ROOT / ".opencode" / "scripts" / "webnovel.py"


def _write_project(root: Path) -> None:
    """最小可跑项目：state.json。"""
    (root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (root / ".webnovel" / "state.json").write_text(
        '{"progress": {"current_chapter": 0, "chapter_status": {}}, "entities_v3": {}}',
        encoding="utf-8",
    )


# data-agent 的处理结果，形状取自 process-chapter 的 Pydantic 校验
PAYLOAD = json.dumps({
    "chapter": 1,
    "entities": [],
    "state_changes": [],
    "structured_relationships": [],
}, ensure_ascii=False)


def _run(root: Path, *args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(ENTRY), "--project-root", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", cwd=str(REPO_ROOT),
    )


class TestProcessChapterSqliteSync:
    def test_exit_zero_on_healthy_sqlite(self, tmp_path):
        """基线：一切正常时仍应成功（防止把检查写成永远失败）。"""
        _write_project(tmp_path)
        r = _run(tmp_path, "state", "process-chapter", "--chapter", "1", "--data", PAYLOAD)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "chapter_processed" in r.stdout

    def test_nonzero_exit_and_error_when_sqlite_sync_fails(self, tmp_path, monkeypatch):
        """SQLite 同步失败 → 不得报成功，必须非零退出。"""
        import data_modules.state_manager as sm

        real = sm.StateManager.save_state

        def broken(self):
            # 保留真实写盘，只让 SQLite 同步失败
            result = real(self)
            return {"saved": True, "sqlite_sync_ok": False}

        monkeypatch.setattr(sm.StateManager, "save_state", broken)

        # 直接调用 main()，避免跨进程时 monkeypatch 不生效
        from data_modules import cli_output
        from data_modules.state_manager import main

        _write_project(tmp_path)
        monkeypatch.setattr(sys, "argv", [
            "state_manager", "--project-root", str(tmp_path),
            "process-chapter", "--chapter", "1", "--data", PAYLOAD,
        ])
        cli_output.reset_error_state()
        try:
            main()
        except SystemExit:
            pass

        assert cli_output.has_error(), "sqlite 同步失败却没有记录错误"
        err = cli_output.get_last_error() or {}
        assert err.get("code") == "SQLITE_SYNC_FAILED"

    def test_save_state_result_shape_preserved(self, tmp_path):
        """save_state 的返回契约不得被改动。"""
        from data_modules.config import DataModulesConfig
        from data_modules.state_manager import StateManager

        cfg = DataModulesConfig.from_project_root(tmp_path)
        cfg.ensure_dirs()
        mgr = StateManager(cfg)
        mgr._load_state()
        mgr.set_chapter_status(1, "chapter_committed")
        mgr._pending_progress_words_delta = 120
        result = mgr.save_state()

        assert set(result) == {"saved", "sqlite_sync_ok"}, f"返回契约变了: {result}"
        assert result["saved"] is True
        assert result["sqlite_sync_ok"] is True
