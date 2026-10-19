#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Prewrite Gate — 写作前检查。

在 chapter-writer-agent 开始起草前运行，验证：
1. 合同文件存在性
2. 前置条件满足（无 pending disambiguation）
3. chapter file 可写
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from . import gate_report, issue


def _check_project_phase(project_root: Path) -> List[Dict[str, Any]]:
    """检查项目阶段。"""
    errors = []
    try:
        from ..project_phase import (
            PHASE_INIT_SCAFFOLDED,
            PHASE_NO_PROJECT,
            resolve_project_phase,
        )
        snapshot = resolve_project_phase(project_root)
        if snapshot.phase == PHASE_NO_PROJECT:
            errors.append(issue(
                "no_project",
                "error",
                "项目不存在（缺少 state.json）",
                repair="运行 webnovel-init 初始化项目",
            ))
        elif snapshot.phase == PHASE_INIT_SCAFFOLDED:
            missing = list(snapshot.missing_init_files or []) + list(snapshot.missing_init_dirs or [])
            errors.append(issue(
                "init_incomplete",
                "error",
                f"项目初始化不完整，缺少 {len(missing)} 项",
                repair="运行 webnovel-init 补齐缺失文件",
                missing=missing,
            ))
    except Exception as exc:
        # fail-closed：门禁无法完成检查时不能「当作没问题」。
        # 这与 precommit._check_chapter_file 的处理一致——那里早就这么做了，
        # 说明作者知道正确做法，这几处只是漏改。
        errors.append(issue(
            "phase_check_failed",
            "error",
            f"项目阶段检查失败: {exc}",
            repair="排查 project_phase 模块或项目元数据损坏",
        ))
    return errors


def _check_contracts(project_root: Path, chapter: int) -> List[Dict[str, Any]]:
    """检查合同文件存在性。"""
    errors = []
    story_dir = project_root / ".story-system"

    # MASTER_SETTING.json
    master = story_dir / "MASTER_SETTING.json"
    if not master.is_file():
        errors.append(issue(
            "missing_master_setting",
            "error",
            "MASTER_SETTING.json 不存在",
            repair="运行 story-system 生成合同",
        ))

    # chapter contract
    chapter_contract = None
    for pattern in [f"chapter_{chapter:03d}.json", f"chapter_{chapter:04d}.json"]:
        p = story_dir / "chapters" / pattern
        if p.is_file():
            chapter_contract = p
            break
    if not chapter_contract:
        errors.append(issue(
            "missing_chapter_contract",
            "error",
            f"第 {chapter} 章合同不存在",
            repair="运行 story-system --chapter {chapter} 生成合同",
        ))

    # volume contract
    volume_dir = story_dir / "volumes"
    if volume_dir.is_dir():
        volume_files = list(volume_dir.glob("volume_*.json"))
        if not volume_files:
            errors.append(issue(
                "missing_volume_contract",
                "warning",
                "卷合同目录为空",
            ))

    return errors


def _check_chapter_file(project_root: Path, chapter: int) -> List[Dict[str, Any]]:
    """检查 chapter file 状态。"""
    errors = []
    # 通过 chapter_paths 查找
    try:
        try:
            from chapter_paths import find_chapter_file
        except ImportError:  # pragma: no cover
            from scripts.chapter_paths import find_chapter_file
        chapter_file = find_chapter_file(project_root, chapter)
        if chapter_file and chapter_file.is_file():
            content = chapter_file.read_text(encoding="utf-8")
            if len(content.strip()) < 100:
                errors.append(issue(
                    "chapter_file_too_short",
                    "warning",
                    f"章节文件内容过短（{len(content.strip())} 字符）",
                ))
    except FileNotFoundError:
        pass  # 还没写——写作前检查的正常情况
    except Exception as exc:
        errors.append(issue(
            "chapter_file_check_failed",
            "error",
            f"章节文件检查失败: {exc}",
            repair="排查正文目录权限或文件名编码",
        ))

    return errors


def _check_disambiguation(project_root: Path) -> List[Dict[str, Any]]:
    """检查 disambiguation pending。"""
    errors = []
    disambig_file = project_root / ".webnovel" / "tmp" / "disambiguation_result.json"
    if disambig_file.is_file():
        try:
            import json
            data = json.loads(disambig_file.read_text(encoding="utf-8"))
            pending = data.get("pending") or []
            if pending:
                errors.append(issue(
                    "disambiguation_pending",
                    "error",
                    f"仍有 {len(pending)} 个待消歧项",
                    impact="写作时实体引用可能不准确",
                    repair="运行 data-agent 完成消歧",
                ))
        except Exception as exc:
            errors.append(issue(
                "disambiguation_check_failed",
                "error",
                f"消歧结果解析失败: {exc}",
                repair="删除损坏的 .webnovel/tmp/disambiguation_result.json 后重跑 data-agent",
            ))
    return errors


def run_prewrite_gate(project_root: Path, chapter: int) -> Dict[str, Any]:
    """运行 prewrite gate。"""
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
        return gate_report("prewrite", errors, warnings)

    # 合同检查
    contract_issues = _check_contracts(project_root, chapter)
    for i in contract_issues:
        if i["severity"] == "error":
            errors.append(i)
        else:
            warnings.append(i)

    # chapter file 检查
    chapter_issues = _check_chapter_file(project_root, chapter)
    for i in chapter_issues:
        if i["severity"] == "error":
            errors.append(i)
        else:
            warnings.append(i)

    # disambiguation 检查
    disambig_issues = _check_disambiguation(project_root)
    for i in disambig_issues:
        if i["severity"] == "error":
            errors.append(i)
        else:
            warnings.append(i)

    return gate_report("prewrite", errors, warnings)
