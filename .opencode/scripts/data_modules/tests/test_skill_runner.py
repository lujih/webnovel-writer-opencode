#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for skill_runner.py"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path


def _ensure_scripts_on_path() -> None:
    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

from skill_runner import (
    cmd_check_file,
    cmd_check_commit,
    cmd_check_index,
    cmd_check_batch_integrity,
    filter_structural_checks,
)


def test_check_file_exists():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        f = root / "test.md"
        f.write_text("hello", encoding="utf-8")
        assert cmd_check_file(str(f)) == 0


def test_check_file_missing():
    with tempfile.TemporaryDirectory() as td:
        assert cmd_check_file(str(Path(td) / "nope.md")) == 1


def test_check_file_empty():
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "empty.md"
        f.write_text("", encoding="utf-8")
        assert cmd_check_file(str(f)) == 1


def test_check_commit_exists():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        commits = root / ".story-system" / "commits"
        commits.mkdir(parents=True)
        (commits / "chapter_020.commit.json").write_text('{"status":"ok"}', encoding="utf-8")
        assert cmd_check_commit(str(root), 20) == 0


def test_check_commit_missing():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ".story-system" / "commits").mkdir(parents=True)
        assert cmd_check_commit(str(root), 20) == 1


def test_check_index_ok():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        webnovel = root / ".webnovel"
        webnovel.mkdir()
        db_path = webnovel / "index.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE chapters (chapter INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO chapters VALUES (20, 'test')")
        conn.commit()
        conn.close()
        assert cmd_check_index(str(root), 20) == 0


def test_check_index_missing():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        webnovel = root / ".webnovel"
        webnovel.mkdir()
        db_path = webnovel / "index.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE chapters (chapter INTEGER PRIMARY KEY, title TEXT)")
        conn.commit()
        conn.close()
        assert cmd_check_index(str(root), 20) == 1


def test_filter_structural_infra():
    result = {
        "chapter": 22,
        "passed": False,
        "checks": [
            {"name": "strand_balance", "passed": False, "severity": "blocking", "detail": "quest continuous 6 chapters", "fix": "switch"},
            {"name": "contract_coverage", "passed": False, "severity": "blocking", "detail": "missing chapter_0022.json", "fix": "run story-system"},
            {"name": "memory_bloat", "passed": True, "severity": "warning", "detail": "", "fix": ""},
        ],
    }
    filtered = filter_structural_checks(result)
    assert filtered["passed"] is False  # strand_balance still blocking
    contract_check = [c for c in filtered["checks"] if c["name"] == "contract_coverage"][0]
    assert contract_check["severity"] == "warning"
    assert contract_check["passed"] is True


def test_filter_structural_only_infra():
    """only infra issues: overall pass"""
    result = {
        "chapter": 22,
        "passed": False,
        "checks": [
            {"name": "contract_coverage", "passed": False, "severity": "blocking", "detail": "missing", "fix": "run story-system"},
        ],
    }
    filtered = filter_structural_checks(result)
    assert filtered["passed"] is True


def test_verify_chapter_files_ok(tmp_path):
    """验证全部文件存在且投影完成应返回 OK"""
    _ensure_scripts_on_path()
    from skill_runner import cmd_verify_chapter_files
    import argparse

    # Setup chapter file
    text_dir = tmp_path / "正文"
    text_dir.mkdir()
    (text_dir / "第001章-测试.md").write_text("测试内容", encoding="utf-8")

    # Setup commit with all projections done
    commits = tmp_path / ".story-system" / "commits"
    commits.mkdir(parents=True)
    commit = {
        "meta": {"chapter": 1, "status": "accepted"},
        "projection_status": {
            "state": "done", "index": "done", "summary": "done",
            "memory": "done", "vector": "skipped",
        },
    }
    (commits / "chapter_001.commit.json").write_text(
        json.dumps(commit, ensure_ascii=False), encoding="utf-8"
    )

    ns = argparse.Namespace(project_root=str(tmp_path), chapter=1)
    rc = cmd_verify_chapter_files(ns)
    assert rc == 0


def test_verify_chapter_files_missing_chapter(tmp_path):
    """章节文件缺失应返回 FAIL"""
    _ensure_scripts_on_path()
    from skill_runner import cmd_verify_chapter_files
    import argparse

    ns = argparse.Namespace(project_root=str(tmp_path), chapter=1)
    rc = cmd_verify_chapter_files(ns)
    assert rc == 1


def test_verify_chapter_files_no_substring_false_positive(tmp_path):
    """P1 回归：第11章 不得被 ch=1 误匹配（旧通配 * 会把子串 "1" 命中 "11"）"""
    _ensure_scripts_on_path()
    from skill_runner import cmd_verify_chapter_files
    import argparse

    text_dir = tmp_path / "正文"
    text_dir.mkdir()
    # 只有第11章、第011章，没有第1章
    (text_dir / "第011章-测试.md").write_text("x" * 10, encoding="utf-8")

    # ch=1 不应匹配到第011章（011 == 11 != 1）
    ns = argparse.Namespace(project_root=str(tmp_path), chapter=1)
    assert cmd_verify_chapter_files(ns) == 1  # 第1章缺失

    # ch=11 应匹配到第011章（0 填充 == 11）
    commits = tmp_path / ".story-system" / "commits"
    commits.mkdir(parents=True)
    (commits / "chapter_011.commit.json").write_text(
        json.dumps({"meta": {"chapter": 11}, "projection_status": {
            "state": "done", "index": "done", "summary": "done",
            "memory": "skipped", "vector": "skipped"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    ns11 = argparse.Namespace(project_root=str(tmp_path), chapter=11)
    assert cmd_verify_chapter_files(ns11) == 0


def test_pause_batch_running_to_paused(tmp_path):
    _ensure_scripts_on_path()
    from skill_runner import cmd_pause_batch
    import argparse

    webnovel = tmp_path / ".webnovel"
    webnovel.mkdir()
    state = {"status": "running", "current_chapter": 5, "completed_chapters": [3, 4]}
    (webnovel / "batch_state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )

    ns = argparse.Namespace(project_root=str(tmp_path))
    rc = cmd_pause_batch(ns)
    assert rc == 0

    updated = json.loads((webnovel / "batch_state.json").read_text("utf-8"))
    assert updated["status"] == "paused"
