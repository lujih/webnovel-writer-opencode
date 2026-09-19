#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Doctor — 项目健康诊断工具。

检查项目完整性、配置正确性、数据一致性。
输出结构化诊断报告，包含 blocking/warning 计数和修复建议。
"""
from __future__ import annotations

import importlib
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _check(name: str, passed: bool, severity: str, detail: str = "", repair: str = "") -> Dict[str, Any]:
    """标准化检查结果。"""
    result = {
        "name": name,
        "status": "pass" if passed else ("block" if severity == "blocking" else "warn"),
        "detail": detail,
    }
    if repair:
        result["repair"] = repair
    return result


def _file_checks(project_root: Path) -> List[Dict[str, Any]]:
    """文件/目录存在性检查。"""
    checks = []

    # 必需目录
    required_dirs = [
        (".webnovel", "运行时数据目录"),
        (".story-system", "故事合约目录"),
        ("正文", "章节正文目录"),
    ]
    for rel, desc in required_dirs:
        path = project_root / rel
        checks.append(_check(
            f"目录: {rel}",
            path.is_dir(),
            "blocking",
            f"{desc} {'存在' if path.is_dir() else '缺失'}",
            f"创建目录: mkdir -p {rel}",
        ))

    # 必需文件
    required_files = [
        (".webnovel/state.json", "项目状态文件"),
        (".story-system/MASTER_SETTING.json", "主设定文件"),
    ]
    for rel, desc in required_files:
        path = project_root / rel
        checks.append(_check(
            f"文件: {rel}",
            path.is_file(),
            "blocking",
            f"{desc} {'存在' if path.is_file() else '缺失'}",
            f"运行 story-system 生成合同",
        ))

    # 可选文件
    optional_files = [
        (".webnovel/index.db", "实体索引数据库"),
        (".webnovel/memory_scratchpad.json", "记忆暂存"),
    ]
    for rel, desc in optional_files:
        path = project_root / rel
        if path.is_file():
            checks.append(_check(f"文件: {rel}", True, "info", f"{desc} 存在"))
        else:
            checks.append(_check(f"文件: {rel}", False, "warning", f"{desc} 缺失"))

    return checks


def _json_checks(project_root: Path) -> List[Dict[str, Any]]:
    """JSON 文件解析检查。"""
    checks = []

    # state.json
    state_path = project_root / ".webnovel" / "state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            required_keys = ["project_info", "progress"]
            missing_keys = [k for k in required_keys if k not in state]
            if missing_keys:
                checks.append(_check(
                    "state.json 结构",
                    False,
                    "warning",
                    f"缺少字段: {', '.join(missing_keys)}",
                ))
            else:
                checks.append(_check("state.json 结构", True, "info", "结构完整"))
        except (json.JSONDecodeError, OSError) as e:
            checks.append(_check(
                "state.json 解析",
                False,
                "blocking",
                f"解析失败: {e}",
                "检查文件编码或修复 JSON 格式",
            ))

    # MASTER_SETTING.json
    master_path = project_root / ".story-system" / "MASTER_SETTING.json"
    if master_path.is_file():
        try:
            master = json.loads(master_path.read_text(encoding="utf-8"))
            if "master_constraints" not in master:
                checks.append(_check(
                    "MASTER_SETTING 结构",
                    False,
                    "warning",
                    "缺少 master_constraints 字段",
                ))
            else:
                checks.append(_check("MASTER_SETTING 结构", True, "info", "结构完整"))
        except (json.JSONDecodeError, OSError) as e:
            checks.append(_check(
                "MASTER_SETTING 解析",
                False,
                "blocking",
                f"解析失败: {e}",
            ))

    return checks


def _sqlite_checks(project_root: Path) -> List[Dict[str, Any]]:
    """SQLite 数据库检查。"""
    checks = []

    db_path = project_root / ".webnovel" / "index.db"
    if not db_path.is_file():
        checks.append(_check("index.db", False, "warning", "文件不存在"))
        return checks

    try:
        conn = sqlite3.connect(str(db_path))
        try:
            # 检查核心表
            tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]

            required_tables = ["entities", "relationships"]
            for table in required_tables:
                if table in tables:
                    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    checks.append(_check(
                        f"表 {table}",
                        True,
                        "info",
                        f"{count} 行",
                    ))
                else:
                    checks.append(_check(
                        f"表 {table}",
                        False,
                        "warning",
                        "表不存在",
                        f"运行 webnovel.py migrate 创建表",
                    ))

            # 检查索引完整性（PRAGMA integrity_check 返回结果集，需检查内容）
            try:
                integrity_result = conn.execute("PRAGMA integrity_check").fetchall()
                is_ok = len(integrity_result) == 1 and integrity_result[0][0] == "ok"
                if is_ok:
                    checks.append(_check("index.db 完整性", True, "info", "通过"))
                else:
                    detail = "; ".join(str(row[0]) for row in integrity_result[:3])
                    checks.append(_check(
                        "index.db 完整性",
                        False,
                        "blocking",
                        f"完整性检查未通过: {detail}",
                        "运行 webnovel.py migrate 修复",
                    ))
            except Exception as e:
                checks.append(_check(
                    "index.db 完整性",
                    False,
                    "blocking",
                    f"完整性检查失败: {e}",
                    "运行 webnovel.py migrate 修复",
                ))
        finally:
            conn.close()
    except Exception as e:
        checks.append(_check(
            "index.db 连接",
            False,
            "blocking",
            f"无法打开数据库: {e}",
        ))

    return checks


def _projection_checks(project_root: Path) -> List[Dict[str, Any]]:
    """投影状态检查。"""
    checks = []

    # 检查 projection 目录
    webnovel_dir = project_root / ".webnovel"
    summary_dir = webnovel_dir / "summaries"
    if summary_dir.is_dir():
        summaries = list(summary_dir.glob("*.md"))
        checks.append(_check(
            "投影: summaries",
            True,
            "info",
            f"{len(summaries)} 个摘要文件",
        ))
    else:
        checks.append(_check(
            "投影: summaries",
            False,
            "warning",
            "摘要目录不存在",
        ))

    # 检查 state.json 中的 projection 状态
    state_path = webnovel_dir / "state.json"
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            progress = state.get("progress") or {}
            chapter_status = progress.get("chapter_status") or {}

            def _status_of(v) -> str:
                # 兼容两种形状：增量路径写 "chapter_committed"（str），
                # 旧版 rebuild 曾写 {"status": ...}（dict）——统一取 status。
                if isinstance(v, dict):
                    return str(v.get("status") or "")
                return str(v or "")

            committed = sum(1 for v in chapter_status.values() if _status_of(v) == "chapter_committed")
            rejected = sum(1 for v in chapter_status.values() if _status_of(v) == "chapter_rejected")
            checks.append(_check(
                "章节状态",
                True,
                "info",
                f"committed: {committed}, rejected: {rejected}",
            ))
        except Exception as e:
            checks.append(_check(
                "章节状态",
                False,
                "warning",
                f"读取失败: {e}",
            ))

    return checks


def _python_checks() -> List[Dict[str, Any]]:
    """Python 环境检查。"""
    checks = []

    # Python 版本
    version = sys.version_info
    if version >= (3, 10):
        checks.append(_check("Python 版本", True, "info", f"{version.major}.{version.minor}.{version.micro}"))
    else:
        checks.append(_check(
            "Python 版本",
            False,
            "blocking",
            f"{version.major}.{version.minor}.{version.micro}（需要 >= 3.10）",
            "升级 Python 到 3.10+",
        ))

    # 必需模块
    required_modules = ["pydantic", "filelock", "fastapi", "uvicorn"]
    for mod_name in required_modules:
        try:
            importlib.import_module(mod_name)
            checks.append(_check(f"模块: {mod_name}", True, "info", "已安装"))
        except ImportError:
            checks.append(_check(
                f"模块: {mod_name}",
                False,
                "warning",
                "未安装",
                f"pip install {mod_name}",
            ))

    return checks


def build_doctor_report(project_root: Path) -> Dict[str, Any]:
    """构建完整诊断报告。"""
    all_checks = []

    all_checks.extend(_file_checks(project_root))
    all_checks.extend(_json_checks(project_root))
    all_checks.extend(_sqlite_checks(project_root))
    all_checks.extend(_projection_checks(project_root))
    all_checks.extend(_python_checks())

    blocking = sum(1 for c in all_checks if c["status"] == "block")
    warnings = sum(1 for c in all_checks if c["status"] == "warn")

    return {
        "ok": blocking == 0,
        "project_root": str(project_root),
        "blocking": blocking,
        "warnings": warnings,
        "checks": all_checks,
    }


def format_doctor_report(report: Dict[str, Any], fmt: str = "json") -> str:
    """格式化诊断报告。"""
    if fmt == "json":
        return json.dumps(report, ensure_ascii=False, indent=2)

    lines = []
    ok = report.get("ok", False)
    status = "✅ 健康" if ok else "❌ 存在问题"
    lines.append(f"项目诊断: {status}")
    lines.append(f"  阻断: {report.get('blocking', 0)}")
    lines.append(f"  警告: {report.get('warnings', 0)}")
    lines.append("")

    for check in report.get("checks") or []:
        icon = {"pass": "✅", "block": "❌", "warn": "⚠️"}.get(check["status"], "❓")
        lines.append(f"  {icon} {check['name']}: {check.get('detail', '')}")
        if check.get("repair") and check["status"] != "pass":
            lines.append(f"     修复: {check['repair']}")

    return "\n".join(lines)


def main() -> None:
    """CLI 入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="项目健康诊断")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--format", choices=["json", "text"], default="text")
    args = parser.parse_args()

    project_root = Path(args.project_root).expanduser().resolve()
    if not project_root.is_dir():
        print(f"错误: 项目目录不存在: {project_root}", file=sys.stderr)
        raise SystemExit(1)

    report = build_doctor_report(project_root)
    print(format_doctor_report(report, args.format))

    raise SystemExit(0 if report["ok"] else 1)


if __name__ == "__main__":
    main()
