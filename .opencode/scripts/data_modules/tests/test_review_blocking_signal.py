#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`review_schema.parse_review_output` 的阻断判据。

`parse_review_output` 有一条明确的设计决定（AGENTS.md 也记录了）：

    通过 parse_review_output 归一化（含 severity→blocking 推导），
    不直接信任 LLM 原始值

方向是对的：模型说 `blocking_count: 0` 但列了 critical issue 时，必须判阻断。
实测该方向成立。

但这个决定**只实现了一半**。当前实现是：

    @property
    def blocking_count(self):
        return sum(1 for i in self.issues if i.blocking)

完全由 `issues` 自算，于是反方向失效：

    {"blocking_count": 3, "issues": []}   →  blocking=False, count=0

模型明确报告了 3 个阻断项、只是没展开明细时，这一章会被**放行**。
而"不信任 LLM 原始值"的本意是「不要因为模型说 0 就放过」，
不是「模型说有也不听」。

这与仓库里其它地方的 fail-closed 取向一致：`review_pipeline` 对形状异常的
载荷合成 critical 阻断项，理由是「无法判断有没有问题 ≠ 没有问题」。
`blocking_count > 0 但 issues 为空` 正是"无法判断具体问题、但已知有问题"
的情形——按同一原则应阻断。

本文件钉住：两个方向的信号都要被尊重，取更严格的一方。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.review_schema import parse_review_output  # noqa: E402


class TestCleanReviewPasses:
    """干净审查必须放行，别把 fail-closed 做成 fail-everything。"""

    def test_empty_issues_and_zero_count(self):
        r = parse_review_output(1, {"blocking_count": 0, "issues": []})
        assert r.has_blocking is False
        assert r.blocking_count == 0

    def test_only_warnings(self):
        r = parse_review_output(1, {
            "blocking_count": 0,
            "issues": [{"severity": "low", "description": "措辞可优化"}],
        })
        assert r.has_blocking is False

    def test_medium_severity_is_not_blocking(self):
        r = parse_review_output(1, {
            "issues": [{"severity": "medium", "description": "节奏偏慢"}],
        })
        assert r.has_blocking is False


class TestIssuesDominateTheCount:
    """方向一（已正确）：issues 里有 critical → 阻断，无视 LLM 的 0。"""

    def test_critical_issue_blocks_despite_zero_count(self):
        r = parse_review_output(1, {
            "blocking_count": 0,
            "issues": [{"severity": "critical", "description": "设定矛盾"}],
        })
        assert r.has_blocking is True

    def test_high_severity_alone_does_not_block(self):
        """既有设计：只有 critical 强制阻断；high 交给修复循环但不阻断提交。

        （`ReviewIssue.__post_init__` 只对 critical 强制 blocking=True。）
        这条钉住现状，避免我把"让 high 也阻断"当成 bug 改掉——那是
        审查策略变更，需单独确认。
        """
        r = parse_review_output(1, {
            "blocking_count": 0,
            "issues": [{"severity": "high", "description": "时间线冲突"}],
        })
        assert r.has_blocking is False

    def test_high_with_count_blocks(self):
        """但模型同时报了 blocking_count 时，按更严格的一方处理。"""
        r = parse_review_output(1, {
            "blocking_count": 1,
            "issues": [{"severity": "high", "description": "时间线冲突"}],
        })
        assert r.has_blocking is True

    def test_explicit_blocking_flag_blocks(self):
        r = parse_review_output(1, {
            "issues": [{"blocking": True, "description": "规则冲突"}],
        })
        assert r.has_blocking is True


class TestCountAloneAlsoBlocks:
    """方向二（缺失）：LLM 说 N 个阻断但没列明细 → 也必须阻断。

    「不信任 LLM 原始值」的本意是不要因为模型说 0 就放过，
    不是模型说有也不听。取更严格的一方。
    """

    def test_count_without_issues_blocks(self):
        r = parse_review_output(1, {"blocking_count": 3, "issues": []})
        assert r.has_blocking is True, (
            "模型报告 3 个阻断项但未展开明细，结果判为『无阻断』——"
            "这一章会被放行。"
        )

    def test_count_without_issues_reports_count(self):
        r = parse_review_output(1, {"blocking_count": 3, "issues": []})
        assert r.blocking_count >= 3, (
            f"阻断计数丢了：模型说 3，实际 {r.blocking_count}"
        )

    def test_count_as_string_is_not_ignored(self):
        """模型常把数字写成字符串。"""
        r = parse_review_output(1, {"blocking_count": "2", "issues": []})
        assert r.has_blocking is True, "blocking_count='2' 被当成 0"

    def test_count_larger_than_listed_issues(self):
        """模型说 5 个阻断，只展开了 1 个 → 仍应按 5 处理（取更严格）。"""
        r = parse_review_output(1, {
            "blocking_count": 5,
            "issues": [{"severity": "critical", "description": "只列了一个"}],
        })
        assert r.blocking_count >= 5 or r.has_blocking is True

    def test_count_negative_is_treated_as_zero(self):
        r = parse_review_output(1, {"blocking_count": -1, "issues": []})
        assert r.has_blocking is False, "负数不该被算成阻断"

    def test_count_garbage_does_not_crash(self):
        for bad in ("abc", None, [], {}, 3.7):
            r = parse_review_output(1, {"blocking_count": bad, "issues": []})
            assert isinstance(r.has_blocking, bool)


class TestMalformedShapesStillFailClosed:
    """形状异常一律阻断（既有行为，别改坏）。"""

    @pytest.mark.parametrize("payload", [
        None,
        ["不是字典"],
        "字符串",
        42,
        {"issues": "不是列表"},
        {"issues": ["不是字典"]},
        {"issues": [{}]},
    ])
    def test_malformed_blocks(self, payload):
        r = parse_review_output(1, payload)
        assert r.has_blocking is True, f"畸形载荷 {payload!r} 未阻断"

    def test_malformed_has_explanatory_issue(self):
        r = parse_review_output(1, {"issues": "不是列表"})
        assert r.issues, "畸形载荷应附带一条说明性 issue，不能只有布尔值"
        text = str(r.issues[0])
        assert any(k in text for k in ("无法", "异常", "invalid", "malformed", "无法判断"))
