#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`/api/review/analytics` 的 CED 聚合。

后端为前端提供三个口径的 CED 曲线与汇总。聚合时必须**排除两类章节**，
否则曲线上会出现误导性的点：

1. `review_parseable = 0` —— 审查输出不可解析（模型没给 JSON、被截断）。
   这类章的 issue 数据不可信，画上去就是虚假尖峰。
2. CED 为 `NULL` —— 分母为 0（章节文件缺失/空文件），算不出来。
   **必须与 0.0 区分**：0.0 意味着"真的没发现问题"，NULL 意味着"不知道"。

把 NULL 混进均值会稀释曲线，把不可解析章混进去会制造尖峰——两者都会让
「改进是否真的减少错误」这个问题得出错误答案。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
DASHBOARD = SCRIPTS.parent

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))

from fastapi.testclient import TestClient  # noqa: E402

from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.index_manager import IndexManager, ReviewMetrics  # noqa: E402

LOCAL_BASE_URL = "http://127.0.0.1:8765"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return tmp_path


@pytest.fixture
def client(project):
    from dashboard.app import create_app

    return TestClient(create_app(project_root=project), base_url=LOCAL_BASE_URL)


def _seed(project: Path, rows) -> None:
    cfg = DataModulesConfig.from_project_root(project)
    cfg.ensure_dirs()
    manager = IndexManager(cfg)
    for ch, words, raw, weighted, blocking, parseable in rows:
        manager.save_review_metrics(ReviewMetrics(
            start_chapter=ch, end_chapter=ch, overall_score=75.0,
            chapter_words=words, ced_raw=raw, ced_weighted=weighted,
            ced_blocking=blocking, review_parseable=parseable,
        ))


def _seed_review_schema_only(project: Path) -> None:
    """只创建 index.db（含 review_metrics 表），不写入任何审查记录。"""
    cfg = DataModulesConfig.from_project_root(project)
    cfg.ensure_dirs()
    IndexManager(cfg)          # 构造即建表


class TestCedTrends:
    """趋势曲线的收集与排除。"""

    def test_includes_only_parseable_with_ced(self, client, project):
        _seed(project, [
            (1, 3000, 6.667, 13.333, 3.333, 1),
            (2, 3000, 3.333, 6.667, 3.333, 1),
        ])
        data = client.get("/api/review/analytics").json()
        chapters = [p["chapter"] for p in data["ced_trends"]["ced_raw"]]
        assert chapters == [2, 1], "应按章倒序返回最近的数据"

    def test_excludes_unparseable(self, client, project):
        _seed(project, [
            (1, 3000, 6.667, 13.333, 3.333, 1),
            (2, 3000, None, None, None, 0),      # 审查输出不可解析
        ])
        data = client.get("/api/review/analytics").json()
        chapters = [p["chapter"] for p in data["ced_trends"]["ced_raw"]]
        assert chapters == [1], (
            f"不可解析的第 2 章被算进 CED 曲线了：{chapters}。"
            f"那一章没有可信的 issue 数据，画上去是虚假尖峰。"
        )

    def test_excludes_null_ced(self, client, project):
        """NULL（算不出）必须排除，且不得被当成 0。"""
        _seed(project, [
            (1, 3000, 6.667, 13.333, 3.333, 1),
            (2, 0, None, None, None, 1),          # 0 字数，算不出
        ])
        data = client.get("/api/review/analytics").json()
        chapters = [p["chapter"] for p in data["ced_trends"]["ced_raw"]]
        assert chapters == [1]
        values = [p["ced"] for p in data["ced_trends"]["ced_raw"]]
        assert None not in values, "NULL 被塞进曲线了"

    def test_zero_ced_is_included(self, client, project):
        """0.0 是真实数据（没发现问题），必须留在曲线上。"""
        _seed(project, [(1, 3000, 0.0, 0.0, 0.0, 1)])
        data = client.get("/api/review/analytics").json()
        points = data["ced_trends"]["ced_raw"]
        assert len(points) == 1 and points[0]["ced"] == 0.0, (
            "ced=0.0 被当成 NULL 排除了——'没发现问题'与'不知道'是两件事"
        )


