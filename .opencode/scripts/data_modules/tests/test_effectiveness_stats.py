#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""效果指标：审查标记误判率、叙事预报 vs CED。

## 这两个指标回答什么

v3.0 之后，三项能力都有「在不在跑」的可观测性，但**没有「有没有用」的度量**：

1. **审查标记误判率**（`flag_triage.dismiss_rate`）——
   门禁分层把 LLM 的 critical 降级成观察标记。这些标记到底准不准？
   `dismiss` 比例就是实测误判率：高 = 旧门禁在大量误杀；低 = 旧门禁多数正确。
   这个数字是判断「门禁分层走对了没有」的唯一依据。

2. **叙事预报 vs CED**（`forecast_effectiveness`）——
   有 forecast 并选支的章，是否比没用的章更少一致性错误。

## 两条「不伪造」原则（与 CED 的 None vs 0 一脉相承）

- **没仲裁过 → `dismiss_rate` 是 None，不是 0**。0 会被读成「LLM 永远正确」，
  真相是「还没人裁过」。
- **样本不足 → `status=insufficient_data`，不给均值**。任一侧 < 5 章时的
  均值差异基本是噪声，给数字会诱导过度解读。

本文件同时钉住 `review_parseable=0` 的章**不进 CED 对比**——把「算不出」当
「0 个错误」会把基线拉低，得出「预报没效果」的假结论。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.effectiveness_stats import (  # noqa: E402
    MIN_CHAPTERS_PER_SIDE,
    flag_triage_stats,
    forecast_effectiveness,
)
from data_modules.index_manager import IndexManager, ReviewMetrics  # noqa: E402


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    config = DataModulesConfig.from_project_root(tmp_path)
    config.ensure_dirs()
    IndexManager(config)               # 建表
    return tmp_path


def _commit(root: Path, chapter: int, flags) -> None:
    commits = root / ".story-system" / "commits"
    commits.mkdir(parents=True, exist_ok=True)
    (commits / f"chapter_{chapter:03d}.commit.json").write_text(
        json.dumps({"meta": {"chapter": chapter, "status": "accepted"},
                    "review_flags": flags}, ensure_ascii=False),
        encoding="utf-8")


def _flag(severity="critical", triage=None, description="x"):
    return {"severity": severity, "triage": triage, "description": description}


def _metrics(root: Path, chapter: int, ced_raw, *, parseable=1) -> None:
    manager = IndexManager(DataModulesConfig.from_project_root(root))
    manager.save_review_metrics(ReviewMetrics(
        start_chapter=chapter, end_chapter=chapter, chapter_words=3000,
        ced_raw=ced_raw,
        ced_weighted=(ced_raw * 2 if ced_raw is not None else None),
        ced_blocking=0.0 if ced_raw is not None else None,
        review_parseable=parseable,
    ))


def _select(root: Path, chapter: int) -> None:
    d = root / ".story-system" / "forecasts" / f"chapter_{chapter:03d}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "selected.json").write_text('{"candidate_id": "calm_transition"}',
                                     encoding="utf-8")


class TestFlagTriageStats:
    """审查标记仲裁统计。"""

    def test_counts_by_outcome(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1, [_flag("critical", "confirmed"),
                          _flag("high", "dismissed"),
                          _flag("high", "dismissed")])
        _commit(root, 2, [_flag("critical", "confirmed")])
        _commit(root, 3, [_flag("low", None), _flag("low", "dismissed")])

        stats = flag_triage_stats(root)
        assert stats["total_flags"] == 6
        assert stats["confirmed"] == 2
        assert stats["dismissed"] == 3
        assert stats["pending"] == 1
        assert stats["triaged"] == 5
        assert stats["chapters_scanned"] == 3
        assert stats["chapters_with_flags"] == 3

    def test_dismiss_rate_is_dismissed_over_triaged(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1, [_flag(triage="dismissed")] * 3 + [_flag(triage="confirmed")] * 1)
        assert flag_triage_stats(root)["dismiss_rate"] == 0.75

    def test_no_triage_yields_none_not_zero(self, tmp_path):
        """没仲裁过 → None。0 会被读成「LLM 永远正确」。"""
        root = _project(tmp_path)
        _commit(root, 1, [_flag(triage=None), _flag(triage=None)])
        stats = flag_triage_stats(root)
        assert stats["pending"] == 2
        assert stats["triaged"] == 0
        assert stats["dismiss_rate"] is None, (
            "无仲裁记录时 dismiss_rate 必须是 None，不是 0"
        )

    def test_empty_project(self, tmp_path):
        stats = flag_triage_stats(_project(tmp_path))
        assert stats["total_flags"] == 0
        assert stats["dismiss_rate"] is None
        assert stats["chapters_scanned"] == 0

    def test_by_severity_breakdown(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1, [_flag("critical", "dismissed"),
                          _flag("critical", "confirmed"),
                          _flag("high", "dismissed")])
        by_sev = flag_triage_stats(root)["by_severity"]
        assert by_sev["critical"] == {"confirmed": 1, "dismissed": 1, "pending": 0}
        assert by_sev["high"] == {"confirmed": 0, "dismissed": 1, "pending": 0}

    def test_unknown_severity_bucketed(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1, [{"triage": "dismissed", "description": "x"}])
        assert "unknown" in flag_triage_stats(root)["by_severity"]

    def test_skips_corrupt_commit_json(self, tmp_path):
        root = _project(tmp_path)
        _commit(root, 1, [_flag(triage="confirmed")])
        commits = root / ".story-system" / "commits"
        (commits / "chapter_002.commit.json").write_text("{坏 JSON", encoding="utf-8")
        stats = flag_triage_stats(root)
        assert stats["chapters_scanned"] == 1, "坏文件应跳过而不是整体失败"
        assert stats["confirmed"] == 1

    def test_missing_commits_dir(self, tmp_path):
        root = _project(tmp_path)
        assert flag_triage_stats(root)["total_flags"] == 0


