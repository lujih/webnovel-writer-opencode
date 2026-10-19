#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""write_gates 测试。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from data_modules.project_phase import INIT_REQUIRED_DIRS, INIT_REQUIRED_FILES
from data_modules.write_gates import (
    format_gate_report,
    gate_report,
    issue,
    run_write_gate,
)


def _make_init_ready(project_root: Path) -> None:
    """建出 ``init`` 阶段要求的最小脚手架。

    prewrite 的 ``_check_project_phase`` 会对照 INIT_REQUIRED_DIRS/FILES 判
    ``init_scaffolded`` 并阻断。此前该检查每次都抛 TypeError 又被
    ``except Exception: pass`` 吞掉，所以下面这些 fixture 只造了 state.json
    也照样"通过"——测的一直只是合同检查那一条。修好 fail-closed 之后，
    这些 fixture 必须真的满足 init 阶段，否则测的就不是「合同齐全」。
    """
    for rel in INIT_REQUIRED_DIRS:
        (project_root / rel).mkdir(parents=True, exist_ok=True)
    for rel in INIT_REQUIRED_FILES:
        path = project_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text("{}" if rel.endswith(".json") else "placeholder\n",
                            encoding="utf-8")


class TestIssue:
    def test_basic_fields(self):
        i = issue("test_code", "error", "test message")
        assert i["code"] == "test_code"
        assert i["severity"] == "error"
        assert i["message"] == "test message"

    def test_optional_fields(self):
        i = issue("test", "warning", "msg", impact="high", repair="fix it")
        assert i["impact"] == "high"
        assert i["repair"] == "fix it"


class TestGateReport:
    def test_ok_when_no_errors(self):
        report = gate_report("prewrite", [], [])
        assert report["ok"] is True
        assert report["stage"] == "prewrite"

    def test_not_ok_when_errors(self):
        errors = [issue("test", "error", "fail")]
        report = gate_report("precommit", errors, [])
        assert report["ok"] is False
        assert report["error_count"] == 1

    def test_warnings_only(self):
        warnings = [issue("test", "warning", "warn")]
        report = gate_report("postcommit", [], warnings)
        assert report["ok"] is True
        assert report["warning_count"] == 1


class TestFormatGateReport:
    def test_json_format(self):
        report = gate_report("prewrite", [], [])
        output = format_gate_report(report, "json")
        parsed = json.loads(output)
        assert parsed["stage"] == "prewrite"

    def test_text_format(self):
        report = gate_report("prewrite", [], [])
        output = format_gate_report(report, "text")
        assert "prewrite" in output
        assert "通过" in output

    def test_text_with_errors(self):
        errors = [issue("test_code", "error", "test message", repair="fix it")]
        report = gate_report("precommit", errors, [])
        output = format_gate_report(report, "text")
        assert "test_code" in output
        assert "fix it" in output


class TestRunWriteGate:
    def test_unknown_stage(self):
        result = run_write_gate("unknown", Path("/tmp"), 1)
        assert result["ok"] is False
        assert any(e["code"] == "unknown_stage" for e in result["errors"])

    def test_prewrite_no_project(self, tmp_path):
        """项目不存在时应报 no_project 错误。"""
        result = run_write_gate("prewrite", tmp_path, 1)
        assert result["ok"] is False
        assert any(e["code"] == "no_project" for e in result["errors"])

    def test_prewrite_missing_contracts(self, tmp_path):
        """项目存在但缺少合同时应报 missing_master_setting 错误。"""
        # 建出 init 脚手架（phase 检查才放行），但**不**建合同文件
        _make_init_ready(tmp_path)
        result = run_write_gate("prewrite", tmp_path, 1)
        assert result["ok"] is False
        assert any(e["code"] == "missing_master_setting" for e in result["errors"])

    def test_prewrite_with_contracts(self, tmp_path):
        # init 脚手架 + 合同齐全
        _make_init_ready(tmp_path)
        story_dir = tmp_path / ".story-system"
        story_dir.mkdir()
        (story_dir / "MASTER_SETTING.json").write_text("{}", encoding="utf-8")
        chapters_dir = story_dir / "chapters"
        chapters_dir.mkdir()
        (chapters_dir / "chapter_001.json").write_text("{}", encoding="utf-8")

        result = run_write_gate("prewrite", tmp_path, 1)
        assert result["ok"] is True

    def test_postcommit_missing_commit_file(self, tmp_path):
        """commit 文件不存在时应报错。"""
        result = run_write_gate("postcommit", tmp_path, 1)
        assert result["ok"] is False
        assert any(e["code"] == "commit_file_missing" for e in result["errors"])

    def test_postcommit_valid_commit(self, tmp_path):
        """有效 commit 且投影完整时应通过。"""
        commits_dir = tmp_path / ".story-system" / "commits"
        commits_dir.mkdir(parents=True)
        commit = {
            "meta": {"chapter": 1, "status": "accepted"},
            "projection_status": {
                "state": "done", "index": "done", "summary": "done",
                "memory": "done", "vector": "done",
            },
        }
        (commits_dir / "chapter_001.commit.json").write_text(json.dumps(commit), encoding="utf-8")
        result = run_write_gate("postcommit", tmp_path, 1)
        assert result["ok"] is True

    def test_postcommit_failed_projection(self, tmp_path):
        """projection 失败时应报错。"""
        commits_dir = tmp_path / ".story-system" / "commits"
        commits_dir.mkdir(parents=True)
        commit = {
            "meta": {"chapter": 1, "status": "accepted"},
            "projection_status": {
                "state": "done", "index": "failed:sqlite_error", "summary": "done",
                "memory": "done", "vector": "done",
            },
        }
        (commits_dir / "chapter_001.commit.json").write_text(json.dumps(commit), encoding="utf-8")
        result = run_write_gate("postcommit", tmp_path, 1)
        assert result["ok"] is False
        assert any(e["code"] == "projection_failed" for e in result["errors"])

    def test_postcommit_missing_projection_writer(self, tmp_path):
        """缺少 projection writer 时应报错。"""
        commits_dir = tmp_path / ".story-system" / "commits"
        commits_dir.mkdir(parents=True)
        commit = {
            "meta": {"chapter": 1, "status": "accepted"},
            "projection_status": {
                "state": "done", "index": "done", "summary": "done",
                "memory": "done",
                # vector 缺失
            },
        }
        (commits_dir / "chapter_001.commit.json").write_text(json.dumps(commit), encoding="utf-8")
        result = run_write_gate("postcommit", tmp_path, 1)
        assert result["ok"] is False
        assert any(e["code"] == "projection_missing" for e in result["errors"])

    def test_postcommit_pending_projection(self, tmp_path):
        """pending projection 应报 warning。"""
        commits_dir = tmp_path / ".story-system" / "commits"
        commits_dir.mkdir(parents=True)
        commit = {
            "meta": {"chapter": 1, "status": "accepted"},
            "projection_status": {
                "state": "done", "index": "pending", "summary": "done",
                "memory": "done", "vector": "done",
            },
        }
        (commits_dir / "chapter_001.commit.json").write_text(json.dumps(commit), encoding="utf-8")
        result = run_write_gate("postcommit", tmp_path, 1)
        # pending 是 warning，不阻断 ok
        assert any(e["code"] == "projection_pending" for e in result["warnings"])
