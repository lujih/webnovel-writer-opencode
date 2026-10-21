#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""artifact_validator 测试。"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from data_modules.artifact_validator import (
    ERROR_BLOCKING_REVIEW,
    ERROR_MISSED_OUTLINE_NODE,
    ERROR_PENDING_DISAMBIGUATION,
    ERROR_SCHEMA,
    validate_artifact_payload,
    validate_commit_artifact_files,
    validate_chapter_commit,
)


class TestValidateArtifactPayload:
    def test_valid_review_result(self):
        payload = {"blocking_count": 0, "issues": []}
        result = validate_artifact_payload("review_result", payload)
        assert result["ok"] is True
        assert result["errors"] == []

    def test_review_with_semantic_blocking_issues_is_warning(self):
        """门禁分层（2026-10）：LLM 语义判定（解析成功）的 blocking issue
        降级为 warning（观察），不再 error 阻断。"""
        payload = {
            "blocking_count": 0,
            "issues": [{"blocking": True, "severity": "critical", "description": "test"}],
        }
        result = validate_artifact_payload("review_result", payload)
        # 语义 blocking → warning，不在 errors 里
        assert not any(e["code"] == ERROR_BLOCKING_REVIEW for e in result["errors"]), (
            "LLM 语义 critical 不再是 error 阻断——它可误判，应降级为观察标记"
        )
        assert any(
            w.get("code") == ERROR_BLOCKING_REVIEW and w.get("severity") == "warning"
            for w in result.get("warnings", [])
        ), "LLM 语义 blocking issue 应降级为 warning 留痕"

    def test_review_with_unparseable_is_error(self):
        """技术故障（审查输出不可解析）仍 error 阻断。"""
        payload = {
            "blocking_count": 1,
            "issues": [{"blocking": True, "severity": "critical",
                        "category": "review_output_malformed",
                        "description": "审查结果不可解析，已阻断提交。"}],
            "review_parseable": 0,
        }
        result = validate_artifact_payload("review_result", payload)
        assert any(
            e["code"] == ERROR_BLOCKING_REVIEW and e.get("severity") == "error"
            for e in result["errors"]
        ), "解析失败必须 error 阻断（无法判断有没有问题 ≠ 没有问题）"

    def test_valid_fulfillment_result(self):
        payload = {
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [],
            "extra_nodes": [],
        }
        result = validate_artifact_payload("fulfillment_result", payload)
        assert result["ok"] is True

    def test_fulfillment_with_missed_cbn(self):
        payload = {
            "planned_nodes": [],
            "covered_nodes": [],
            "missed_nodes": [{"type": "CBN", "name": "test"}],
            "extra_nodes": [],
        }
        result = validate_artifact_payload("fulfillment_result", payload)
        assert any(e["code"] == ERROR_MISSED_OUTLINE_NODE for e in result["errors"])

    def test_valid_disambiguation_result(self):
        payload = {"pending": []}
        result = validate_artifact_payload("disambiguation_result", payload)
        assert result["ok"] is True

    def test_disambiguation_with_pending(self):
        payload = {"pending": [{"entity": "test"}]}
        result = validate_artifact_payload("disambiguation_result", payload)
        assert any(e["code"] == ERROR_PENDING_DISAMBIGUATION for e in result["errors"])

    def test_valid_extraction_result(self):
        payload = {
            "accepted_events": [{"event_type": "test"}],
            "state_deltas": [],
            "entity_deltas": [],
        }
        result = validate_artifact_payload("extraction_result", payload)
        assert result["ok"] is True

    def test_extraction_no_events(self):
        payload = {
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
        }
        result = validate_artifact_payload("extraction_result", payload)
        assert any(w["code"] == "no_events" for w in result["warnings"])

    def test_schema_validation_failure(self):
        payload = {"invalid": "data"}
        result = validate_artifact_payload("review_result", payload)
        assert result["ok"] is False
        assert any(e["code"] == ERROR_SCHEMA for e in result["errors"])

    def test_unknown_artifact(self):
        result = validate_artifact_payload("unknown_type", {})
        assert any(w["code"] == "unknown_artifact" for w in result["warnings"])


class TestValidateCommitArtifactFiles:
    def test_missing_files(self, tmp_path):
        result = validate_commit_artifact_files(tmp_path, 1)
        assert result["ok"] is False
        assert result["error_count"] == 4  # all 4 files missing


