#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""运维诊断聚合端点 `/api/diagnostics/summary`。

背景：`rag_query_log` 与 `tool_call_stats` 两张表**一直在被写入**
（`rag_adapter` 每次检索、`context_manager.build` 每次组装），数据链是活的，
但面板此前只有原始日志转储端点（`/api/rag-queries`、`/api/tool-stats`），
没有任何入口把它们变成人能读的东西。本文件钉住新增的聚合端点。

聚合逻辑容易错的地方（也正是这里要防的）：
  - 百分比/分位数算错但不报错，只是数字不对；
  - 窗口统计（LIMIT 后）与全表统计混用；
  - 空表时除零。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO_ROOT / ".opencode"))

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.app import create_app  # noqa: E402

BASE_URL = "http://127.0.0.1:8765"


def _ensure_webnovel(project: Path) -> None:
    """建出 .webnovel/ 目录——sqlite3.connect 不会自动创建父目录。

    conftest.py 包了一层 _safe_sqlite_connect（`unable to open database file`
    在这里会直接抛出），所以不能指望"连不上就算了"。
    """
    (project / ".webnovel").mkdir(parents=True, exist_ok=True)


def _make_db(project: Path, *, with_tables: bool = True) -> sqlite3.Connection:
    db_path = project / ".webnovel" / "index.db"
    conn = sqlite3.connect(str(db_path))
    if with_tables:
        conn.execute("""
            CREATE TABLE rag_query_log (
                id INTEGER PRIMARY KEY, query TEXT, query_type TEXT,
                results_count INTEGER, hit_sources TEXT, latency_ms INTEGER,
                chapter INTEGER, created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE tool_call_stats (
                id INTEGER PRIMARY KEY, tool_name TEXT, success BOOLEAN,
                retry_count INTEGER, error_code TEXT, error_message TEXT,
                chapter INTEGER, created_at TEXT
            )
        """)
    conn.commit()
    return conn


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _seed(conn: sqlite3.Connection, rag_rows, tool_rows) -> None:
    for i, (qtype, results, latency) in enumerate(rag_rows):
        conn.execute(
            """INSERT INTO rag_query_log
               (query, query_type, results_count, latency_ms, chapter, created_at)
               VALUES (?,?,?,?,?,?)""",
            (f"查询{i}", qtype, results, latency, i + 1, f"2026-10-{i + 1:02d}"),
        )
    for i, (name, success, code, msg) in enumerate(tool_rows):
        conn.execute(
            """INSERT INTO tool_call_stats
               (tool_name, success, error_code, error_message, chapter, created_at)
               VALUES (?,?,?,?,?,?)""",
            (name, success, code, msg, i + 1, f"2026-10-{i + 1:02d}"),
        )
    conn.commit()


class TestDiagnosticsAggregation:

    def test_counts_and_average(self, tmp_path: Path):
        _ensure_webnovel(tmp_path)
        conn = _make_db(tmp_path)
        # 延迟 100,110,...,190 → 均值 145
        _seed(conn, [("entity", 5, 100 + i * 10) for i in range(10)], [])
        conn.close()

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        rag = client.get("/api/diagnostics/summary").json()["rag"]
        assert rag["total"] == 10
        assert rag["avg_latency_ms"] == pytest.approx(145.0)

    def test_percentiles(self, tmp_path: Path):
        _ensure_webnovel(tmp_path)
        conn = _make_db(tmp_path)
        _seed(conn, [("entity", 5, 100 + i * 10) for i in range(10)], [])
        conn.close()

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        rag = client.get("/api/diagnostics/summary").json()["rag"]
        # 10 个值 100..190，p50 应在中位（145），p95 接近最大
        assert rag["p50_latency_ms"] == pytest.approx(145.0, abs=1.0)
        assert rag["p95_latency_ms"] >= 180

    def test_empty_hits_counted(self, tmp_path: Path):
        _ensure_webnovel(tmp_path)
        conn = _make_db(tmp_path)
        # 前 3 条 results_count=0
        _seed(conn, [("entity", 0, 100) for _ in range(3)] + [("entity", 5, 100) for _ in range(7)], [])
        conn.close()

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        rag = client.get("/api/diagnostics/summary").json()["rag"]
        assert rag["empty_hits_total"] == 3

    def test_by_query_type_buckets(self, tmp_path: Path):
        _ensure_webnovel(tmp_path)
        conn = _make_db(tmp_path)
        _seed(conn, [("entity", 5, 100)] * 4 + [("foreshadow", 5, 100)] * 6, [])
        conn.close()

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        by_type = client.get("/api/diagnostics/summary").json()["rag"]["by_query_type"]
        assert by_type == {"entity": 4, "foreshadow": 6}

    def test_slowest_sorted_desc(self, tmp_path: Path):
        _ensure_webnovel(tmp_path)
        conn = _make_db(tmp_path)
        _seed(conn, [("entity", 5, 100), ("entity", 5, 900), ("entity", 5, 500)], [])
        conn.close()

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        slowest = client.get("/api/diagnostics/summary").json()["rag"]["slowest"]
        latencies = [item["latency_ms"] for item in slowest]
        assert latencies == sorted(latencies, reverse=True)
        assert latencies[0] == 900


