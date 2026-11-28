#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""面板 `/api/changes/audit`：把「提交到底改了什么」暴露给审查分析页。

与 `/api/review/analytics` 的分工：那边看审查说了什么，这边看提交记录。
审查会漏报，提交记录不会——本轮加这个端点就是为了让两者能对照。

钉住：
1. 端点返回 `findings` / `count` / `locks_loaded`；
2. 只报高危字段与违背 canon lock 的记录（低危变更不进报告）；
3. canon_locks.json 损坏时降级为空锁，不让整页 500；
4. 空项目返回空列表而不是报错。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.app import create_app  # noqa: E402


def _client(root: Path) -> TestClient:
    # Host 守卫拒绝 TestClient 默认的 `testserver`（DNS rebinding 防护），
    # 必须用面板实际监听的 origin。
    return TestClient(create_app(project_root=root), base_url="http://127.0.0.1:8765")


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"progress": {}}), encoding="utf-8")
    (tmp_path / ".story-system" / "commits").mkdir(parents=True, exist_ok=True)
    return tmp_path


def _commit(root: Path, chapter: int, deltas: list[dict]) -> None:
    payload = {"meta": {"chapter": chapter, "status": "accepted"},
               "state_deltas": deltas, "accepted_events": []}
    path = root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TestChangesAuditEndpoint:
    def test_empty_project(self, tmp_path):
        payload = _client(_project(tmp_path)).get("/api/changes/audit").json()
        assert payload == {"findings": [], "count": 0, "locks_loaded": 0}

    def test_reports_high_risk_only(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1, [
            {"entity_id": "xiaoyan", "field": "realm", "old": "斗者", "new": "斗师"},
            {"entity_id": "xiaoyan", "field": "hair_color", "old": "黑", "new": "白"},
        ])
        payload = _client(root).get("/api/changes/audit").json()
        assert payload["count"] == 1
        assert payload["findings"][0]["field"] == "realm"
        assert payload["findings"][0]["risk"] == "high"
        assert payload["findings"][0]["chapter"] == 1

    def test_limit_is_respected(self, tmp_path):
        root = _project(tmp_path)
        for chapter in (1, 2, 3):
            _commit(root, chapter, [
                {"entity_id": "a", "field": "realm", "old": "x", "new": f"L{chapter}"},
            ])
        payload = _client(root).get("/api/changes/audit", params={"limit": 1}).json()
        assert [f["chapter"] for f in payload["findings"]] == [3]

    def test_lock_violation_surfaces(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1, [
            {"entity_id": "a", "field": "hair_color", "old": "黑", "new": "白"},
        ])
        (root / ".story-system" / "canon_locks.json").write_text(json.dumps({
            "locks": [{"subject": "a", "field": "hair_color", "value": "黑", "chapter": 1}],
        }, ensure_ascii=False), encoding="utf-8")
        payload = _client(root).get("/api/changes/audit").json()
        # 低危字段本身不进报告，但违反锁定必须进——这正是回溯审计的价值
        assert payload["count"] == 1
        assert payload["findings"][0]["locked_value"] == "黑"
        assert payload["locks_loaded"] == 1

    def test_corrupt_lock_file_degrades(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1, [{"entity_id": "a", "field": "realm", "new": "斗师"}])
        (root / ".story-system" / "canon_locks.json").write_text("{not json", encoding="utf-8")
        response = _client(root).get("/api/changes/audit")
        assert response.status_code == 200
        payload = response.json()
        assert payload["locks_loaded"] == 0
        assert payload["count"] == 1, "锁文件坏了也应该照常报高危变更"


