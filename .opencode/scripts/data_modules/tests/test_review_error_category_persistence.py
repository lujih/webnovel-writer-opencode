#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""5 大类一致性错误计数的落库、聚合与 API 暴露。

第 1 轮引入了 ConStory-Bench 的错误分类法（`review_schema.CONSISTENCY_TAXONOMY`），
但只做到 schema / metrics dict 层——**数据没有落库，面板也看不到**。分类法
不落库等于只活在内存里：趋势上仍然只有一个总量，回答不了「错在哪一类」。

本文件钉住：
1. `review_metrics.error_category_counts` 列存在，且老库能幂等迁补；
2. `save_review_metrics` 往返（写进去能按 dict 读回来）；
3. `get_error_category_totals()` 的聚合与窗口（`since_chapter` / `limit`）；
4. 坏数据（非法 JSON / 非 dict / 非数字计数）被跳过而不是让聚合崩掉；
5. 面板 `/api/review/analytics` 真的返回 `error_category_totals`。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.index_manager import IndexManager, ReviewMetrics  # noqa: E402
from review_pipeline import _build_review_metrics_record  # noqa: E402


@pytest.fixture
def index(tmp_path) -> IndexManager:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    return IndexManager(DataModulesConfig.from_project_root(str(tmp_path)))


def _save(index: IndexManager, chapter: int, counts: dict) -> None:
    index.save_review_metrics(ReviewMetrics(
        start_chapter=chapter, end_chapter=chapter,
        chapter_words=2000, ced_raw=0.5, ced_weighted=1.0, ced_blocking=0.0,
        error_category_counts=counts,
    ))


class TestColumnAndRoundtrip:
    def test_column_exists(self, index):
        with index._get_conn() as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(review_metrics)")}
        assert "error_category_counts" in columns

    def test_save_and_read_back(self, index):
        _save(index, 1, {"timeline_plot": 2, "worldbuilding": 1})
        rows = index.get_recent_review_metrics(5)
        assert rows[0]["error_category_counts"] == {"timeline_plot": 2, "worldbuilding": 1}

    def test_upsert_replaces_counts(self, index):
        _save(index, 1, {"timeline_plot": 2})
        _save(index, 1, {"narrative_style": 1})
        rows = index.get_recent_review_metrics(5)
        assert len(rows) == 1
        assert rows[0]["error_category_counts"] == {"narrative_style": 1}

    def test_empty_counts_default(self, index):
        index.save_review_metrics(ReviewMetrics(start_chapter=1, end_chapter=1))
        rows = index.get_recent_review_metrics(5)
        assert rows[0]["error_category_counts"] == {}


class TestAggregation:
    def test_sums_across_chapters(self, index):
        _save(index, 1, {"timeline_plot": 2})
        _save(index, 2, {"timeline_plot": 1, "worldbuilding": 3})
        assert index.get_error_category_totals() == {"timeline_plot": 3, "worldbuilding": 3}

    def test_since_chapter_window(self, index):
        _save(index, 1, {"timeline_plot": 5})
        _save(index, 5, {"timeline_plot": 2})
        assert index.get_error_category_totals(since_chapter=5) == {"timeline_plot": 2}

    def test_limit_keeps_newest(self, index):
        for chapter in range(1, 6):
            _save(index, chapter, {"timeline_plot": 1})
        # 只取最近 2 章（按 end_chapter 倒序）
        assert index.get_error_category_totals(limit=2) == {"timeline_plot": 2}

    def test_bad_json_is_skipped(self, index):
        _save(index, 1, {"timeline_plot": 1})
        with index._get_conn() as conn:
            conn.execute(
                "UPDATE review_metrics SET error_category_counts = ? WHERE end_chapter = 1",
                ("{not json",),
            )
            conn.commit()
        assert index.get_error_category_totals() == {}

    def test_non_numeric_counts_skipped(self, index):
        _save(index, 1, {"timeline_plot": "many", "worldbuilding": 2})
        assert index.get_error_category_totals() == {"worldbuilding": 2}

    def test_empty_table(self, index):
        assert index.get_error_category_totals() == {}


class TestRecordBuilderCarriesCounts:
    def test_builder_passes_through(self):
        record = _build_review_metrics_record({
            "start_chapter": 3, "end_chapter": 3,
            "overall_score": 80.0, "dimension_scores": {}, "severity_counts": {},
            "critical_issues": [], "chapter_words": 2000,
            "ced_raw": 1.0, "ced_weighted": 2.0, "ced_blocking": 0.0,
            "review_parseable": 1,
            "error_category_counts": {"factual_detail": 4},
        })
        assert record.error_category_counts == {"factual_detail": 4}

    def test_builder_tolerates_missing(self):
        record = _build_review_metrics_record({
            "start_chapter": 1, "end_chapter": 1, "review_parseable": 1,
        })
        assert record.error_category_counts == {}


class TestDashboardExposes:
    def test_analytics_endpoint_returns_totals(self):
        """面板必须真的把分类分布吐出来，否则落库了也没人看得见。"""
        source = (REPO_ROOT / ".opencode" / "dashboard" / "app.py").read_text(encoding="utf-8")
        assert '"error_category_totals"' in source
        assert "error_category_counts" in source, (
            "analytics 没有读取 error_category_counts 列"
        )

    def test_endpoint_returns_it_at_runtime(self, tmp_path):
        from fastapi.testclient import TestClient

        from dashboard.app import create_app

        (tmp_path / ".webnovel").mkdir(parents=True)
        (tmp_path / ".webnovel" / "state.json").write_text(
            json.dumps({"progress": {}}), encoding="utf-8")
        index = IndexManager(DataModulesConfig.from_project_root(str(tmp_path)))
        _save(index, 1, {"worldbuilding": 2, "timeline_plot": 1})

        # Host 守卫会拒绝 TestClient 默认的 `testserver`（DNS rebinding 防护），
        # 必须用面板实际监听的 origin。
        client = TestClient(create_app(project_root=tmp_path),
                            base_url="http://127.0.0.1:8765")
        payload = client.get("/api/review/analytics").json()
        assert payload.get("error_category_totals") == {
            "timeline_plot": 1, "worldbuilding": 2,
        }
