#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Postcommit Gate — 提交后检查。

在 chapter-commit 完成后验证：
1. commit JSON 文件存在且可解析
2. commit status 为 accepted
3. 投影状态检查（优先从 projection_log 读取）
4. 产物文件存在性
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from .. import OK_PROJECTION_STATUSES, REQUIRED_PROJECTION_WRITERS
from . import gate_report, issue


def _check_commit_file(project_root: Path, chapter: int) -> Dict[str, Any]:
    """检查 commit 文件。"""
    commits_dir = project_root / ".story-system" / "commits"
    commit_path = None
    for pattern in [f"chapter_{chapter:03d}.commit.json", f"chapter_{chapter:04d}.commit.json"]:
        p = commits_dir / pattern
        if p.is_file():
            commit_path = p
            break

    if not commit_path:
        return {"ok": False, "errors": [issue(
            "commit_file_missing",
            "error",
            f"第 {chapter} 章 commit 文件不存在",
        )], "commit": None}

    try:
        commit = json.loads(commit_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {"ok": False, "errors": [issue(
            "commit_file_invalid",
            "error",
            f"commit 文件解析失败: {e}",
        )], "commit": None}

    errors = []
    status = str((commit.get("meta") or {}).get("status") or "")
    if status not in ("accepted", "rejected"):
        errors.append(issue(
            "invalid_commit_status",
            "error",
            f"无效的 commit status: {status}",
        ))

    return {"ok": len(errors) == 0, "errors": errors, "commit": commit}


def _get_projection_status(
    project_root: Path, chapter: int, commit: Dict[str, Any]
) -> tuple[Dict[str, str], Exception | None]:
    """获取投影状态。优先从 projection_log 读取（权威记录），fallback 到 commit 文件。

    返回 ``(状态字典, 读取异常或 None)``。第二个返回值让调用方能区分
    「日志说没有 pending」与「日志根本读不到」——前者是健康，后者是盲区。
    不吞异常，也不引入模块级可变状态（并发下会串台）。
    """
    # 优先从 projection_log 读取
    logged_status: Dict[str, str] = {}
    log_error: Exception | None = None
    try:
        from ..projection_log import latest_projection_run, projection_status_from_run
        latest_run = latest_projection_run(project_root, chapter=chapter)
        logged_status = projection_status_from_run(latest_run) or {}
    except Exception as exc:
        # 读不到权威记录不等于「投影没问题」——把原因交给调用方上报。
        log_error = exc

    # 从 commit 文件读取
    raw = commit.get("projection_status")
    commit_status: Dict[str, str] = {}
    if isinstance(raw, dict):
        commit_status = {str(k): str(v) for k, v in raw.items()}

    # 如果 log 有非 pending 数据，优先使用（权威记录）
    if logged_status and any(v != "pending" for v in logged_status.values()):
        return logged_status, log_error

    # 如果 commit 有非 pending 数据，使用 commit（投影完成后 commit 也会更新）
    if commit_status and any(v != "pending" for v in commit_status.values()):
        return commit_status, log_error

    # 都是 pending 或缺失，返回 commit 状态（或空）
    return commit_status, log_error


def _check_projections(project_root: Path, chapter: int, commit: Dict[str, Any]) -> List[Dict[str, Any]]:
    """检查投影完整性。"""
    errors = []
    projection_status, log_error = _get_projection_status(project_root, chapter, commit)
    if log_error is not None:
        errors.append(issue(
            "projection_log_unreadable",
            "warning",
            f"投影日志读取失败，已退回 commit 文件的 projection_status: {log_error}",
            impact="投影状态可能不是最新权威记录",
            repair="排查 .webnovel/ 下 projection 日志是否损坏",
        ))

    # 检查每个必需的 writer
    for writer in REQUIRED_PROJECTION_WRITERS:
        status = str(projection_status.get(writer) or "").strip()
        if not status:
            errors.append(issue(
                "projection_missing",
                "error",
                f"投影 {writer} 状态缺失",
                repair=f"运行 webnovel.py ssot rebuild 补跑 {writer}",
            ))
        elif status.startswith("failed"):
            errors.append(issue(
                "projection_failed",
                "error",
                f"投影 {writer} 失败: {status}",
                repair=f"运行 webnovel.py ssot rebuild 补跑 {writer}",
            ))
        elif status == "pending":
            errors.append(issue(
                "projection_pending",
                "warning",
                f"投影 {writer} 仍在 pending",
                repair=f"等待投影完成或运行 webnovel.py ssot rebuild",
            ))
        elif status not in OK_PROJECTION_STATUSES:
            errors.append(issue(
                "projection_invalid",
                "warning",
                f"投影 {writer} 状态非法: {status}",
                repair="只接受 done 或 skipped 状态",
            ))

    # 检查 summary 文件
    if chapter > 0:
        summary_file = project_root / ".webnovel" / "summaries" / f"ch{chapter:04d}.md"
        if projection_status.get("summary") == "done" and not summary_file.is_file():
            errors.append(issue(
                "summary_file_missing",
                "warning",
                f"投影声称 summary done 但文件不存在: {summary_file.name}",
            ))

    # 检查 index.db
    index_db = project_root / ".webnovel" / "index.db"
    if projection_status.get("index") == "done" and not index_db.is_file():
        errors.append(issue(
            "index_db_missing",
            "warning",
            "投影声称 index done 但 index.db 不存在",
        ))

    # 检查 scratchpad（warning 级别）
    scratchpad = project_root / ".webnovel" / "memory_scratchpad.json"
    if projection_status.get("memory") == "done" and not scratchpad.is_file():
        errors.append(issue(
            "scratchpad_missing",
            "warning",
            "投影声称 memory done 但 memory_scratchpad.json 不存在",
        ))

    return errors


def run_postcommit_gate(project_root: Path, chapter: int) -> Dict[str, Any]:
    """运行 postcommit gate。"""
    errors = []
    warnings = []

    # 项目阶段检测（注入诊断上下文）
    phase_info = {}
    try:
        from ..project_phase import resolve_project_phase
        snapshot = resolve_project_phase(project_root, chapter=chapter)
        phase_info = {
            "phase": snapshot.phase,
            "blocking": list(snapshot.blocking or []),
            "warnings": list(snapshot.warnings or []),
        }
    except Exception as exc:
        # fail-closed：阶段未知时不能当成健康。postcommit 失败只降级为
        # warning（提交已落盘，不该回滚），但必须让操作者看见。
        warnings.append(issue(
            "phase_check_failed",
            "warning",
            f"项目阶段检查失败: {exc}",
            repair="排查 project_phase 模块或项目元数据损坏",
        ))

    # commit 文件检查
    commit_check = _check_commit_file(project_root, chapter)
    for err in commit_check.get("errors") or []:
        if err["severity"] == "error":
            errors.append(err)
        else:
            warnings.append(err)

    if not commit_check["ok"]:
        result = gate_report("postcommit", errors, warnings)
        result["phase"] = phase_info
        return result

    commit = commit_check["commit"]

    # 投影检查（优先从 projection_log 读取）
    proj_issues = _check_projections(project_root, chapter, commit)
    for i in proj_issues:
        if i["severity"] == "error":
            errors.append(i)
        else:
            warnings.append(i)

    result = gate_report("postcommit", errors, warnings)
    result["phase"] = phase_info
    return result
