#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""追读债聚合端点：/api/debts/summary 与 /api/debts/{id}。

背景：追读债子系统（chase_debt / debt_events / 章节复利计息）自 v5.3 起
就存在于数据层，`chapter-commit` 也在 open_loop_created 时建债，但 dashboard
一直没有入口——作者在面板上看不到任何一笔债。本文件钉住新增的两个端点。

设计要点（这些是刻意的行为，改动前请先读）：

1. **超期按当前章推算，不读 status 字段**。库里 status 只在
   `accrue_interest` 被调用时才推进；作者若很久没跑章节提交，一堆早已
   过期的债仍标 'active'。端点在每行上同时给 `status`（库里的）与
   `is_overdue`（真实情况），让前端能看出两者不一致。
2. **缺表返回空而不是 500**。旧库可能还没有 chase_debt，面板不该因此崩。
3. 路由顺序：`/api/debts/summary` 必须先于 `/api/debts/{debt_id}` 注册，
   否则 "summary" 会被当成 debt_id 解析（FastAPI 按声明顺序匹配）。
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


def _make_db(project: Path, *, with_debt_tables: bool = True) -> sqlite3.Connection:
    db_path = project / ".webnovel" / "index.db"
    conn = sqlite3.connect(str(db_path))
    if with_debt_tables:
        conn.execute("""
            CREATE TABLE chase_debt (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                debt_type TEXT NOT NULL,
                original_amount REAL DEFAULT 1.0,
                current_amount REAL DEFAULT 1.0,
                interest_rate REAL DEFAULT 0.1,
                source_chapter INTEGER NOT NULL,
                due_chapter INTEGER NOT NULL,
                override_contract_id INTEGER,
                status TEXT DEFAULT 'active',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE debt_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                debt_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                amount REAL NOT NULL,
                chapter INTEGER NOT NULL,
                note TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.commit()
    return conn


def _seed_debts(conn: sqlite3.Connection) -> None:
    rows = [
        # (type, original, current, rate, source, due, status)
        ("foreshadowing", 1.0, 2.46, 0.10, 15, 45, "active"),    # 当前章 120 → 超期
        ("reader_promise", 2.0, 2.20, 0.05, 60, 118, "active"),  # 超期 2 章
        ("pacing_debt", 1.5, 1.62, 0.08, 110, 175, "active"),    # 尚远
        ("foreshadowing", 1.0, 1.00, 0.10, 100, 130, "resolved"),  # 已结清
    ]
    for dtype, orig, cur, rate, src, due, status in rows:
        conn.execute(
            """INSERT INTO chase_debt
               (debt_type, original_amount, current_amount, interest_rate,
                source_chapter, due_chapter, status)
               VALUES (?,?,?,?,?,?,?)""",
            (dtype, orig, cur, rate, src, due, status),
        )
        debt_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.execute(
            "INSERT INTO debt_events (debt_id,event_type,amount,chapter,note) VALUES (?,?,?,?,?)",
            (debt_id, "create", orig, src, "建债"),
        )
    conn.commit()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"progress": {"current_chapter": 120}}), encoding="utf-8"
    )
    return tmp_path


@pytest.fixture
def client(project: Path) -> TestClient:
    conn = _make_db(project)
    _seed_debts(conn)
    conn.close()
    return TestClient(create_app(project_root=project), base_url=BASE_URL,
                      raise_server_exceptions=False)


class TestDebtsSummary:

    def test_returns_all_debts(self, client):
        response = client.get("/api/debts/summary")
        assert response.status_code == 200, response.text[:300]
        data = response.json()
        assert len(data["items"]) == 4

    def test_totals_only_count_unresolved(self, client):
        data = client.get("/api/debts/summary").json()
        # resolved 的那笔不计入余额
        expected = round(2.46 + 2.20 + 1.62, 4)
        assert data["totals"]["balance"] == pytest.approx(expected, abs=1e-6)

    def test_overdue_derived_from_current_chapter(self, client):
        data = client.get("/api/debts/summary").json()
        assert data["current_chapter"] == 120
        # 到期章 45 与 118 都早于 120 → 超期；175 未到
        assert data["totals"]["overdue"] == 2

    def test_status_field_and_is_overdue_can_disagree(self, client):
        """库里的 status 全是 active，但真实超期与否要单独给出。"""
        data = client.get("/api/debts/summary").json()
        active_rows = [r for r in data["items"] if r["status"] == "active"]
        assert all(r["status"] == "active" for r in active_rows)
        assert any(r["is_overdue"] for r in active_rows), (
            "status=active 但已过到期章的行必须标 is_overdue"
        )

    def test_chapters_left_is_signed(self, client):
        data = client.get("/api/debts/summary").json()
        by_due = {r["due_chapter"]: r for r in data["items"]}
        assert by_due[45]["chapters_left"] == -75
        assert by_due[175]["chapters_left"] == 55

    def test_growth_is_current_over_original(self, client):
        data = client.get("/api/debts/summary").json()
        by_due = {r["due_chapter"]: r for r in data["items"]}
        assert by_due[45]["growth"] == pytest.approx(2.46, abs=1e-3)
        assert by_due[175]["growth"] == pytest.approx(1.62 / 1.5, abs=1e-3)

    def test_by_type_buckets(self, client):
        data = client.get("/api/debts/summary").json()
        by_type = data["by_type"]
        assert by_type["foreshadowing"]["count"] == 2
        assert by_type["reader_promise"]["count"] == 1
        assert by_type["pacing_debt"]["count"] == 1

    def test_overdue_sorted_first(self, client):
        data = client.get("/api/debts/summary").json()
        overdue_flags = [r["is_overdue"] for r in data["items"]]
        # 所有 True 应排在 False 之前
        assert overdue_flags == sorted(overdue_flags, reverse=True)

    def test_limit_respected(self, client):
        data = client.get("/api/debts/summary", params={"limit": 2}).json()
        assert len(data["items"]) == 2

    def test_limit_bounds_validated(self, client):
        assert client.get("/api/debts/summary", params={"limit": 0}).status_code == 422
        assert client.get("/api/debts/summary", params={"limit": 999}).status_code == 422


class TestDebtDetail:

    def test_returns_debt_with_events(self, client):
        response = client.get("/api/debts/1")
        assert response.status_code == 200, response.text[:300]
        data = response.json()
        assert data["debt"]["id"] == 1
        assert len(data["events"]) == 1
        assert data["events"][0]["event_type"] == "create"

    def test_includes_overdue_judgement(self, client):
        data = client.get("/api/debts/1").json()
        assert data["is_overdue"] is True
        assert data["chapters_left"] == -75

    def test_missing_debt_returns_404(self, client):
        assert client.get("/api/debts/9999").status_code == 404

    def test_invalid_id_returns_422(self, client):
        assert client.get("/api/debts/abc").status_code == 422


class TestDegradedCases:
    """旧库 / 新项目：缺表不能 500。"""

    def test_summary_without_debt_tables_returns_empty(self, tmp_path: Path):
        (tmp_path / ".webnovel").mkdir(parents=True)
        (tmp_path / ".webnovel" / "state.json").write_text(
            json.dumps({"progress": {"current_chapter": 5}}), encoding="utf-8"
        )
        conn = _make_db(tmp_path, with_debt_tables=False)
        conn.close()

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        response = client.get("/api/debts/summary")
        assert response.status_code == 200, (
            f"缺 chase_debt 表时不应 500：{response.status_code} {response.text[:300]}"
        )
        data = response.json()
        assert data["items"] == []
        assert data["totals"]["count"] == 0

    def test_missing_index_db_returns_404(self, tmp_path: Path):
        (tmp_path / ".webnovel").mkdir(parents=True)
        (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        # 整个 index.db 不存在时，_get_db 抛 404 是既有约定
        assert client.get("/api/debts/summary").status_code == 404


class TestRouteOrdering:
    """/api/debts/summary 必须不被 /api/debts/{debt_id} 吞掉。"""

    def test_summary_path_wins_over_int_path_param(self, client):
        response = client.get("/api/debts/summary")
        assert response.status_code == 200
        assert "items" in response.json(), (
            "summary 被当成 debt_id 解析了——检查路由注册顺序"
        )


class TestTotalsIgnoreLimit:
    """totals 必须是**全表**口径，不能随 limit 变化。

    2026-10-20 第二轮审查发现：早先实现用同一条带 LIMIT 的查询既算统计又出
    明细，于是 limit=10 时「总余额」从 60 变成 10——作者看到的是随分页参数
    跳动的总额。统计与明细现在分开查。
    """

    def _seed_many(self, project: Path, count: int) -> None:
        conn = _make_db(project)
        for i in range(count):
            conn.execute(
                """INSERT INTO chase_debt
                   (debt_type, original_amount, current_amount, interest_rate,
                    source_chapter, due_chapter, status)
                   VALUES (?,?,?,?,?,?,?)""",
                ("foreshadowing", 1.0, 2.0, 0.1, i + 1, 50 + i, "active"),
            )
        conn.commit()
        conn.close()

    def test_totals_do_not_shrink_with_limit(self, tmp_path: Path):
        (tmp_path / ".webnovel").mkdir(parents=True)
        (tmp_path / ".webnovel" / "state.json").write_text(
            json.dumps({"progress": {"current_chapter": 100}}), encoding="utf-8"
        )
        self._seed_many(tmp_path, 60)

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)

        small = client.get("/api/debts/summary", params={"limit": 10}).json()
        large = client.get("/api/debts/summary", params={"limit": 500}).json()

        assert small["totals"]["count"] == 60, "totals.count 被 limit 截断了"
        assert small["totals"]["balance"] == pytest.approx(120.0), "totals.balance 被 limit 截断了"
        assert small["totals"] == large["totals"], "totals 不应随 limit 变化"

    def test_items_are_still_truncated(self, tmp_path: Path):
        (tmp_path / ".webnovel").mkdir(parents=True)
        (tmp_path / ".webnovel" / "state.json").write_text(
            json.dumps({"progress": {"current_chapter": 100}}), encoding="utf-8"
        )
        self._seed_many(tmp_path, 30)

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        data = client.get("/api/debts/summary", params={"limit": 5}).json()
        assert len(data["items"]) == 5

    def test_truncated_flag_reports_state(self, tmp_path: Path):
        (tmp_path / ".webnovel").mkdir(parents=True)
        (tmp_path / ".webnovel" / "state.json").write_text(
            json.dumps({"progress": {"current_chapter": 100}}), encoding="utf-8"
        )
        self._seed_many(tmp_path, 12)

        client = TestClient(create_app(project_root=tmp_path), base_url=BASE_URL,
                            raise_server_exceptions=False)
        assert client.get("/api/debts/summary", params={"limit": 5}).json()["truncated"] is True
        assert client.get("/api/debts/summary", params={"limit": 500}).json()["truncated"] is False
