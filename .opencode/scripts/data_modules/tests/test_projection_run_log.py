#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""投影运行记录的作者可见视图（G6 的最小可交付子集）。

外部依据：NarraLume 的 run center——长任务的步骤、错误、可恢复动作摆在同一个
视图里，作者不必读日志猜"刚才哪里失败了"。

本项目现状（本轮前）：`projection_log` 已被 `chapter_commit_service` 写入、
被 `postcommit` gate 与 `project_phase` 读取，但**没有任何作者可见入口**。
投影失败时作者只看到"某个阶段报错"，不知道是哪一章、哪个 writer、下一步做什么。

本文件钉住：
1. 摘要按「新 → 旧」排列，`limit` / `chapter` 过滤生效；
2. 失败 writer 与未完成 writer 分开标注（`failed:xxx` 与 `pending` 是两回事）；
3. 非 ok 的行必须带**真实存在**的恢复命令——建议不存在的命令比不给更糟；
4. `append_projection_run` 的写入异常不再被 `except (ImportError, Exception)`
   吞掉（修复前它把磁盘满/权限不足当成"锁不可用"，再用一次写入掩盖真因）；
5. CLI `run-log list` 端到端可用。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.projection_log import (  # noqa: E402
    recover_action,
    append_projection_run,
    format_runs,
    projection_log_path,
    summarize_runs,
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    # CLI 层的 _resolve_root 要求 `.webnovel/state.json` 存在（书项目判据），
    # 纯 Python 用例不需要，但端到端用例必须给。
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"progress": {}}), encoding="utf-8")
    return tmp_path


def _run(root: Path, chapter: int, writers: dict, status: str = "accepted") -> None:
    append_projection_run(
        root,
        {"meta": {"chapter": chapter, "status": status}, "projection_status": {}},
        writers,
    )


class TestSummarize:
    def test_empty(self, tmp_path):
        assert summarize_runs(_project(tmp_path)) == []
        assert "没有投影运行记录" in format_runs([])

    def test_all_ok(self, tmp_path):
        root = _project(tmp_path)
        _run(root, 1, {"state": {"status": "done"}, "index": {"status": "done"}})
        rows = summarize_runs(root)
        assert len(rows) == 1
        assert rows[0]["ok"] is True
        assert rows[0]["failed_writers"] == []
        assert rows[0]["action"] == ""

    def test_failed_writer_flagged_with_action(self, tmp_path):
        root = _project(tmp_path)
        _run(root, 2, {
            "state": {"status": "done"},
            "index": {"status": "failed:sqlite_locked"},
        })
        row = summarize_runs(root)[0]
        assert row["ok"] is False
        assert row["failed_writers"] == ["index"]
        assert row["action"] == recover_action(2)
        text = format_runs([row])
        assert "❌" in text and "index" in text
        assert "projections retry --chapter 2" in text, "恢复建议必须是真实存在的命令，且要指名章节"

    def test_pending_writer_is_not_failure(self, tmp_path):
        root = _project(tmp_path)
        _run(root, 3, {"state": {"status": "done"}, "index": {"status": "pending"}})
        row = summarize_runs(root)[0]
        assert row["ok"] is False
        assert row["failed_writers"] == []
        assert row["pending_writers"] == ["index"]
        assert "⚠️" in format_runs([row])

    def test_newest_first_and_limit(self, tmp_path):
        root = _project(tmp_path)
        for chapter in range(1, 6):
            _run(root, chapter, {"state": {"status": "done"}})
        rows = summarize_runs(root, limit=2)
        assert [r["chapter"] for r in rows] == [5, 4]

    def test_chapter_filter(self, tmp_path):
        root = _project(tmp_path)
        _run(root, 1, {"state": {"status": "done"}})
        _run(root, 2, {"state": {"status": "done"}})
        rows = summarize_runs(root, chapter=2)
        assert [r["chapter"] for r in rows] == [2]

    def test_bad_lines_ignored(self, tmp_path):
        root = _project(tmp_path)
        _run(root, 1, {"state": {"status": "done"}})
        path = projection_log_path(root)
        with path.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n\n")
        assert len(summarize_runs(root)) == 1


class TestAppendDoesNotSwallowErrors:
    def test_write_error_propagates(self, tmp_path, monkeypatch):
        """磁盘/权限错误必须冒出去，不能被"锁失败降级"掩盖。"""
        root = _project(tmp_path)
        path = projection_log_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)

        real_open = Path.open

        def boom(self, *args, **kwargs):  # noqa: ANN001
            if self == path:
                raise OSError("disk full")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", boom)
        with pytest.raises(OSError):
            append_projection_run(root, {"meta": {"chapter": 1, "status": "accepted"}}, {})


class TestCli:
    def _run(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "webnovel.py"), "run-log", "list",
             "--project-root", str(root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120, env=env,
        )

    def test_lists_runs(self, tmp_path):
        root = _project(tmp_path)
        _run(root, 7, {"state": {"status": "done"}, "memory": {"status": "failed:x"}})
        result = self._run(root)
        assert result.returncode == 0, result.stderr
        assert "第007章" in result.stdout
        assert "memory" in result.stdout
        assert "projections retry" in result.stdout

    def test_json_output(self, tmp_path):
        root = _project(tmp_path)
        _run(root, 1, {"state": {"status": "done"}})
        result = self._run(root, "--json")
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload[0]["chapter"] == 1
        assert payload[0]["ok"] is True

    def test_empty_project(self, tmp_path):
        root = _project(tmp_path)
        result = self._run(root)
        assert result.returncode == 0
        assert "没有投影运行记录" in result.stdout
