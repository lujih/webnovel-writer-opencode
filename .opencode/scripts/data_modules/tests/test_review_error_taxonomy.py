#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一致性错误分类法（对齐 ConStory-Bench）与证据定位。

背景：本项目的 CED 只有三个"总量"口径，能回答「错得多不多」，回答不了
「错在时间线还是设定」。ConStory-Bench（arXiv 2603.05890，微软 + SUTD）
给出 5 类 19 子类型的错误分类法与三阶段证据链评估器，其经验是：
**错误类型分布本身是流程的指纹**——只看总量会把"世界规则崩塌"和
"文风漂移"混成一个数，而两者的修复动作完全不同。

本文件钉住四件事：
1. 分类法完整（5 类 / 19 子类型，映射唯一）；
2. 非法 `error_type` 被清空，而不是降级成 `other` 冒充分类；
3. `evidence_start/end` 半截或倒序时整对清空（半截偏移比没有更危险）；
4. 分类密度（`ced_by_category`）只统计已分类项，未分类只进总量。

同时钉住 reviewer prompt 与代码分类法**同源**：prompt 里少一个子类型，
模型就永远不会输出它，分类趋势图上那一类恒为 0——这正是本项目反复栽的
「声明了但没生效」。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
SCRIPTS = REPO_ROOT / ".opencode" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.review_schema import (  # noqa: E402
    CONSISTENCY_SUBTYPES,
    CONSISTENCY_TAXONOMY,
    SUBTYPE_TO_CATEGORY,
    ReviewIssue,
    compute_ced,
    parse_review_output,
)

REVIEWER_MD = REPO_ROOT / ".opencode" / "agents" / "reviewer.md"


class TestTaxonomyShape:
    def test_five_categories(self):
        assert set(CONSISTENCY_TAXONOMY) == {
            "timeline_plot", "characterization", "worldbuilding",
            "factual_detail", "narrative_style",
        }

    def test_nineteen_subtypes(self):
        assert len(CONSISTENCY_SUBTYPES) == 19

    def test_subtype_mapping_is_bijective(self):
        """每个子类型只能属于一个大类，且 19 个都在映射里。"""
        assert len(SUBTYPE_TO_CATEGORY) == len(CONSISTENCY_SUBTYPES)
        assert set(SUBTYPE_TO_CATEGORY) == set(CONSISTENCY_SUBTYPES)

    def test_subtypes_are_snake_case(self):
        for subtype in CONSISTENCY_SUBTYPES:
            assert re.fullmatch(r"[a-z0-9]+(_[a-z0-9]+)+", subtype), subtype


class TestIssueValidation:
    def test_known_subtype_kept(self):
        issue = ReviewIssue(severity="high", error_type="core_rule_violation")
        assert issue.error_type == "core_rule_violation"

    def test_unknown_subtype_cleared_not_downgraded(self):
        """未知类型必须清空——降级成 other 会让分类统计凭空多一条。"""
        issue = ReviewIssue(severity="high", error_type="world_rule_broken")
        assert issue.error_type == ""

    def test_whitespace_tolerated(self):
        issue = ReviewIssue(severity="low", error_type="  tone_inconsistency  ")
        assert issue.error_type == "tone_inconsistency"

    def test_valid_span_kept(self):
        issue = ReviewIssue(severity="low", evidence_start=10, evidence_end=20)
        assert (issue.evidence_start, issue.evidence_end) == (10, 20)

    def test_half_span_cleared(self):
        assert ReviewIssue(severity="low", evidence_start=10).evidence_start is None
        assert ReviewIssue(severity="low", evidence_end=10).evidence_end is None

    def test_reversed_span_cleared(self):
        issue = ReviewIssue(severity="low", evidence_start=20, evidence_end=10)
        assert (issue.evidence_start, issue.evidence_end) == (None, None)

    def test_negative_or_bool_span_cleared(self):
        for start, end in [(-1, 5), (True, 5), (0, False)]:
            issue = ReviewIssue(severity="low", evidence_start=start, evidence_end=end)
            assert (issue.evidence_start, issue.evidence_end) == (None, None)


class TestMetrics:
    def test_error_type_counts_ignore_unclassified(self):
        result = parse_review_output(1, {"issues": [
            {"severity": "high", "description": "a", "error_type": "skill_fluctuation"},
            {"severity": "low", "description": "b", "error_type": "skill_fluctuation"},
            {"severity": "low", "description": "c"},  # 未分类
        ]})
        assert result.error_type_counts == {"skill_fluctuation": 2}
        assert result.error_category_counts == {"characterization": 2}

    def test_metrics_dict_carries_both_maps(self):
        result = parse_review_output(1, {"issues": [
            {"severity": "critical", "description": "x", "error_type": "absolute_time_contradiction"},
        ]})
        metrics = result.to_metrics_dict()
        assert metrics["error_type_counts"] == {"absolute_time_contradiction": 1}
        assert metrics["error_category_counts"] == {"timeline_plot": 1}

    def test_ced_by_category_density(self):
        issues = [
            ReviewIssue(severity="high", error_type="core_rule_violation"),
            ReviewIssue(severity="low", error_type="geographical_contradiction"),
            ReviewIssue(severity="low"),  # 未分类：只进总量
        ]
        ced = compute_ced(issues, 2000)
        # 2 个已分类 / 2000 字 × 10000 = 10.0
        assert ced["ced_by_category"] == {"worldbuilding": 10.0}
        # 总量包含未分类的那条
        assert ced["ced_raw"] == round(3 / 2000 * 10000, 4)

    def test_ced_by_category_empty_when_uncomputable(self):
        assert compute_ced([], 0)["ced_by_category"] == {}

    def test_ced_by_category_absent_when_nothing_classified(self):
        ced = compute_ced([ReviewIssue(severity="low")], 1000)
        assert ced["ced_by_category"] == {}


@pytest.fixture(scope="module")
def reviewer_prompt() -> str:
    return REVIEWER_MD.read_text(encoding="utf-8")


class TestReviewerPromptParity:
    """prompt 与代码的分类法必须同源。"""

    @pytest.mark.parametrize("subtype", sorted(CONSISTENCY_SUBTYPES))
    def test_prompt_lists_every_subtype(self, reviewer_prompt, subtype):
        assert f"`{subtype}`" in reviewer_prompt, (
            f"reviewer.md 未列出 {subtype}——模型不会输出它，该类密度恒为 0"
        )

    def test_prompt_requires_error_type(self, reviewer_prompt):
        assert '"error_type"' in reviewer_prompt
        assert "19 选 1" in reviewer_prompt or "不得留空" in reviewer_prompt

    def test_prompt_documents_evidence_span(self, reviewer_prompt):
        assert "evidence_start" in reviewer_prompt and "evidence_end" in reviewer_prompt
        assert "start ≤ end" in reviewer_prompt or "start <= end" in reviewer_prompt

    def test_prompt_warns_about_positional_bias(self, reviewer_prompt):
        """外部基准的位置统计要写进 prompt，否则模型只看开头。"""
        assert "40–60%" in reviewer_prompt or "40-60%" in reviewer_prompt