class TestCedSummary:
    """汇总字段。"""

    def test_counts_and_skips(self, client, project):
        _seed(project, [
            (1, 3000, 6.667, 13.333, 3.333, 1),
            (2, 3000, 3.333, 6.667, 3.333, 1),
            (3, 3000, 1.667, 3.333, 0.0, 1),
            (4, 3000, None, None, None, 0),
            (5, 0, None, None, None, 1),
        ])
        summary = client.get("/api/review/analytics").json()["ced_summary"]
        assert summary["chapters_counted"] == 3
        assert summary["chapters_skipped"] == 2

    def test_trend_direction_when_improving(self, client, project):
        """错误密度下降应报 down（改善）。"""
        _seed(project, [
            (1, 3000, 9.0, 18.0, 6.0, 1),
            (2, 3000, 5.0, 10.0, 3.0, 1),
            (3, 3000, 1.0, 2.0, 0.0, 1),
        ])
        summary = client.get("/api/review/analytics").json()["ced_summary"]
        for key in ("ced_raw", "ced_weighted", "ced_blocking"):
            assert summary[key]["trend"] == "down", (
                f"{key} 的最新值低于均值，应报 down（改善），实际 {summary[key]['trend']}"
            )

    def test_trend_direction_when_worsening(self, client, project):
        _seed(project, [
            (1, 3000, 1.0, 2.0, 0.0, 1),
            (2, 3000, 5.0, 10.0, 3.0, 1),
            (3, 3000, 9.0, 18.0, 6.0, 1),
        ])
        summary = client.get("/api/review/analytics").json()["ced_summary"]
        for key in ("ced_raw", "ced_weighted", "ced_blocking"):
            assert summary[key]["trend"] == "up"

    def test_all_fields_none_when_no_data(self, client, project):
        # 空库（连 index.db 都没有）时端点返回 404，是既有行为；
        # 这里关心的是"有库但无审查记录"时三个口径必须齐全。
        _seed_review_schema_only(project)
        summary = client.get("/api/review/analytics").json()["ced_summary"]
        for key in ("ced_raw", "ced_weighted", "ced_blocking"):
            assert summary[key]["avg"] is None, f"{key} 应有 None 的 avg"
            assert summary[key]["trend"] is None, f"{key} 应有 None 的 trend"
        assert summary["chapters_counted"] == 0

    def test_latest_is_most_recent_chapter(self, client, project):
        _seed(project, [
            (1, 3000, 9.0, 18.0, 6.0, 1),
            (5, 3000, 2.0, 4.0, 1.0, 1),
        ])
        summary = client.get("/api/review/analytics").json()["ced_summary"]
        assert summary["ced_raw"]["latest"] == pytest.approx(2.0), (
            "latest 应取最新章（第 5 章）而不是最旧章"
        )


class TestExistingFieldsUnchanged:
    """纯追加：既有响应字段不受影响。"""

    def test_existing_keys_present(self, client, project):
        _seed(project, [(1, 3000, 6.667, 13.333, 3.333, 1)])
        data = client.get("/api/review/analytics").json()
        for key in ("dimension_trends", "dimension_averages",
                    "weakest_dimensions", "severity_totals",
                    "critical_issues", "total_reviews"):
            assert key in data, f"既有字段 {key} 丢了"

    def test_review_metrics_endpoint_exposes_ced(self, client, project):
        _seed(project, [(1, 3000, 6.667, 13.333, 3.333, 1)])
        rows = client.get("/api/review-metrics").json()
        assert rows, "review-metrics 应返回数据"
        row = rows[0]
        for key in ("chapter_words", "ced_raw", "ced_weighted",
                    "ced_blocking", "review_parseable"):
            assert key in row, f"review-metrics 缺 {key}"

    def test_empty_db_does_not_crash(self, client, project):
        """完全没有 index.db 时返回 404（既有行为），不应 500。"""
        r = client.get("/api/review/analytics")
        assert r.status_code in (200, 404), f"意外状态码 {r.status_code}"
        if r.status_code == 200:
            assert r.json()["ced_summary"]["chapters_counted"] == 0

    def test_db_without_reviews_has_full_shape(self, client, project):
        """有 index.db 但无审查记录时，CED 字段必须齐全（键集合一致）。"""
        _seed_review_schema_only(project)
        data = client.get("/api/review/analytics").json()
        assert "ced_trends" in data and "ced_summary" in data
        assert set(data["ced_summary"]) >= {
            "ced_raw", "ced_weighted", "ced_blocking",
            "chapters_counted", "chapters_skipped"}