class TestToolHealth:

    def test_failure_rate(self, tmp_path: Path):
        _ensure_webnovel(tmp_path)
        conn = _make_db(tmp_path)
        _seed(conn, [], [("build", False, "E1", "boom")] * 2 + [("build", True, None, None)] * 6)
        conn.close()

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        tools = client.get("/api/diagnostics/summary").json()["tools"]
        assert tools["total"] == 8
        assert tools["failures"] == 2
        assert tools["failure_rate"] == pytest.approx(0.25)

    def test_by_tool_buckets(self, tmp_path: Path):
        _ensure_webnovel(tmp_path)
        conn = _make_db(tmp_path)
        _seed(conn, [], [("a", True, None, None)] * 3 + [("b", False, "E", "x")] * 2)
        conn.close()

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        by_tool = client.get("/api/diagnostics/summary").json()["tools"]["by_tool"]
        assert by_tool["a"] == {"count": 3, "failures": 0}
        assert by_tool["b"] == {"count": 2, "failures": 2}

    def test_recent_failures_only_contains_failures(self, tmp_path: Path):
        _ensure_webnovel(tmp_path)
        conn = _make_db(tmp_path)
        _seed(conn, [], [("a", True, None, None), ("b", False, "E2", "坏了")])
        conn.close()

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        failures = client.get("/api/diagnostics/summary").json()["tools"]["recent_failures"]
        assert len(failures) == 1
        assert failures[0]["tool_name"] == "b"
        assert failures[0]["error_code"] == "E2"


class TestDegradedCases:
    """空表 / 缺表：返回零值，不能 500 也不能除零。"""

    def test_empty_tables_return_zeros(self, tmp_path: Path):
        _ensure_webnovel(tmp_path)
        conn = _make_db(tmp_path)
        conn.close()

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        response = client.get("/api/diagnostics/summary")
        assert response.status_code == 200, response.text[:300]
        data = response.json()
        assert data["rag"]["total"] == 0
        assert data["rag"]["avg_latency_ms"] == 0
        assert data["rag"]["p95_latency_ms"] is None
        assert data["tools"]["total"] == 0
        # 除零不能变成 ZeroDivisionError
        assert data["tools"]["failure_rate"] == 0.0

    def test_missing_tables_return_zeros(self, tmp_path: Path):
        """旧库没有这两张表时，_fetchall_safe 应降级为空。"""
        _ensure_webnovel(tmp_path)
        conn = _make_db(tmp_path, with_tables=False)
        conn.close()

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        response = client.get("/api/diagnostics/summary")
        assert response.status_code == 200, response.text[:300]
        assert response.json()["rag"]["total"] == 0

    def test_missing_index_db_returns_404(self, tmp_path: Path):
        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        assert client.get("/api/diagnostics/summary").status_code == 404


class TestLimitBoundaries:

    def test_invalid_limit_rejected(self, tmp_path: Path):
        _ensure_webnovel(tmp_path)
        conn = _make_db(tmp_path)
        conn.close()
        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        assert client.get("/api/diagnostics/summary", params={"limit": 0}).status_code == 422
        assert client.get("/api/diagnostics/summary", params={"limit": 9999}).status_code == 422

    def test_limit_does_not_cap_totals(self, tmp_path: Path):
        """与追读债同款坑：totals 必须是全表口径，不能受 limit 影响。"""
        _ensure_webnovel(tmp_path)
        conn = _make_db(tmp_path)
        _seed(conn, [("entity", 5, 100)] * 40, [("t", True, None, None)] * 40)
        conn.close()

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        small = client.get("/api/diagnostics/summary", params={"limit": 5}).json()
        assert small["rag"]["total"] == 40, "rag total 被 limit 截断了"
        assert small["tools"]["total"] == 40, "tools total 被 limit 截断了"
