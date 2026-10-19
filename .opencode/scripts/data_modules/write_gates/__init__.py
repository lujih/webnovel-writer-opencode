#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Write Gates — 三阶段写入门禁。

prewrite:  写作前检查（合同完整性、项目阶段）
precommit: 提交前检查（产物验证、chapter file 存在性）
postcommit: 提交后检查（投影完整性、状态一致性）
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def issue(
    code: str,
    severity: str,
    message: str,
    impact: str = "",
    repair: str = "",
    **extra: Any,
) -> Dict[str, Any]:
    """标准化错误/警告对象。

    ``**extra`` 允许调用方附带诊断上下文（如 ``missing=[...]``、
    ``blocking=[...]``、``phase="..."``），原样并入结果对象。

    此前本函数不接受扩展字段，而 `prewrite._check_project_phase` 传了
    ``missing=``、`precommit._check_project_phase` 传了 ``blocking=`` 与
    ``phase=``。两处都在 ``except Exception: pass`` 里，于是每次都抛
    TypeError 又被静默吞掉——那两条检查**从未真正执行过**。
    """
    result: Dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
    }
    if impact:
        result["impact"] = impact
    if repair:
        result["repair"] = repair
    for key, value in extra.items():
        result[key] = value
    return result


def gate_report(
    stage: str,
    errors: List[Dict[str, Any]] | None = None,
    warnings: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """结构化报告构建器。"""
    errors = errors or []
    warnings = warnings or []
    has_blocker = any(e.get("severity") == "error" for e in errors)
    return {
        "schema_version": "write-gate/v1",
        "stage": stage,
        "ok": not has_blocker,
        "errors": errors,
        "warnings": warnings,
        "error_count": len(errors),
        "warning_count": len(warnings),
    }


def format_gate_report(report: Dict[str, Any], fmt: str = "json") -> str:
    """格式化门禁报告。"""
    if fmt == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)

    # text 格式
    lines = []
    stage = report.get("stage", "unknown")
    ok = report.get("ok", False)
    status = "✅ 通过" if ok else "❌ 失败"
    lines.append(f"[{stage}] {status}")
    lines.append(f"  错误: {report.get('error_count', 0)}")
    lines.append(f"  警告: {report.get('warning_count', 0)}")

    for err in report.get("errors") or []:
        lines.append(f"  ❌ [{err.get('code')}] {err.get('message')}")
        if err.get("repair"):
            lines.append(f"     修复: {err['repair']}")

    for warn in report.get("warnings") or []:
        lines.append(f"  ⚠️ [{warn.get('code')}] {warn.get('message')}")

    return "\n".join(lines)


def run_write_gate(stage: str, project_root: Path, chapter: int) -> Dict[str, Any]:
    """分发器：路由到对应阶段的 gate 检查。"""
    if stage == "prewrite":
        from .prewrite import run_prewrite_gate
        return run_prewrite_gate(project_root, chapter)
    elif stage == "precommit":
        from .precommit import run_precommit_gate
        return run_precommit_gate(project_root, chapter)
    elif stage == "postcommit":
        from .postcommit import run_postcommit_gate
        return run_postcommit_gate(project_root, chapter)
    else:
        return gate_report(stage, errors=[issue(
            "unknown_stage",
            "error",
            f"未知的 gate 阶段: {stage}",
        )])
