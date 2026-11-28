#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""面板 `/api/context/health/{chapter}` 暴露 `meta.memory_stats`。

第 18 轮把分层记忆的统计写进上下文 payload 的 `meta.memory_stats`
（`working_injected` / `episodic_injected` / `semantic_injected` / `limits` /
`filtered` / `conflicts`）。这份数据是判断**记忆预算配比是否合理**的唯一依据，
必须能从面板读到，而不是让人去解析 `chapter-NNN.context.json`。

钉住：
1. context.json 里有统计时，端点原样透出；
2. 没有 `meta`（旧快照）时返回 `{}` —— 不报错、也不伪造字段；
3. trace 缺失时仍是 404（既有契约不变）。
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
    return TestClient(create_app(project_root=root), base_url="http://127.0.0.1:8765")


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"progress": {"current_chapter": 3}}), encoding="utf-8")
    return tmp_path


def _runtime(root: Path, chapter: int, *, context: dict | None) -> None:
    runtime = root / ".webnovel" / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    (runtime / f"chapter-{chapter:03d}.trace.json").write_text(
        json.dumps({"stage": "drafting", "template": "default",
                    "sections": {"included": ["core"], "excluded": []}}),
        encoding="utf-8")
    if context is not None:
        (runtime / f"chapter-{chapter:03d}.context.json").write_text(
            json.dumps(context, ensure_ascii=False), encoding="utf-8")


class TestMemoryStatsEndpoint:
    def test_surfaces_stats_from_context_file(self, tmp_path):
        root = _project(tmp_path)
        _runtime(root, 3, context={
            "meta": {"chapter": 3, "memory_stats": {
                "working_injected": 2, "episodic_injected": 3, "semantic_injected": 1,
                "layered_total_injected": 6, "filtered": 4, "conflicts": 1,
                "limits": {"working": 13, "episodic": 9, "semantic": 7},
            }},
            "core": {"chapter_outline": "大纲"},
        })
        payload = _client(root).get("/api/context/health/3").json()
        stats = payload["memory_stats"]
        assert stats["layered_total_injected"] == 6
        assert stats["conflicts"] == 1
        assert stats["limits"]["semantic"] == 7

    def test_old_snapshot_without_meta_returns_empty(self, tmp_path):
        root = _project(tmp_path)
        _runtime(root, 3, context={"core": {"chapter_outline": "旧快照没有 meta"}})
        payload = _client(root).get("/api/context/health/3").json()
        assert payload["memory_stats"] == {}

    def test_missing_context_file_returns_empty(self, tmp_path):
        root = _project(tmp_path)
        _runtime(root, 3, context=None)
        payload = _client(root).get("/api/context/health/3").json()
        assert payload["memory_stats"] == {}

    def test_missing_trace_still_404(self, tmp_path):
        response = _client(_project(tmp_path)).get("/api/context/health/9")
        assert response.status_code == 404
