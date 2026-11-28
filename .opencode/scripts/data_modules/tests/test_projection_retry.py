#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`projections retry --chapter N`：重跑单章投影。

**为什么这个命令值得存在**：`write-guard.js` 的 `ALLOWED_MARKERS` 里早就写着
`projections retry` / `projections replay`，`run-log` 也会告诉你「哪个 writer
失败」——但**命令从未实现**。作者拿到「失败」之后唯一的出路是 `ssot rebuild`
（重建全部投影），代价远大于重跑一章。这是本仓库反复出现的病灶：声明了但不存在。

钉住四件事：
1. 重跑幂等——**跑两次不会在不可变事件日志里写入重复事件**（`apply_projections`
   内部的 retry guard 按「该章已有事件」跳过 `publish_event`）；
2. 投影结果会落回 commit 文件（`projection_status` 更新）；
3. 跳过 precommit gate——gate 检查的是「能否入库」，retry 处理的是已在库里的产物；
4. 不存在的章 / 状态不可投影 / 文件损坏分别给出可区分的错误。
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

from data_modules.chapter_commit_service import ChapterCommitService  # noqa: E402


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".story-system" / "commits").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"progress": {"current_chapter": 1}}), encoding="utf-8")
    return tmp_path


def _commit(root: Path, chapter: int, *, status: str = "accepted") -> Path:
    payload = {
        "meta": {"chapter": chapter, "status": status},
        "state_deltas": [{"entity_id": "a", "field": "realm", "old": "斗者", "new": "斗师"}],
        "accepted_events": [{
            # `StoryEvent` 要求 event_id / chapter / event_type / subject 齐全，
            # 缺一个就在 normalize_events 阶段抛 ValidationError。
            "event_id": f"ch{chapter:03d}-evt-1",
            "chapter": chapter,
            "event_type": "power_breakthrough",
            "subject": "a",
            "payload": {"new_realm": "斗师"},
        }] if status == "accepted" else [],
        "review_result": {"issues": [], "overall_score": 90},
        "projection_status": {},
    }
    path = root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _events(root: Path) -> list:
    """读 SSOT 事件日志（按行 JSON 或按文件都兼容）。"""
    log = root / ".story-system" / "events" / "events.jsonl"
    if log.is_file():
        return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    directory = root / ".story-system" / "events"
    if not directory.is_dir():
        return []
    out = []
    for path in sorted(directory.glob("*.event.json")):
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


class TestRetry:
    def test_updates_projection_status(self, tmp_path):
        root = _project(tmp_path)
        path = _commit(root, 1)
        result = ChapterCommitService(root).retry_projections(1)
        assert result["chapter"] == 1
        assert result["status"] == "accepted"
        assert result["ok"] is True, result
        assert result["after"], "至少要跑过 state 等 writer"
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["projection_status"] == result["after"], "结果必须落回 commit 文件"

    def test_is_idempotent_for_event_log(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1)
        service = ChapterCommitService(root)
        service.retry_projections(1)
        first = len(_events(root))
        service.retry_projections(1)
        second = len(_events(root))
        assert first == second, "重跑不得在不可变事件日志里追加重复事件"

    def test_rejected_chapter_can_be_retried(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 2, status="rejected")
        result = ChapterCommitService(root).retry_projections(2)
        assert result["status"] == "rejected"

    def test_missing_commit_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ChapterCommitService(_project(tmp_path)).retry_projections(9)

    def test_broken_commit_raises_value_error(self, tmp_path):
        root = _project(tmp_path)
        (root / ".story-system" / "commits" / "chapter_003.commit.json").write_text(
            "{not json", encoding="utf-8")
        with pytest.raises(ValueError):
            ChapterCommitService(root).retry_projections(3)

    def test_non_projectable_status_raises(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 4, status="draft")
        with pytest.raises(ValueError):
            ChapterCommitService(root).retry_projections(4)


class TestCli:
    def _run(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "webnovel.py"), "projections", "retry",
             "--project-root", str(root), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=180, env=env,
        )

    def test_retry_succeeds(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1)
        result = self._run(root, "--chapter", "1")
        assert result.returncode == 0, result.stderr
        assert "第 1 章" in result.stdout

    def test_missing_chapter_returns_nonzero(self, tmp_path):
        result = self._run(_project(tmp_path), "--chapter", "7")
        assert result.returncode != 0
        assert "commit 记录" in result.stderr

    def test_json_output(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1)
        result = self._run(root, "--chapter", "1", "--json")
        payload = json.loads(result.stdout)
        assert payload["chapter"] == 1
        assert "after" in payload