class TestValidateChapterCommit:
    def test_missing_commit(self, tmp_path):
        result = validate_chapter_commit(tmp_path, 1)
        assert result["ok"] is False
        assert any(e["code"] == "commit_missing" for e in result["errors"])

    def test_valid_commit(self, tmp_path):
        commits_dir = tmp_path / ".story-system" / "commits"
        commits_dir.mkdir(parents=True)
        commit = {
            "meta": {"chapter": 1, "status": "accepted"},
            "review_result": {"blocking_count": 0, "issues": []},
            "fulfillment_result": {"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
            "disambiguation_result": {"pending": []},
            "extraction_result": {"accepted_events": [{"event_type": "test"}], "state_deltas": [], "entity_deltas": []},
            "projection_status": {
                "state": "done", "index": "done", "summary": "done",
                "memory": "done", "vector": "done",
            },
        }
        (commits_dir / "chapter_001.commit.json").write_text(
            json.dumps(commit), encoding="utf-8"
        )
        result = validate_chapter_commit(tmp_path, 1)
        assert result["ok"] is True

    def test_commit_with_failed_projection(self, tmp_path):
        """projection_status 含 failed 时应报错。"""
        commits_dir = tmp_path / ".story-system" / "commits"
        commits_dir.mkdir(parents=True)
        commit = {
            "meta": {"chapter": 1, "status": "accepted"},
            "review_result": {"blocking_count": 0, "issues": []},
            "fulfillment_result": {"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
            "disambiguation_result": {"pending": []},
            "extraction_result": {"accepted_events": [], "state_deltas": [], "entity_deltas": []},
            "projection_status": {
                "state": "done", "index": "failed:sqlite_error", "summary": "done",
                "memory": "done", "vector": "done",
            },
        }
        (commits_dir / "chapter_001.commit.json").write_text(json.dumps(commit), encoding="utf-8")
        result = validate_chapter_commit(tmp_path, 1)
        assert result["ok"] is False
        assert any("projection" in e["code"] for e in result["errors"])

    def test_commit_missing_vector_projection(self, tmp_path):
        """projection_status 缺少 vector 时应报错。"""
        commits_dir = tmp_path / ".story-system" / "commits"
        commits_dir.mkdir(parents=True)
        commit = {
            "meta": {"chapter": 1, "status": "accepted"},
            "review_result": {"blocking_count": 0, "issues": []},
            "fulfillment_result": {"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
            "disambiguation_result": {"pending": []},
            "extraction_result": {"accepted_events": [], "state_deltas": [], "entity_deltas": []},
            "projection_status": {
                "state": "done", "index": "done", "summary": "done",
                "memory": "done",
                # vector 缺失
            },
        }
        (commits_dir / "chapter_001.commit.json").write_text(json.dumps(commit), encoding="utf-8")
        result = validate_chapter_commit(tmp_path, 1)
        assert result["ok"] is False
        assert any("incomplete" in e["code"] for e in result["errors"])

    def test_commit_missing_nested_artifact(self, tmp_path):
        """commit 缺少嵌套 artifact 时应报错。"""
        commits_dir = tmp_path / ".story-system" / "commits"
        commits_dir.mkdir(parents=True)
        commit = {
            "meta": {"chapter": 1, "status": "accepted"},
            # 缺少 review_result, fulfillment_result 等
            "projection_status": {
                "state": "done", "index": "done", "summary": "done",
                "memory": "done", "vector": "done",
            },
        }
        (commits_dir / "chapter_001.commit.json").write_text(json.dumps(commit), encoding="utf-8")
        result = validate_chapter_commit(tmp_path, 1)
        assert result["ok"] is False
        assert any(e["code"] == "missing_artifact" for e in result["errors"])

    def test_commit_with_pending_projection(self, tmp_path):
        """projection_status 含 pending 时应报错。"""
        commits_dir = tmp_path / ".story-system" / "commits"
        commits_dir.mkdir(parents=True)
        commit = {
            "meta": {"chapter": 1, "status": "accepted"},
            "review_result": {"blocking_count": 0, "issues": []},
            "fulfillment_result": {"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
            "disambiguation_result": {"pending": []},
            "extraction_result": {"accepted_events": [], "state_deltas": [], "entity_deltas": []},
            "projection_status": {
                "state": "done", "index": "pending", "summary": "done",
                "memory": "done", "vector": "done",
            },
        }
        (commits_dir / "chapter_001.commit.json").write_text(json.dumps(commit), encoding="utf-8")
        result = validate_chapter_commit(tmp_path, 1)
        assert result["ok"] is False
        assert any("incomplete" in e["code"] for e in result["errors"])
