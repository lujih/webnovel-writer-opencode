#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`structural_checker` 的失败语义。

这是 **Code Checkers 层**——LLM 审查之前的确定性检查，结论直接决定
`blocking` 与最终 `passed`。它服务于 `webnovel-review` 与写-修循环，
所以"检查器自己崩了"不能与"检查通过"混为一谈。

实测缺陷：`state.json` 损坏时 `_load_state()` 直接抛 `JSONDecodeError`：

    run_checks(project_root, 1)
    → json.decoder.JSONDecodeError: Expecting property name ...

CLI 层于是以 traceback 收场（不是"检查未通过"）。而本项目在别处已经
确立了这一层的正确做法：

- `state_manager._read_state_strict()` —— 损坏 → 备份 + 抛 `CorruptStateError`
- `status_reporter.load_state()` —— 签名 `-> bool`，损坏 → 打印修复建议 + False
- `write_gates` —— 检查器异常 → 转 issue 上报（fail-closed）

`structural_checker` 与它们同属"要给出结论"的组件，却让异常穿透。

本文件钉住：
1. 损坏的 state.json 必须变成「检查未通过 + 可读原因」，不是 traceback
2. 判据要 fail-closed（无法判断 ≠ 通过）
3. 正常路径与各项检查的既有语义不变
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.structural_checker import run_checks  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    return tmp_path


class TestMissingStateDoesNotCrash:
    """既有行为：缺 state.json 不崩，走各类检查的默认结论。"""

    def test_returns_report(self, project):
        result = run_checks(project, 1)
        assert isinstance(result, dict)
        assert "checks" in result
        assert "passed" in result

    def test_passed_is_false_when_contract_missing(self, project):
        """没有合同文件时 contract_coverage 应阻断——这是既有语义。"""
        result = run_checks(project, 1)
        assert result["passed"] is False


class TestCorruptStateDoesNotCrash:
    """核心：损坏的 state.json 必须是「检查未通过」，不是异常。"""

    def _write_corrupt(self, project: Path) -> None:
        (project / ".webnovel" / "state.json").write_text("{ bad json", encoding="utf-8")

    def test_does_not_raise(self, project):
        self._write_corrupt(project)
        try:
            run_checks(project, 1)
        except Exception as exc:
            pytest.fail(
                f"state.json 损坏时 run_checks 抛了 {type(exc).__name__}: {exc}。"
                f"这会让审查链路以 traceback 中断，而不是报告「检查未通过」。"
            )

    def test_passed_is_false(self, project):
        self._write_corrupt(project)
        result = run_checks(project, 1)
        assert result["passed"] is False, (
            "state.json 读不出来却报告 passed=True——无法判断不等于通过"
        )

    def test_reports_a_blocking_check(self, project):
        self._write_corrupt(project)
        result = run_checks(project, 1)
        blocking = [c for c in result["checks"]
                    if c.get("severity") == "blocking" and not c.get("passed")]
        assert blocking, (
            f"损坏的 state.json 应产生至少一条 blocking 未通过项，实际：{result['checks']}"
        )

    def test_detail_mentions_the_problem(self, project):
        self._write_corrupt(project)
        result = run_checks(project, 1)
        blob = json.dumps(result, ensure_ascii=False)
        assert "state.json" in blob or "解析" in blob or "读取" in blob, (
            f"报告里没说明是 state.json 的问题：{blob[:400]}"
        )


class TestNormalPathUnchanged:
    """正常 state.json 时各项检查照旧。"""

    def _write_state(self, project: Path, payload: dict) -> None:
        (project / ".webnovel" / "state.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def test_healthy_state_and_contracts(self, project):
        self._write_state(project, {
            "progress": {"current_chapter": 3, "chapter_status": {"3": "chapter_committed"}},
            "plot_threads": {"foreshadowing": []},
            "entity_state": {},
        })
        story = project / ".story-system"
        (story / "chapters").mkdir(parents=True)
        (story / "MASTER_SETTING.json").write_text("{}", encoding="utf-8")
        (story / "chapters" / "chapter_003.json").write_text("{}", encoding="utf-8")

        result = run_checks(project, 3)
        assert "checks" in result
        assert isinstance(result["passed"], bool)

    def test_empty_json_object_is_valid(self, project):
        """`{}` 是合法 JSON，不该被当成损坏。"""
        self._write_state(project, {})
        result = run_checks(project, 1)
        assert isinstance(result, dict)

    def test_json_with_bom_is_readable(self, project):
        """Windows 编辑器写的 UTF-8 BOM 不该被判成损坏。"""
        (project / ".webnovel" / "state.json").write_bytes(
            '\ufeff{"progress": {}}'.encode("utf-8")
        )
        try:
            result = run_checks(project, 1)
        except Exception as exc:
            pytest.fail(f"带 BOM 的合法 JSON 被当成损坏：{type(exc).__name__}: {exc}")
        assert isinstance(result, dict)


class TestCliReportsInsteadOfCrashing:
    """CLI 层面：坏 state.json 不能输出 traceback。"""

    def test_no_traceback(self, tmp_path):
        import subprocess

        book = tmp_path / "book"
        (book / ".webnovel").mkdir(parents=True)
        (book / ".webnovel" / "state.json").write_text("{ bad", encoding="utf-8")

        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "data_modules" / "structural_checker.py"),
             "--project-root", str(book), "--chapter", "1"],
            capture_output=True, text=True, timeout=120,
        )
        combined = r.stdout + r.stderr
        assert "Traceback (most recent call last)" not in combined, (
            f"structural_checker CLI 输出 traceback：\n{combined[-1200:]}"
        )
