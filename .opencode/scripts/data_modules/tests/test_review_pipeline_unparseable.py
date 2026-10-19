#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`build_review_artifacts` 对不可解析审查输出的处理。

审查链路的两条路径对本仓库的 fail-closed 原则执行得**不一致**：

    # JSON 但 issues 畸形  →  fail-closed，合成 critical 阻断项 ✅
    build_review_artifacts(root, 1, '{"issues": "不是数组"}')
    → has_blocking=True, blocking_count=1

    # 根本不是 JSON      →  直接抛 ValueError ❌
    build_review_artifacts(root, 1, '本章写得很好，没有问题。')
    → ValueError: no valid JSON found in reviewer output

两种情形都是「无法判断这一章有没有问题」。既有代码对第一种的处理（见
`review_schema._shape_issue` 的 docstring）已经说明了正确立场：

    形状异常意味着「无法判断这一章有没有问题」，而不是「没有问题」。
    ……这里合成一条 critical + blocking 的问题，把判定交回人工/修复循环。

非 JSON 输出是同一个问题的更极端版本：模型没按格式回，连"畸形 JSON"
都算不上。抛出 ValueError 的后果是**审查流程中断、不产出任何
review_result**，而下游 `chapter-commit` 需要它——整章卡死，且用户看到的
是 "no valid JSON found" 这种内部术语。

这类输出在真实使用里很常见：模型可能回一段散文评价、可能被截断、
可能先输出"让我分析一下"再忘了给 JSON。

本文件钉住：不可解析的输出必须走与畸形 JSON 相同的 fail-closed 路径，
产出带明确原因的阻断结果，而不是抛异常。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from review_pipeline import build_review_artifacts  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _review_file(project: Path, content: str) -> Path:
    path = project / "review_output.md"
    path.write_text(content, encoding="utf-8")
    return path


class TestParseableOutputStillWorks:
    """正常路径不能改坏。"""

    def test_clean_json(self, project):
        path = _review_file(project, '{"blocking_count": 0, "issues": []}')
        result = build_review_artifacts(project, 1, path)
        assert result["review_result"]["has_blocking"] is False

    def test_json_in_code_fence(self, project):
        path = _review_file(project, '```json\n{"issues": []}\n```')
        result = build_review_artifacts(project, 1, path)
        assert "review_result" in result

    def test_json_with_surrounding_prose(self, project):
        path = _review_file(project, '以下是审查结果：\n{"issues": []}\n以上。')
        result = build_review_artifacts(project, 1, path)
        assert "review_result" in result

    def test_critical_issue_blocks(self, project):
        path = _review_file(project,
                            '{"issues": [{"severity": "critical", "description": "设定矛盾"}]}')
        result = build_review_artifacts(project, 1, path)
        assert result["review_result"]["has_blocking"] is True


class TestMalformedJsonFailsClosed:
    """既有正确行为：JSON 畸形 → 阻断（不是抛异常）。"""

    def test_issues_not_array(self, project):
        path = _review_file(project, '{"issues": "不是数组"}')
        result = build_review_artifacts(project, 1, path)
        assert result["review_result"]["has_blocking"] is True

    def test_toplevel_not_object(self, project):
        path = _review_file(project, '[1, 2, 3]')
        try:
            result = build_review_artifacts(project, 1, path)
        except Exception:
            pytest.skip("顶层数组当前抛异常，由下一条用例单独覆盖")
        assert result["review_result"]["has_blocking"] is True


class TestUnparseableOutputFailsClosed:
    """核心：完全不是 JSON 时也必须走 fail-closed，而不是抛异常。"""

    @pytest.mark.parametrize("content,label", [
        ("本章写得很好，没有问题。", "纯散文"),
        ("", "空输出"),
        ('{"issues": [{"severity": "critical"', "截断的 JSON"),
        ("让我先分析一下这一章……（然后忘了给 JSON）", "模型跑题"),
        ("null", "JSON null"),
        ("[1, 2, 3]", "顶层数组"),
    ])
    def test_does_not_raise(self, project, content, label):
        path = _review_file(project, content)
        try:
            build_review_artifacts(project, 1, path)
        except Exception as exc:
            pytest.fail(
                f"审查输出不可解析（{label}）时抛了 {type(exc).__name__}: {exc}。"
                f"这会让审查流程中断、不产出 review_result，下游 chapter-commit "
                f"拿不到它——整章卡死。应走与「畸形 JSON」相同的 fail-closed 路径。"
            )

    def test_blocks(self, project):
        path = _review_file(project, "本章写得很好，没有问题。")
        result = build_review_artifacts(project, 1, path)
        assert result["review_result"]["has_blocking"] is True, (
            "不可解析的审查输出被当成「没有问题」放行了"
        )

    def test_reason_is_explained(self, project):
        path = _review_file(project, "本章写得很好，没有问题。")
        result = build_review_artifacts(project, 1, path)
        blob = str(result["review_result"])
        assert any(k in blob for k in ("无法", "不可解析", "格式", "malformed",
                                       "unparseable", "review_output")), (
            f"阻断结果里没说清为什么阻断：{blob[:400]}"
        )

    def test_still_returns_full_artifact_shape(self, project):
        """即使阻断，也要返回下游需要的完整结构，不能只有半个 dict。"""
        path = _review_file(project, "不是 JSON")
        result = build_review_artifacts(project, 1, path)
        for key in ("chapter", "review_result", "metrics", "overall_score",
                    "system_health"):
            assert key in result, f"返回结构缺 {key}：{sorted(result)}"

    def test_metrics_are_computed(self, project):
        path = _review_file(project, "不是 JSON")
        result = build_review_artifacts(project, 1, path)
        assert isinstance(result["metrics"], dict)
        assert "overall_score" in result["metrics"]

    def test_report_can_be_rendered(self, project):
        """阻断结果也要能渲染成报告，否则用户看不到原因。"""
        from review_pipeline import render_review_report

        path = _review_file(project, "不是 JSON")
        result = build_review_artifacts(project, 1, path)
        # render_review_report 接收完整 artifacts（含 review_result + metrics）
        text = render_review_report(result)
        assert text.strip(), "报告为空"
        assert any(k in text for k in ("无法", "阻断", "critical", "格式"))


class TestEmptyFileIsDistinguished:
    """空文件与"内容是散文"都不可解析，但原因不同，都该阻断。"""

    def test_empty_file_blocks(self, project):
        path = _review_file(project, "")
        result = build_review_artifacts(project, 1, path)
        assert result["review_result"]["has_blocking"] is True

    def test_whitespace_only_blocks(self, project):
        path = _review_file(project, "   \n\t\n  ")
        result = build_review_artifacts(project, 1, path)
        assert result["review_result"]["has_blocking"] is True