class TestChangesDiffEndpoint:
    def test_missing_chapter_is_404(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1, [{"entity_id": "a", "field": "realm", "new": "1"}])
        response = _client(root).get("/api/changes/diff", params={"from": 1, "to": 2})
        assert response.status_code == 404
        assert "第 2 章" in response.json()["detail"]

    def test_reports_changed_added_removed(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1, [
            {"entity_id": "a", "field": "realm", "new": "斗者"},
            {"entity_id": "a", "field": "mood", "new": "平静"},
            {"entity_id": "b", "field": "realm", "new": "斗师"},
        ])
        _commit(root, 2, [
            {"entity_id": "a", "field": "realm", "new": "斗灵"},
            {"entity_id": "a", "field": "mood", "new": "平静"},
            {"entity_id": "c", "field": "realm", "new": "斗王"},
        ])
        payload = _client(root).get("/api/changes/diff", params={"from": 1, "to": 2}).json()
        assert payload["counts"] == {"changed": 1, "added": 1, "removed": 1}
        assert payload["changed"][0]["entity_id"] == "a"
        assert payload["changed"][0]["from"] == "斗者"
        assert payload["changed"][0]["to"] == "斗灵"
        assert payload["added"][0]["entity_id"] == "c"
        assert payload["removed"][0]["entity_id"] == "b"

    def test_identical_chapters_have_no_diff(self, tmp_path):
        root = _project(tmp_path)
        deltas = [{"entity_id": "a", "field": "realm", "new": "斗师"}]
        _commit(root, 1, deltas)
        _commit(root, 2, deltas)
        payload = _client(root).get("/api/changes/diff", params={"from": 1, "to": 2}).json()
        assert payload["counts"] == {"changed": 0, "added": 0, "removed": 0}

    def test_rejects_non_positive_chapter(self, tmp_path):
        response = _client(_project(tmp_path)).get(
            "/api/changes/diff", params={"from": 0, "to": 1})
        assert response.status_code == 422


class TestChangesListEndpoint:
    """`/api/changes/list` 给单章的**全量**清单——与 audit 的摘要互补。"""

    def test_missing_commit_is_404(self, tmp_path):
        response = _client(_project(tmp_path)).get("/api/changes/list", params={"chapter": 9})
        assert response.status_code == 404
        assert "没有 commit 记录" in response.json()["detail"]

    def test_returns_all_changes_including_low_risk(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 2, [
            {"entity_id": "a", "field": "realm", "old": "斗者", "new": "斗师"},
            {"entity_id": "a", "field": "hair_color", "old": "黑", "new": "白"},
        ])
        payload = _client(root).get("/api/changes/list", params={"chapter": 2}).json()
        assert payload["chapter"] == 2
        assert payload["count"] == 2
        assert [c["field"] for c in payload["changes"]] == ["realm", "hair_color"]
        assert [c["risk"] for c in payload["changes"]] == ["high", "low"]

    def test_marks_lock_violation(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1, [{"entity_id": "a", "field": "realm", "old": "x", "new": "斗宗"}])
        (root / ".story-system" / "canon_locks.json").write_text(json.dumps({
            "locks": [{"subject": "a", "field": "realm", "value": "斗师", "chapter": 1}],
        }, ensure_ascii=False), encoding="utf-8")
        payload = _client(root).get("/api/changes/list", params={"chapter": 1}).json()
        assert payload["changes"][0]["locked_value"] == "斗师"

    def test_rejects_invalid_chapter(self, tmp_path):
        response = _client(_project(tmp_path)).get("/api/changes/list", params={"chapter": 0})
        assert response.status_code == 422


class TestAnalyticsKeepsKeyContract:
    def test_empty_db_still_returns_category_key(self, tmp_path):
        """空库时也要有 `error_category_totals`，否则前端得判两处形状。

        注意：`/api/review/analytics` 依赖 index.db，项目里没有库时直接 404
        （`index.db 不存在`）——所以必须先建库，否则测的是错误路径。
        """
        from data_modules.config import DataModulesConfig
        from data_modules.index_manager import IndexManager

        root = _project(tmp_path)
        IndexManager(DataModulesConfig.from_project_root(str(root)))
        payload = _client(root).get("/api/review/analytics").json()
        assert payload["error_category_totals"] == {}
        assert payload["items"] == []