class TestForecastEffectiveness:
    """预报 vs CED 对比。"""

    def _balanced(self, root: Path, *, forecast_ced: float, baseline_ced: float):
        for ch in range(1, MIN_CHAPTERS_PER_SIDE + 1):
            _metrics(root, ch, forecast_ced)
            _select(root, ch)
        for ch in range(MIN_CHAPTERS_PER_SIDE + 1, MIN_CHAPTERS_PER_SIDE * 2 + 1):
            _metrics(root, ch, baseline_ced)

    def test_computes_group_means(self, tmp_path):
        root = _project(tmp_path)
        self._balanced(root, forecast_ced=2.0, baseline_ced=5.0)
        eff = forecast_effectiveness(root)
        assert eff["status"] == "ok"
        assert eff["forecast"]["chapters"] == MIN_CHAPTERS_PER_SIDE
        assert eff["baseline"]["chapters"] == MIN_CHAPTERS_PER_SIDE
        assert eff["forecast"]["ced_raw"] == pytest.approx(2.0)
        assert eff["baseline"]["ced_raw"] == pytest.approx(5.0)

    def test_delta_is_forecast_minus_baseline(self, tmp_path):
        root = _project(tmp_path)
        self._balanced(root, forecast_ced=2.0, baseline_ced=5.0)
        # 负数 = 用了预报的章错误更少
        assert forecast_effectiveness(root)["delta"]["ced_raw"] == pytest.approx(-3.0)

    def test_insufficient_when_one_side_small(self, tmp_path):
        root = _project(tmp_path)
        # 选支侧只有 2 章
        for ch in (1, 2):
            _metrics(root, ch, 2.0)
            _select(root, ch)
        for ch in range(3, 13):
            _metrics(root, ch, 5.0)
        eff = forecast_effectiveness(root)
        assert eff["status"] == "insufficient_data"
        assert eff["delta"]["ced_raw"] is None, "样本不足时不得给差值"
        assert "至少" in eff["reason"]

    def test_insufficient_on_empty(self, tmp_path):
        eff = forecast_effectiveness(_project(tmp_path))
        assert eff["status"] == "insufficient_data"
        assert eff["forecast"]["chapters"] == 0

    def test_unparseable_excluded_from_ced(self, tmp_path):
        """review_parseable=0 的章 CED 无效，不得当成 0 计入基线。"""
        root = _project(tmp_path)
        self._balanced(root, forecast_ced=2.0, baseline_ced=5.0)
        # 再塞 3 章不可解析的到基线侧
        for ch in range(20, 23):
            _metrics(root, ch, None, parseable=0)
        eff = forecast_effectiveness(root)
        assert eff["baseline"]["ced_raw"] == pytest.approx(5.0), (
            "不可解析章不得把基线均值拉低"
        )

    def test_forecast_without_selection_counts_as_baseline(self, tmp_path):
        """只生成了候选但没选支的章，不算「用了预报」。"""
        root = _project(tmp_path)
        for ch in range(1, MIN_CHAPTERS_PER_SIDE + 1):
            _metrics(root, ch, 2.0)
        # 生成候选但无 selected.json
        for ch in range(1, MIN_CHAPTERS_PER_SIDE + 1):
            d = root / ".story-system" / "forecasts" / f"chapter_{ch:03d}"
            d.mkdir(parents=True, exist_ok=True)
            (d / "manifest.json").write_text("{}", encoding="utf-8")
        eff = forecast_effectiveness(root)
        assert eff["forecast"]["chapters"] == 0
        assert eff["baseline"]["chapters"] == MIN_CHAPTERS_PER_SIDE

    def test_missing_index_db(self, tmp_path):
        root = tmp_path
        (root / ".webnovel").mkdir(parents=True)
        eff = forecast_effectiveness(root)
        assert eff["status"] == "insufficient_data"


class TestDashboardEndpoint:
    """/api/effectiveness/stats。"""

    @pytest.fixture
    def client(self, tmp_path):
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        root = _project(tmp_path)
        _commit(root, 1, [_flag(triage="dismissed"), _flag(triage="confirmed")])

        if str(SCRIPTS.parent) not in sys.path:
            sys.path.insert(0, str(SCRIPTS.parent))
        from fastapi.testclient import TestClient
        from dashboard.app import create_app

        return TestClient(create_app(project_root=root),
                          base_url="http://127.0.0.1:8765"), root

    def test_returns_both_blocks(self, client):
        c, _root = client
        r = c.get("/api/effectiveness/stats")
        assert r.status_code == 200
        data = r.json()
        assert "flag_triage" in data and "forecast_effectiveness" in data
        assert data["flag_triage"]["dismiss_rate"] == 0.5
        assert data["forecast_effectiveness"]["status"] == "insufficient_data"

    def test_empty_project_does_not_crash(self, tmp_path):
        pytest.importorskip("fastapi")
        pytest.importorskip("httpx")
        (tmp_path / ".webnovel").mkdir(parents=True)
        (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
        if str(SCRIPTS.parent) not in sys.path:
            sys.path.insert(0, str(SCRIPTS.parent))
        from fastapi.testclient import TestClient
        from dashboard.app import create_app

        c = TestClient(create_app(project_root=tmp_path),
                       base_url="http://127.0.0.1:8765")
        r = c.get("/api/effectiveness/stats")
        assert r.status_code == 200
        assert r.json()["flag_triage"]["dismiss_rate"] is None
