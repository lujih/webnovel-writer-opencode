#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Artifact Validator — 提交前产物完整性验证。

验证 chapter-commit 流程中的 4 个 JSON 产物：
- review_result
- fulfillment_result
- disambiguation_result
- extraction_result

每个产物经过两层检查：
1. Schema 验证（Pydantic 模型）
2. 策略检查（领域规则）
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from . import OK_PROJECTION_STATUSES, REQUIRED_PROJECTION_WRITERS
from .chapter_commit_schema import (
    DisambiguationResult,
    ExtractionResult,
    FulfillmentResult,
    ReviewResult,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SCHEMA_VERSION = "webnovel-artifact-validator/v1"

ERROR_SCHEMA = "schema_error"
ERROR_MISSING = "missing_artifact"
ERROR_BLOCKING_REVIEW = "blocking_review"
ERROR_MISSED_OUTLINE_NODE = "missed_outline_node"
ERROR_PENDING_DISAMBIGUATION = "pending_disambiguation"
ERROR_PROJECTION_FAILURE = "projection_failure"
ERROR_PROJECTION_INCOMPLETE = "projection_incomplete"

# 产物名称 → Pydantic 模型映射
ARTIFACT_SCHEMAS = {
    "review_result": ReviewResult,
    "fulfillment_result": FulfillmentResult,
    "disambiguation_result": DisambiguationResult,
    "extraction_result": ExtractionResult,
}

# 产物文件名映射
ARTIFACT_FILES = {
    "review_result": "review_results.json",
    "fulfillment_result": "fulfillment_result.json",
    "disambiguation_result": "disambiguation_result.json",
    "extraction_result": "extraction_result.json",
}


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def _issue(
    code: str,
    severity: str,
    message: str,
    *,
    path: str = "",
    field: str = "",
    impact: str = "",
    repair: str = "",
    **extra: Any,
) -> Dict[str, Any]:
    """标准化错误/警告对象。"""
    issue: Dict[str, Any] = {
        "code": code,
        "severity": severity,
        "message": message,
    }
    if path:
        issue["path"] = path
    if field:
        issue["field"] = field
    if impact:
        issue["impact"] = impact
    if repair:
        issue["repair"] = repair
    issue.update(extra)
    return issue


def _empty_report(artifact: str, path: str = "") -> Dict[str, Any]:
    """返回标准化的空报告骨架。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact": artifact,
        "path": path,
        "ok": True,
        "errors": [],
        "warnings": [],
        "payload": None,
    }


def _read_json_artifact(path: str | Path) -> tuple[Any, Dict[str, Any] | None]:
    """统一读取 JSON artifact 文件，返回 (data, error_issue)。"""
    artifact_path = Path(path)
    if not artifact_path.is_file():
        return None, _issue(
            ERROR_MISSING,
            "error",
            f"artifact 缺失: {artifact_path}",
            path=str(artifact_path),
            impact="提交前 artifact 不完整，无法可靠生成 chapter commit。",
            repair="重新运行 reviewer/data-agent，或按 schema 补齐该 JSON 文件。",
        )
    try:
        return json.loads(artifact_path.read_text(encoding="utf-8")), None
    except json.JSONDecodeError as exc:
        return None, _issue(
            ERROR_SCHEMA,
            "error",
            f"JSON 解析失败: {exc}",
            path=str(artifact_path),
            impact="artifact 无法被 runtime 读取。",
            repair="修复 JSON 格式，确保文件为 UTF-8。",
        )
    except OSError as exc:
        return None, _issue(
            ERROR_SCHEMA,
            "error",
            f"文件读取失败: {exc}",
            path=str(artifact_path),
            impact="artifact 无法被 runtime 读取。",
            repair="检查文件权限和路径是否正确。",
        )


def _schema_error_message(exc: Exception) -> str:
    """将 Pydantic ValidationError 转换为可读消息。"""
    if isinstance(exc, ValidationError):
        return "; ".join(str(error.get("msg") or "") for error in exc.errors()) or str(exc)
    return str(exc)


def merge_reports(
    reports: List[Dict[str, Any]],
    *,
    artifact: str = "chapter_commit_inputs",
) -> Dict[str, Any]:
    """合并多个验证报告。"""
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    payloads: Dict[str, Any] = {}
    for report in reports:
        errors.extend(report.get("errors") or [])
        warnings.extend(report.get("warnings") or [])
        if report.get("payload") is not None:
            payloads[str(report.get("artifact"))] = report.get("payload")
    return {
        "schema_version": SCHEMA_VERSION,
        "artifact": artifact,
        "ok": not any(item.get("severity") in ("blocker", "error") for item in errors),
        "errors": errors,
        "warnings": warnings,
        "payloads": payloads,
        "reports": reports,
    }


# ---------------------------------------------------------------------------
# 策略检查
# ---------------------------------------------------------------------------

def _policy_issues(artifact_name: str, payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """领域策略检查 — 超越 schema 验证的业务规则。"""
    issues: List[Dict[str, Any]] = []

    if artifact_name == "review_result":
        # 门禁分层（2026-10）后，review_result 的 blocking issue 分两类：
        #   ① 技术故障信号——`_shape_issue` 合成项（category=review_output_malformed
        #      或描述命中固定前缀）。审查输出不可解析 = 「无法判断有没有问题」，
        #      静默放行会让被否章节以满分通过进 SSOT → 仍 error 阻断。
        #   ② LLM 语义判定——模型自己报的 critical/high。LLM 的 critical 不可靠
        #      （误判会杀死好章节）→ 降级为 warning（观察标记），由 flag-triage
        #      人工裁决，不再阻断提交。
        # 修复前：所有 blocking issue 一律 error——一次 LLM 误判或一次 JSON 解析
        # 失败都会拒章。
        from .review_schema import (
            MALFORMED_CATEGORY,
            MALFORMED_DESCRIPTION_PREFIX,
            UNPARSEABLE_DESCRIPTION_PREFIX,
        )
        blocking = 0
        technical = 0
        semantic = 0
        for item in payload.get("issues") or []:
            if not (isinstance(item, dict) and item.get("blocking")):
                continue
            blocking += 1
            category = str(item.get("category") or "")
            description = str(item.get("description") or "")
            # __post_init__ 会把 review_output_malformed 降级成 other，
            # 故用 category 命中 + 描述前缀双重识别技术故障信号。
            if (category == MALFORMED_CATEGORY
                    or description.startswith(MALFORMED_DESCRIPTION_PREFIX)
                    or description.startswith(UNPARSEABLE_DESCRIPTION_PREFIX)):
                technical += 1
            else:
                semantic += 1
        if technical > 0:
            issues.append(_issue(
                ERROR_BLOCKING_REVIEW,
                "error",
                f"审查结果不可解析（{technical} 个技术故障信号），无法判定本章是否存在问题",
                field="issues",
                impact="无法判断有没有问题 ≠ 没有问题，必须人工复核。",
                repair="重新运行审查或人工补写审查 JSON。",
                blocking_count=technical,
            ))
        if semantic > 0:
            issues.append(_issue(
                ERROR_BLOCKING_REVIEW,
                "warning",
                f"审查报出 {semantic} 个阻断级语义问题（LLM 判定，可信度有限）",
                field="issues",
                impact="已降级为观察标记，不阻断提交；由 flag-triage 人工裁决。",
                repair="用 `webnovel.py flag-triage list` 查看并逐条 confirm/dismiss。",
                blocking_count=semantic,
            ))

    elif artifact_name == "fulfillment_result":
        # 只检查 CBN（核心情节点）遗漏
        missed = payload.get("missed_nodes") or []
        cbn = [n for n in missed if isinstance(n, dict) and str(n.get("type", "")).upper() == "CBN"]
        if cbn:
            issues.append(_issue(
                ERROR_MISSED_OUTLINE_NODE,
                "error",
                f"遗漏 {len(cbn)} 个 CBN（核心情节点）",
                field="missed_nodes",
                impact="大纲必须节点未覆盖，提交会把偏离章节固化为事实。",
                repair="补写遗漏节点，或经用户裁决修改本章规划。",
                missed_cbn=cbn,
            ))

    elif artifact_name == "disambiguation_result":
        pending = payload.get("pending") or []
        if pending:
            issues.append(_issue(
                ERROR_PENDING_DISAMBIGUATION,
                "error",
                f"仍有 {len(pending)} 个待消歧项",
                field="pending",
                impact="未消歧实体会污染角色、关系和事件投影。",
                repair="人工确认 pending 项，或把低置信实体从 extraction 中移除。",
                pending_count=len(pending),
            ))

    elif artifact_name == "extraction_result":
        # 注意：payload 已经是 Pydantic 归一化后的扁平 dict，直接 .get() 即可。
        # 若传入原始 commit payload（嵌套结构），需用 extraction_list() 读取。
        events = payload.get("accepted_events") if isinstance(payload, dict) else None
        if not events:
            issues.append(_issue(
                "no_events",
                "warning",
                "未提取到任何事件",
                impact="缺少事件会导致记忆、向量、伏笔投影为空。",
                repair="检查 observer-agent 是否正常运行，或手动补充 extraction_result。",
            ))

    return issues


# ---------------------------------------------------------------------------
# 验证函数
# ---------------------------------------------------------------------------

def validate_artifact_payload(
    artifact_name: str,
    payload: Dict[str, Any],
    *,
    path: str = "",
) -> Dict[str, Any]:
    """验证单个产物的 schema + 策略。"""
    if artifact_name not in ARTIFACT_SCHEMAS:
        report = _empty_report(artifact_name, path)
        report["warnings"].append(_issue(
            "unknown_artifact",
            "warning",
            f"未知产物类型: {artifact_name}",
        ))
        report["ok"] = True
        return report

    report = _empty_report(artifact_name, path)
    schema_cls = ARTIFACT_SCHEMAS[artifact_name]

    # Schema 验证
    try:
        model = schema_cls.model_validate(payload)
    except Exception as exc:
        report["errors"].append(_issue(
            ERROR_SCHEMA,
            "error",
            _schema_error_message(exc),
            path=path,
            impact="artifact 字段形状不符合 chapter commit 权威 schema。",
            repair="按 chapter_commit_schema.py 的顶层字段要求修正。",
        ))
        report["ok"] = False
        return report

    # 归一化后的数据
    normalized = model.model_dump()
    report["payload"] = normalized

    # 策略检查
    for issue in _policy_issues(artifact_name, normalized):
        if issue["severity"] in ("error", "blocker"):
            report["errors"].append(issue)
        else:
            report["warnings"].append(issue)

    report["ok"] = len(report["errors"]) == 0
    return report


def validate_artifact_file(artifact_name: str, path: Path) -> Dict[str, Any]:
    """从文件读取并验证单个产物。"""
    payload, error = _read_json_artifact(path)
    if error:
        report = _empty_report(artifact_name, str(path))
        report["errors"].append(error)
        report["ok"] = False
        return report
    return validate_artifact_payload(artifact_name, payload, path=str(path))


# ---------------------------------------------------------------------------
# 便捷验证函数
# ---------------------------------------------------------------------------

def validate_review_result(path: str | Path) -> Dict[str, Any]:
    return validate_artifact_file("review_result", Path(path))


def validate_fulfillment_result(path: str | Path) -> Dict[str, Any]:
    return validate_artifact_file("fulfillment_result", Path(path))


def validate_disambiguation_result(path: str | Path) -> Dict[str, Any]:
    return validate_artifact_file("disambiguation_result", Path(path))


def validate_extraction_result(path: str | Path) -> Dict[str, Any]:
    return validate_artifact_file("extraction_result", Path(path))


# ---------------------------------------------------------------------------
# 组合验证
# ---------------------------------------------------------------------------

def validate_commit_artifact_files(project_root: Path, chapter: int) -> Dict[str, Any]:
    """验证所有 4 个 commit 产物（CLI 友好签名）。"""
    tmp_dir = project_root / ".webnovel" / "tmp"
    results = {}

    for artifact_name, filename in ARTIFACT_FILES.items():
        path = tmp_dir / filename
        results[artifact_name] = validate_artifact_file(artifact_name, path)

    # 合并报告
    all_errors = []
    all_warnings = []
    for name, result in results.items():
        all_errors.extend(result.get("errors") or [])
        all_warnings.extend(result.get("warnings") or [])

    return {
        "schema_version": SCHEMA_VERSION,
        "ok": len(all_errors) == 0,
        "artifacts": results,
        "errors": all_errors,
        "warnings": all_warnings,
        "error_count": len(all_errors),
        "warning_count": len(all_warnings),
    }


def validate_chapter_commit(project_root: Path, chapter: int) -> Dict[str, Any]:
    """验证已持久化的 chapter commit JSON。

    检查项：
    1. commit 文件存在且可解析
    2. commit 内嵌的 4 个 artifact schema 验证
    3. projection_status 完整性（5 个 writer 全部有状态）
    4. projection_status 合法性（只接受 done/skipped）
    """
    commits_dir = project_root / ".story-system" / "commits"
    commit_path = None
    for pattern in [f"chapter_{chapter:03d}.commit.json", f"chapter_{chapter:04d}.commit.json"]:
        p = commits_dir / pattern
        if p.is_file():
            commit_path = p
            break

    if not commit_path:
        return {
            "ok": False,
            "errors": [_issue("commit_missing", "error", f"第 {chapter} 章 commit 文件不存在")],
            "warnings": [],
        }

    payload, error = _read_json_artifact(commit_path)
    if error:
        return {
            "ok": False,
            "errors": [error],
            "warnings": [],
        }

    if not isinstance(payload, dict):
        return {
            "ok": False,
            "errors": [_issue(ERROR_SCHEMA, "error", "commit 文件必须是 JSON 对象")],
            "warnings": [],
        }

    report = _empty_report("chapter_commit", str(commit_path))
    nested_reports = []

    # 检查 commit 内嵌的 4 个 artifact
    for artifact_name in ARTIFACT_SCHEMAS:
        if artifact_name not in payload:
            report["errors"].append(_issue(
                ERROR_MISSING,
                "error",
                f"commit 缺少 {artifact_name}",
                path=str(commit_path),
                field=artifact_name,
                impact="commit 文件缺少提交 artifact 快照。",
                repair="重新执行 chapter-commit 生成完整 commit。",
            ))
            continue
        nested_reports.append(validate_artifact_payload(
            artifact_name, payload[artifact_name], path=str(commit_path),
        ))

    # 检查投影状态完整性
    projection_status = payload.get("projection_status") or {}
    if not isinstance(projection_status, dict):
        projection_status = {}

    for writer in REQUIRED_PROJECTION_WRITERS:
        status = str(projection_status.get(writer) or "").strip()
        if not status:
            report["errors"].append(_issue(
                ERROR_PROJECTION_INCOMPLETE,
                "error",
                f"投影 {writer} 状态缺失",
                path=str(commit_path),
                field=f"projection_status.{writer}",
                impact="postcommit 必须确认 state/index/summary/memory/vector 五项投影状态。",
                repair="重新执行 chapter-commit 或补跑 projections retry。",
            ))
        elif status.startswith("failed"):
            report["errors"].append(_issue(
                ERROR_PROJECTION_FAILURE,
                "error",
                f"投影 {writer} 失败: {status}",
                path=str(commit_path),
                field=f"projection_status.{writer}",
                impact="提交事实已生成，但 read-model 投影不完整。",
                repair="修复失败原因后补跑 projection retry。",
            ))
        elif status not in OK_PROJECTION_STATUSES:
            report["errors"].append(_issue(
                ERROR_PROJECTION_INCOMPLETE,
                "error",
                f"投影 {writer} 状态非法: {status}",
                path=str(commit_path),
                field=f"projection_status.{writer}",
                impact="postcommit 只接受 projection 状态 done 或 skipped。",
                repair="等待投影完成或补跑 projections retry。",
            ))

    # 合并嵌套 artifact 的错误
    for nested in nested_reports:
        report["errors"].extend(nested.get("errors") or [])
        report["warnings"].extend(nested.get("warnings") or [])

    report["payload"] = payload
    report["ok"] = len(report["errors"]) == 0
    return report


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI 入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="验证 chapter-commit 产物")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--chapter", type=int, required=True)
    parser.add_argument("--format", choices=["json", "text"], default="json")
    args = parser.parse_args()

    project_root = Path(args.project_root).expanduser().resolve()
    report = validate_commit_artifact_files(project_root, args.chapter)

    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        status = "✅ 通过" if report["ok"] else "❌ 失败"
        print(f"产物验证: {status}")
        print(f"  错误: {report['error_count']}")
        print(f"  警告: {report['warning_count']}")
        for err in report["errors"]:
            print(f"  ❌ [{err['code']}] {err['message']}")
        for warn in report["warnings"]:
            print(f"  ⚠️ [{warn['code']}] {warn['message']}")

    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
