#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CED（一致性错误密度）的计算口径与落库。

ConStory-Bench（arXiv 2603.05890）用 CED = 每万词一致性错误数 作为长文
一致性的标准度量。本项目此前只有**过程指标**（清单完成度、审查均分）——
高分只代表"清单项打勾了"，不代表"没有一致性错误"。

本文件钉住三件事：

1. **三个口径的定义与边界**（`review_schema.compute_ced`）
   - ced_raw      = issues / words × 10000
   - ced_weighted = Σ(severity 权重) / words × 10000
   - ced_blocking = blocking issues / words × 10000
   - **分母为 0 时三者皆为 None**，不得除以 0 也不得伪造 0——
     "算不出来"与"算出来是 0"必须区分（后者意味着真的没发现问题）
   - 中文口径：`_count_chapter_words()` 用 `len()` 计**字符**即"字数"，
     与 ConStory 的"每万词"单位不同，不可横向比数值

2. **不可解析的审查输出不产出 CED**
   `_shape_issue()` 会合成一条 critical issue（fail-closed）。若照常算，
   纯解析失败会被算成"发现一致性问题"，趋势图上出现无法解释的尖峰。
   用 `ReviewResult.unparseable` 显式标记，`to_metrics_dict()` 输出
   `review_parseable=0`。

3. **旧库自动迁移**
   本库此前只靠 `CREATE TABLE IF NOT EXISTS` 原地演化，没有任何 ALTER。
   老库的 `review_metrics` 只有 9 列，而新 INSERT 带 chapter_words 等列——
   不迁移的话老用户跑一次 `review-pipeline --save-metrics` 就直接崩。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.index_manager import IndexManager, ReviewMetrics  # noqa: E402
from data_modules.review_schema import (  # noqa: E402
    SEVERITY_WEIGHTS,
    ReviewIssue,
    ReviewResult,
    _shape_issue,
    compute_ced,
)
from review_pipeline import (  # noqa: E402
    _build_review_metrics_record,
    build_review_artifacts,
)


def _issues(*severities: str) -> list[ReviewIssue]:
    return [ReviewIssue(severity=s, description=f"issue-{s}") for s in severities]


class TestCedDefinitions:
    """三个口径的定义。"""

    def test_raw_counts_each_issue_equally(self):
        ced = compute_ced(_issues("critical", "high", "medium", "low"), 10000)
        assert ced["ced_raw"] == pytest.approx(4.0)

    def test_weighted_uses_severity_weights(self):
        # 4.0 + 3.0 + 2.0 + 1.0 = 10.0 → /10000*10000 = 10
        ced = compute_ced(_issues("critical", "high", "medium", "low"), 10000)
        assert ced["ced_weighted"] == pytest.approx(10.0)

    def test_blocking_counts_only_blocking(self):
        # 只有 critical 强制 blocking
        ced = compute_ced(_issues("critical", "high", "low"), 10000)
        assert ced["ced_blocking"] == pytest.approx(1.0)

    def test_scales_per_10k_words(self):
        ced = compute_ced(_issues("critical"), 2000)
        assert ced["ced_raw"] == pytest.approx(5.0)      # 1/2000*10000
        assert ced["ced_weighted"] == pytest.approx(20.0)  # 4/2000*10000

    def test_weights_are_ordered(self):
        assert SEVERITY_WEIGHTS["critical"] > SEVERITY_WEIGHTS["high"]
        assert SEVERITY_WEIGHTS["high"] > SEVERITY_WEIGHTS["medium"]
        assert SEVERITY_WEIGHTS["medium"] > SEVERITY_WEIGHTS["low"]


