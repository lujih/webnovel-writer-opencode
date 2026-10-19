#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Precommit Gate — 提交前检查。

在 chapter-commit 运行前验证：
1. chapter file 存在且非空
2. 4 个产物文件存在且格式正确
3. 产物策略检查（blocking/missed_cbn/pending）
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from . import gate_report, issue

# 阻断提交的项目阶段
_BLOCKED_PRECOMMIT_PHASES = frozenset({
    "no_project",
    "init_scaffolded",
    "projection_failed",
})


def _check_project_phase(project_root: Path) -> List[Dict[str, Any]]:
    """检查项目阶段。"""
    errors = []
    try:
        from ..project_phase import resolve_project_phase
        snapshot = resolve_project_phase(project_root)
        if snapshot.phase in _BLOCKED_PRECOMMIT_PHASES:
            blocking_info = list(snapshot.blocking or [])
            errors.append(issue(
                "invalid_phase",
                "error",
                f"项目阶段 {snapshot.phase} 阻断提交",
                repair="先修复阻断问题",
                phase=snapshot.phase,
                blocking=blocking_info,
            ))
    except Exception as exc:
        # fail-closed：阶段未知时不能当成可提交。
        # 下方 _check_chapter_file 已是这个模式，此处对齐。
        errors.append(issue(
            "phase_check_failed",
            "error",
            f"项目阶段检查失败: {exc}",
            repair="排查 project_phase 模块或项目元数据损坏",
        ))
    return errors


def _check_chapter_file(project_root: Path, chapter: int) -> List[Dict[str, Any]]:
    """检查 chapter file 存在且非空。"""
    errors = []
    try:
        from chapter_paths import find_chapter_file
        chapter_file = find_chapter_file(project_root, chapter)
        if not chapter_file or not chapter_file.is_file():
            errors.append(issue(
                "chapter_file_missing",
                "error",
                f"第 {chapter} 章文件不存在",
                repair="运行 chapter-writer-agent 生成章节",
            ))
        elif chapter_file.is_file():
            content = chapter_file.read_text(encoding="utf-8")
            if len(content.strip()) < 100:
                errors.append(issue(
                    "chapter_file_empty",
                    "error",
                    f"第 {chapter} 章文件内容过短（{len(content.strip())} 字符）",
                ))
    except Exception as e:
        errors.append(issue(
            "chapter_file_check_failed",
            "error",
            f"检查章节文件失败: {e}",
        ))
    return errors


def _check_artifacts(project_root: Path, chapter: int) -> Dict[str, Any]:
    """验证 4 个产物文件。"""
    from ..artifact_validator import validate_commit_artifact_files
    return validate_commit_artifact_files(project_root, chapter)


def run_precommit_gate(project_root: Path, chapter: int) -> Dict[str, Any]:
    """运行 precommit gate。"""
    errors = []
    warnings = []

    # 项目阶段检查
    phase_issues = _check_project_phase(project_root)
    for i in phase_issues:
        if i["severity"] == "error":
            errors.append(i)
        else:
            warnings.append(i)
    if errors:
        return gate_report("precommit", errors, warnings)

    # chapter file 检查
    chapter_issues = _check_chapter_file(project_root, chapter)
    for i in chapter_issues:
        if i["severity"] == "error":
            errors.append(i)
        else:
            warnings.append(i)

    # 产物验证
    artifact_report = _check_artifacts(project_root, chapter)
    for err in artifact_report.get("errors") or []:
        errors.append(err)
    for warn in artifact_report.get("warnings") or []:
        warnings.append(warn)

    return gate_report("precommit", errors, warnings)