class TestCedDenominatorSafety:
    """分母为 0 时必须给 None，不能伪造值。"""

    @pytest.mark.parametrize("words", [0, -1])
    def test_zero_words_gives_none(self, words):
        ced = compute_ced(_issues("critical"), words)
        assert ced["ced_raw"] is None
        assert ced["ced_weighted"] is None
        assert ced["ced_blocking"] is None
        assert ced["chapter_words"] == 0

    def test_no_issues_gives_zero_not_none(self):
        """有分母、无问题 → 0.0（真的没发现问题），与 None 是两件事。"""
        ced = compute_ced([], 3000)
        assert ced["ced_raw"] == 0.0
        assert ced["ced_weighted"] == 0.0
        assert ced["ced_blocking"] == 0.0

    def test_empty_both_dimensions(self):
        ced = compute_ced([], 0)
        assert ced["ced_raw"] is None, "无分母时即便无 issue 也算不出 CED"


class TestZeroWordCountFile:
    """0 字数章节（空文件/纯 markdown）取不到分母。"""

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / ".webnovel").mkdir(parents=True)
        (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
        (tmp_path / "正文").mkdir()
        return tmp_path

    def test_empty_chapter_file(self, project):
        (project / "正文" / "第0001章.md").write_text("", encoding="utf-8")
        p = project / "r.md"
        p.write_text('{"issues": [{"severity": "critical", "description": "x"}]}',
                     encoding="utf-8")
        metrics = build_review_artifacts(project, 1, p)["metrics"]
        assert metrics["chapter_words"] == 0
        assert metrics["ced_raw"] is None, (
            "0 字章节算出了 CED——那是除以 0 或伪造值"
        )

    def test_markdown_only_file(self, project):
        (project / "正文" / "第0001章.md").write_text(
            "# 标题\n\n---\n\n```python\ncode\n```\n", encoding="utf-8")
        p = project / "r.md"
        p.write_text('{"issues": []}', encoding="utf-8")
        metrics = build_review_artifacts(project, 1, p)["metrics"]
        assert metrics["chapter_words"] == 0


class TestUnparseableDoesNotProduceCed:
    """纯解析失败不该被算成一致性问题。"""

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / ".webnovel").mkdir(parents=True)
        (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
        (tmp_path / "正文").mkdir()
        (tmp_path / "正文" / "第0001章.md").write_text("正文" * 3000, encoding="utf-8")
        return tmp_path

    @pytest.mark.parametrize("content", [
        "这根本不是 JSON",
        '{"issues": [{"severity": "critical"',     # 截断
        "",
    ])
    def test_unparseable_marked(self, project, content):
        p = project / "bad.md"
        p.write_text(content, encoding="utf-8")
        metrics = build_review_artifacts(project, 1, p)["metrics"]

        assert metrics["review_parseable"] == 0, (
            "审查输出不可解析时没标记 review_parseable=0。"
            "category 不可用作信号——review_output_malformed 会被 "
            "__post_init__ 降级成 other。"
        )
        record = _build_review_metrics_record(metrics)
        assert record.review_parseable == 0
        assert record.ced_raw is None, (
            "解析失败被算成了一致性问题——趋势图会出现虚假尖峰"
        )
        assert record.chapter_words == 6000, "字数（客观事实）仍应记录"

    def test_unparseable_still_blocks(self, project):
        """fail-closed 不能被 CED 改动削弱。"""
        p = project / "bad.md"
        p.write_text("不是 JSON", encoding="utf-8")
        result = build_review_artifacts(project, 1, p)
        assert result["review_result"]["has_blocking"] is True

    def test_normal_path_parseable_one(self, project):
        p = project / "ok.md"
        p.write_text('{"issues": [{"severity": "high", "description": "y"}]}',
                     encoding="utf-8")
        metrics = build_review_artifacts(project, 1, p)["metrics"]
        assert metrics["review_parseable"] == 1
        record = _build_review_metrics_record(metrics)
        assert record.review_parseable == 1
        assert record.ced_raw is not None

    def test_shape_issue_sets_flag(self):
        result = _shape_issue(1, "test")
        assert result.unparseable is True
        assert result.to_metrics_dict()["review_parseable"] == 0

    def test_normal_result_flag_false(self):
        result = ReviewResult(chapter=1, issues=[], summary="ok")
        assert result.to_metrics_dict()["review_parseable"] == 1


class TestParseableFalsyTrap:
    """钉住 `0 or 1` 陷阱：review_parseable=0 是合法值。

    本会话已在 budget.strip() 与 hybrid_search top_k 上踩过同一个 falsy 陷阱，
    这里写死防止再犯。
    """

    def test_zero_survives_record_build(self):
        metrics = {
            "start_chapter": 1, "end_chapter": 1,
            "review_parseable": 0, "chapter_words": 100,
            "ced_raw": None,
        }
        record = _build_review_metrics_record(metrics)
        assert record.review_parseable == 0, (
            "review_parseable=0 被 `or 1` 吃成了 1"
        )

    def test_missing_defaults_to_one(self):
        record = _build_review_metrics_record({"start_chapter": 1, "end_chapter": 1})
        assert record.review_parseable == 1


class TestPersistence:
    """落库与旧库迁移。"""

    @pytest.fixture
    def project(self, tmp_path: Path) -> Path:
        (tmp_path / ".webnovel").mkdir(parents=True)
        (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
        return tmp_path

    def _record(self) -> ReviewMetrics:
        return ReviewMetrics(
            start_chapter=1, end_chapter=1, overall_score=80.0,
            chapter_words=3000, ced_raw=6.667, ced_weighted=13.333,
            ced_blocking=3.333, review_parseable=1,
        )

    def test_roundtrip(self, project):
        cfg = DataModulesConfig.from_project_root(project)
        cfg.ensure_dirs()
        manager = IndexManager(cfg)
        manager.save_review_metrics(self._record())

        rows = manager.get_recent_review_metrics(5)
        assert len(rows) == 1
        row = rows[0]
        assert row["chapter_words"] == 3000
        assert row["ced_raw"] == pytest.approx(6.667, abs=0.01)
        assert row["ced_blocking"] == pytest.approx(3.333, abs=0.01)
        assert row["review_parseable"] == 1

    def test_none_ced_persists_as_null(self, project):
        """None 必须存成 SQL NULL，不能被转成 0。"""
        cfg = DataModulesConfig.from_project_root(project)
        cfg.ensure_dirs()
        manager = IndexManager(cfg)
        manager.save_review_metrics(ReviewMetrics(
            start_chapter=1, end_chapter=1, chapter_words=0,
            ced_raw=None, review_parseable=0))

        rows = manager.get_recent_review_metrics(5)
        assert rows[0]["ced_raw"] is None, "None 被存成了 0——读起来像'没问题'"

    def test_legacy_table_gets_migrated(self, project):
        """老库（9 列）构造 IndexManager 后应自动补齐新列。"""
        db = project / ".webnovel" / "index.db"
        conn = sqlite3.connect(str(db))
        conn.execute("""
            CREATE TABLE review_metrics (
                start_chapter INTEGER NOT NULL, end_chapter INTEGER NOT NULL,
                overall_score REAL DEFAULT 0, dimension_scores TEXT,
                severity_counts TEXT, critical_issues TEXT, report_file TEXT,
                notes TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (start_chapter, end_chapter))
        """)
        conn.commit()
        conn.close()

        # 不迁移的话，这里会抛 sqlite3.OperationalError
        cfg = DataModulesConfig.from_project_root(project)
        cfg.ensure_dirs()
        manager = IndexManager(cfg)          # 构造即迁移
        manager.save_review_metrics(self._record())   # 不抛即通过

        cols = [r[1] for r in sqlite3.connect(str(db)).execute("PRAGMA table_info(review_metrics)")]
        for expected in ("chapter_words", "ced_raw", "ced_weighted",
                         "ced_blocking", "review_parseable"):
            assert expected in cols, f"旧库迁移后缺 {expected}"

    def test_migration_is_idempotent(self, project):
        cfg = DataModulesConfig.from_project_root(project)
        cfg.ensure_dirs()
        IndexManager(cfg)
        IndexManager(cfg)      # 第二次构造不应因 ALTER 重复而失败
        IndexManager(cfg)
