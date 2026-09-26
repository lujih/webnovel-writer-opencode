#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
webnovel 统一入口（面向 skills / agents 的稳定 CLI）

设计目标：
- 只有一个入口命令，避免到处拼 `python -m data_modules.xxx ...` 导致参数位置/引号/路径炸裂。
- 自动解析正确的 book project_root（包含 `.webnovel/state.json` 的目录）。
- 所有写入类命令在解析到 project_root 后，统一前置 `--project-root` 传给具体模块。

典型用法（推荐，不依赖 PYTHONPATH / 不要求 cd）：
  python "<SCRIPTS_DIR>/webnovel.py" preflight
  python "<SCRIPTS_DIR>/webnovel.py" where
  python "<SCRIPTS_DIR>/webnovel.py" use D:\\wk\\xiaoshuo\\凡人资本论
  python "<SCRIPTS_DIR>/webnovel.py" --project-root D:\\wk\\xiaoshuo index stats
  python "<SCRIPTS_DIR>/webnovel.py" --project-root D:\\wk\\xiaoshuo state process-chapter --chapter 100 --data @payload.json
  python "<SCRIPTS_DIR>/webnovel.py" --project-root D:\\wk\\xiaoshuo extract-context --chapter 100 --format json

也支持（不推荐，容易踩 PYTHONPATH/cd/参数顺序坑）：
  python -m data_modules.webnovel where
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from runtime_compat import normalize_windows_path
from project_locator import resolve_project_root, write_current_project_pointer, update_global_registry_current_project
from chapter_paths import extract_chapter_num_from_filename

from .story_runtime_health import build_story_runtime_health
from .story_contracts import read_json_if_exists


def _scripts_dir() -> Path:
    # data_modules/webnovel.py -> data_modules -> scripts
    return Path(__file__).resolve().parent.parent


def _resolve_root(explicit_project_root: Optional[str]) -> Path:
    # 允许显式传入工作区根目录或书项目根目录
    raw = explicit_project_root
    if raw:
        return resolve_project_root(raw)
    # 优先从脚本自身位置搜索（解决 CWD != 项目目录时的路径问题），
    # 失败再从 CWD 搜索。
    scripts_ws = _scripts_dir().parent  # .opencode/ → workspace root
    try:
        return resolve_project_root(cwd=scripts_ws)
    except FileNotFoundError:
        return resolve_project_root()


def _strip_project_root_args(argv: list[str]) -> list[str]:
    """
    下游工具统一由本入口注入 `--project-root`，避免重复传参导致 argparse 报错/歧义。
    """
    out: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok == "--project-root":
            i += 2
            continue
        if tok.startswith("--project-root="):
            i += 1
            continue
        out.append(tok)
        i += 1
    return out


PASSTHROUGH_TOOLS = {
    "index",
    "state",
    "rag",
    "style",
    "entity",
    "context",
    "memory",
    "migrate",
    "status",
    "update-state",
    "backup",
    "archive",
    "init",
    "story-system",
    "memory-contract",
    "project-memory",
}


def _passthrough_tail(argv: list[str], tool: str) -> list[str]:
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--project-root":
            i += 2
            continue
        if token.startswith("--project-root="):
            i += 1
            continue
        if token == tool:
            return list(argv[i + 1 :])
        i += 1
    return []


def _run_data_module(module: str, argv: list[str]) -> int:
    """
    Import `data_modules.<module>` and call its main(), while isolating sys.argv.
    """
    from runtime_compat import enable_windows_utf8_stdio
    enable_windows_utf8_stdio()
    mod = importlib.import_module(f"data_modules.{module}")
    main = getattr(mod, "main", None)
    if not callable(main):
        raise RuntimeError(f"data_modules.{module} 缺少可调用的 main()")

    old_argv = sys.argv
    try:
        sys.argv = [f"data_modules.{module}"] + argv
        try:
            main()
            return 0
        except SystemExit as e:
            return int(e.code or 0)
    finally:
        sys.argv = old_argv


def _run_script(script_name: str, argv: list[str]) -> int:
    """
    Run a script under `.opencode/scripts/` via a subprocess.

    用途：兼容没有 main() 的脚本。
    """
    script_path = _scripts_dir() / script_name
    if not script_path.is_file():
        raise FileNotFoundError(f"未找到脚本: {script_path}")
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(script_path), *argv],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    return int(proc.returncode or 0)


def cmd_where(args: argparse.Namespace) -> int:
    try:
        root = _resolve_root(args.project_root)
    except FileNotFoundError as exc:
        print(_project_root_diagnostic(args.project_root, exc), file=sys.stderr)
        return 1
    print(str(root))
    return 0


def cmd_chapter_path(args: argparse.Namespace) -> int:
    """查找章节文件的相对路径（相对于 project_root）。"""
    import re
    root = Path(args.project_root).expanduser().resolve()
    text_dir = root / "正文"
    if not text_dir.is_dir():
        print("ERROR: 正文目录不存在", file=sys.stderr)
        return 1

    pattern = re.compile(rf"第0*{args.chapter}章")
    for f in text_dir.rglob("*.md"):
        if pattern.search(f.name):
            print(str(f.relative_to(root)).replace('\\', '/'))
            return 0

    print(f"ERROR: 未找到第{args.chapter}章的章节文件", file=sys.stderr)
    return 1


def _project_root_diagnostic(
    explicit_project_root: Optional[str], exc: FileNotFoundError
) -> str:
    if explicit_project_root:
        return (
            "未找到有效书项目根目录（需要包含 .webnovel/state.json）: "
            f"{explicit_project_root}\n"
            f"detail: {exc}"
        )
    return (
        "当前工作区还没有激活的书项目（未找到 .webnovel/state.json）。\n"
        "请先运行 webnovel init 创建项目，或运行 webnovel use <project_root> 绑定已有书项目。\n"
        f"detail: {exc}"
    )


def _build_fs_state_sync(project_root: Path) -> dict:
    import re

    story_dir = project_root / ".story-system" / "volumes"
    missing_volumes = []
    if story_dir.is_dir():
        for f in sorted(story_dir.glob("volume_*.json")):
            m = re.match(r"volume_0*(\d+)\.json", f.name)
            if m:
                vol_num = int(m.group(1))
                if vol_num > 1:
                    prev_path = story_dir / f"volume_{vol_num - 1:03d}.json"
                    if not prev_path.is_file():
                        missing_volumes.append(str(prev_path.relative_to(project_root)))
        missing_volumes = sorted(set(missing_volumes))
    if missing_volumes:
        return {
            "name": "fs_state_sync",
            "ok": True,
            "severity": "warning",
            "detail": f"缺失卷文件: {', '.join(missing_volumes[:10])}",
        }

    fs_nums = set()
    text_dir = project_root / "正文"
    if text_dir.is_dir():
        for f in text_dir.rglob("第*章*.md"):
            num = extract_chapter_num_from_filename(f.name)
            if num is not None:
                fs_nums.add(num)

    state = read_json_if_exists(project_root / ".webnovel" / "state.json")
    state_nums = set()
    if state is not None:
        try:
            chapter_status = (state.get("progress") or {}).get("chapter_status") or {}
            state_nums = set(int(k) for k in chapter_status.keys())
        except (ValueError, OSError):
            pass

    orphans = sorted(fs_nums - state_nums)
    ghosts = sorted(state_nums - fs_nums)
    if orphans or ghosts:
        detail_parts = []
        if orphans:
            detail_parts.append(f"孤文件(有正文无状态): {orphans}")
        if ghosts:
            detail_parts.append(f"幽灵章(有状态无正文): {ghosts}")
        return {
            "name": "fs_state_sync",
            "ok": True,
            "severity": "warning",
            "detail": "; ".join(detail_parts),
        }
    return {
        "name": "fs_state_sync",
        "ok": True,
        "severity": "info",
        "detail": f"fs={len(fs_nums)} chapters, state={len(state_nums)} records, in sync",
    }


def _build_preflight_report(explicit_project_root: Optional[str]) -> dict:
    scripts_dir = _scripts_dir().resolve()
    plugin_root = scripts_dir.parent
    skill_root = plugin_root / "skills" / "webnovel-write"
    entry_script = scripts_dir / "webnovel.py"
    extract_script = scripts_dir / "extract_chapter_context.py"

    checks: list[dict[str, object]] = [
        {"name": "scripts_dir", "ok": scripts_dir.is_dir(), "path": str(scripts_dir)},
        {"name": "entry_script", "ok": entry_script.is_file(), "path": str(entry_script)},
        {"name": "extract_context_script", "ok": extract_script.is_file(), "path": str(extract_script)},
        {"name": "skill_root", "ok": skill_root.is_dir(), "path": str(skill_root)},
    ]

    # 可选依赖检查（不阻断 preflight）
    try:
        import aiohttp  # noqa: F401
        checks.append({"name": "aiohttp", "ok": True, "path": ""})
    except ImportError:
        checks.append({
            "name": "aiohttp",
            "ok": True,
            "path": "",
            "warning": "aiohttp 未安装，vector 投影将跳过。pip install aiohttp",
        })

    project_root = ""
    project_root_error = ""
    story_runtime: dict = {}
    try:
        resolved_root = _resolve_root(explicit_project_root)
        project_root = str(resolved_root)
        checks.append({"name": "project_root", "ok": True, "path": project_root})
        story_runtime = build_story_runtime_health(resolved_root)
        fs_state_check = _build_fs_state_sync(resolved_root)
        checks.append(fs_state_check)
    except FileNotFoundError as exc:
        project_root_error = _project_root_diagnostic(explicit_project_root, exc)
        if explicit_project_root:
            # 用户显式指定了 --project-root，但找不到 → 硬错误
            checks.append(
                {
                    "name": "project_root",
                    "ok": False,
                    "path": explicit_project_root,
                    "error": project_root_error,
                }
            )
        else:
            # 未指定 --project-root 且自动探测不到 → 安装环境正常，只需后续 init
            checks.append(
                {
                    "name": "project_root",
                    "ok": True,
                    "path": "",
                    "warning": "尚未初始化书项目，请运行 webnovel init 创建项目",
                }
            )
    except Exception as exc:
        project_root_error = str(exc)
        checks.append({"name": "project_root", "ok": False, "path": explicit_project_root or "", "error": project_root_error})

    return {
        "ok": all(bool(item["ok"]) for item in checks),
        "project_root": project_root,
        "scripts_dir": str(scripts_dir),
        "skill_root": str(skill_root),
        "checks": checks,
        "project_root_error": project_root_error,
        "story_runtime": story_runtime,
    }


def cmd_preflight(args: argparse.Namespace) -> int:
    report = _build_preflight_report(args.project_root)
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for item in report["checks"]:
            if item.get("warning"):
                status = "WARN"
            elif item["ok"]:
                status = "OK"
            else:
                status = "ERROR"
            path = item.get("path") or ""
            print(f"{status} {item['name']}: {path}")
            if item.get("warning"):
                print(f"  warn: {item['warning']}")
            if item.get("error"):
                print(f"  detail: {item['error']}")
        story_runtime = report.get("story_runtime") or {}
        if story_runtime:
            display = story_runtime.get("display_text") or (
                f"chapter={story_runtime.get('chapter')} "
                f"mainline_ready={story_runtime.get('mainline_ready')} "
                f"latest_commit_status={story_runtime.get('latest_commit_status')}"
            )
            print(f"INFO story_runtime: {display}")
    return 0 if report["ok"] else 1


def cmd_use(args: argparse.Namespace) -> int:
    project_root = normalize_windows_path(args.project_root).expanduser()
    try:
        project_root = project_root.resolve()
    except Exception as exc:
        import sys
        print(f"⚠️ path.resolve() 失败 ({project_root}): {exc}", file=sys.stderr)
        project_root = project_root

    workspace_root: Optional[Path] = None
    if args.workspace_root:
        workspace_root = normalize_windows_path(args.workspace_root).expanduser()
        try:
            workspace_root = workspace_root.resolve()
        except Exception as exc:
            import sys
            print(f"⚠️ path.resolve() 失败 ({workspace_root}): {exc}", file=sys.stderr)
            workspace_root = workspace_root

    # 1) 写入工作区指针（若工作区内存在 `.opencode/` 或 `.claude/`）
    pointer_file = write_current_project_pointer(project_root, workspace_root=workspace_root)
    if pointer_file is not None:
        print(f"workspace pointer: {pointer_file}")
    else:
        print("workspace pointer: (skipped)")

    # 2) 写入用户级 registry（保证全局安装/空上下文可恢复）
    reg_path = update_global_registry_current_project(workspace_root=workspace_root, project_root=project_root)
    if reg_path is not None:
        print(f"global registry: {reg_path}")
    else:
        print("global registry: (skipped)")

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="webnovel unified CLI")
    parser.add_argument("--project-root", help="书项目根目录或工作区根目录（可选，默认自动检测）")
    parser.add_argument("--mode", choices=["default", "fast", "minimal"], default="default",
                       help="写作模式（暂未调度，预留参数）")

    sub = parser.add_subparsers(dest="tool", required=True)

    p_where = sub.add_parser("where", help="打印解析出的 project_root")
    p_where.set_defaults(func=cmd_where)

    p_chapter_path = sub.add_parser("chapter-path", help="查找章节文件相对路径（正文目录）")
    p_chapter_path.add_argument("--chapter", type=int, required=True)
    p_chapter_path.set_defaults(func=cmd_chapter_path)

    p_preflight = sub.add_parser("preflight", help="校验统一 CLI 运行环境与 project_root")
    p_preflight.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")
    p_preflight.set_defaults(func=cmd_preflight)

    p_use = sub.add_parser("use", help="绑定当前工作区使用的书项目（写入指针/registry）")
    p_use.add_argument("project_root", help="书项目根目录（必须包含 .webnovel/state.json）")
    p_use.add_argument("--workspace-root", help="工作区根目录（可选；默认由运行环境推断）")
    p_use.set_defaults(func=cmd_use)

    # Pass-through to data modules
    p_index = sub.add_parser("index", help="转发到 index_manager")
    p_index.add_argument("args", nargs=argparse.REMAINDER)

    p_state = sub.add_parser("state", help="转发到 state_manager")
    p_state.add_argument("args", nargs=argparse.REMAINDER)

    p_rag = sub.add_parser("rag", help="转发到 rag_adapter")
    p_rag.add_argument("args", nargs=argparse.REMAINDER)

    p_style = sub.add_parser("style", help="转发到 style_sampler")
    p_style.add_argument("args", nargs=argparse.REMAINDER)

    p_entity = sub.add_parser("entity", help="转发到 entity_linker")
    p_entity.add_argument("args", nargs=argparse.REMAINDER)

    p_context = sub.add_parser("context", help="转发到 context_manager")
    p_context.add_argument("args", nargs=argparse.REMAINDER)

    p_memory = sub.add_parser("memory", help="转发到 memory.store")
    p_memory.add_argument("args", nargs=argparse.REMAINDER)

    p_migrate = sub.add_parser("migrate", help="转发到 migrate_state_to_sqlite")
    p_migrate.add_argument("args", nargs=argparse.REMAINDER)

    # Pass-through to scripts
    p_status = sub.add_parser("status", help="转发到 status_reporter.py")
    p_status.add_argument("args", nargs=argparse.REMAINDER)

    p_doctor = sub.add_parser("doctor", help="项目健康诊断")
    p_doctor.add_argument("--format", choices=["json", "text"], default="text")
    p_doctor.add_argument("--deep", action="store_true", help="深度检查")

    p_update_state = sub.add_parser("update-state", help="转发到 update_state.py")
    p_update_state.add_argument("args", nargs=argparse.REMAINDER)

    p_backup = sub.add_parser("backup", help="转发到 backup_manager.py")
    p_backup.add_argument("args", nargs=argparse.REMAINDER)

    p_archive = sub.add_parser("archive", help="转发到 archive_manager.py")
    p_archive.add_argument("args", nargs=argparse.REMAINDER)

    p_init = sub.add_parser("init", help="转发到 init_project.py（初始化项目）")
    p_init.add_argument("args", nargs=argparse.REMAINDER)

    p_extract_context = sub.add_parser("extract-context", help="转发到 extract_chapter_context.py")
    p_extract_context.add_argument("--chapter", type=int, required=True, help="目标章节号")
    p_extract_context.add_argument("--format", choices=["text", "json"], default="text", help="输出格式")

    p_story_system = sub.add_parser("story-system", help="转发到 story_system.py")
    p_story_system.add_argument("args", nargs=argparse.REMAINDER)

    p_story_events = sub.add_parser("story-events", help="转发到 story_events.py")
    p_story_events.add_argument("--chapter", type=int, default=0, help="目标章节号")
    p_story_events.add_argument("--limit", type=int, default=200, help="查询条数")
    p_story_events.add_argument("--health", action="store_true", help="输出事件链健康信息")

    p_commit = sub.add_parser("chapter-commit", help="转发到 chapter_commit.py")
    p_commit.add_argument("--chapter", type=int, required=True, help="目标章节号")
    p_commit.add_argument("--review-result", default="", help="review_result JSON 文件")
    p_commit.add_argument("--fulfillment-result", default="", help="fulfillment_result JSON 文件")
    p_commit.add_argument("--disambiguation-result", default="", help="disambiguation_result JSON 文件")
    p_commit.add_argument("--extraction-result", default="", help="extraction_result JSON 文件")

    p_memory_contract = sub.add_parser("memory-contract", help="转发到 memory_cli.py")
    p_memory_contract.add_argument("args", nargs=argparse.REMAINDER)

    p_project_memory = sub.add_parser("project-memory", help="转发到 project_memory.py")
    p_project_memory.add_argument("args", nargs=argparse.REMAINDER)

    p_review_pipeline = sub.add_parser("review-pipeline", help="转发到 review_pipeline.py")
    p_review_pipeline.add_argument("--chapter", type=int, required=True, help="目标章节号")
    p_review_pipeline.add_argument("--review-results", required=True, help="reviewer 原始结果 JSON 文件")
    p_review_pipeline.add_argument("--metrics-out", default="", help="metrics 输出文件")
    p_review_pipeline.add_argument("--report-file", default="", help="审查报告路径")
    p_review_pipeline.add_argument("--save-metrics", action="store_true", help="直接写入 index.db")

    p_placeholder_scan = sub.add_parser("placeholder-scan", help="扫描大纲/设定集未补齐占位")
    p_placeholder_scan.add_argument("--format", choices=["json", "text"], default="json", help="输出格式")

    p_master_outline_sync = sub.add_parser("master-outline-sync", help="当前卷规划完成后写回 V+1 最小总纲锚点")
    p_master_outline_sync.add_argument("--volume", type=int, required=True, help="当前已完成规划的卷号")
    p_master_outline_sync.add_argument("--writeback-file", default="", help="显式结构化写回 JSON")
    p_master_outline_sync.add_argument("--format", choices=["json", "text"], default="json", help="输出格式")

    p_export = sub.add_parser("export", help="导出正文为 Markdown/TXT/EPUB")
    p_export.add_argument("args", nargs=argparse.REMAINDER)

    p_publish = sub.add_parser("publish", help="发布章节到小说平台")
    p_publish.add_argument("args", nargs=argparse.REMAINDER)

    knowledge_parser = sub.add_parser("knowledge", help="时序知识查询")
    knowledge_sub = knowledge_parser.add_subparsers(dest="knowledge_action")

    qs_parser = knowledge_sub.add_parser("query-entity-state", help="查询实体在指定章节的状态")
    qs_parser.add_argument("--entity", required=True, help="实体 ID")
    qs_parser.add_argument("--at-chapter", type=int, required=True, help="目标章节号")

    qr_parser = knowledge_sub.add_parser("query-relationships", help="查询实体在指定章节的关系")
    qr_parser.add_argument("--entity", required=True, help="实体 ID")
    qr_parser.add_argument("--at-chapter", type=int, required=True, help="目标章节号")

    # structural checker (写前自检)
    checkers_parser = sub.add_parser("checkers", help="结构自检（写前阻断）")
    checkers_sub = checkers_parser.add_subparsers(dest="checkers_action")

    p_structural = checkers_sub.add_parser("structural", help="运行五项结构检查")
    p_structural.add_argument("--chapter", type=int, required=True, help="目标章节号")
    p_structural.add_argument("--format", choices=["json", "text"], default="json", help="输出格式")

    # orchestrate — batch chapter pipeline
    p_orchestrate = sub.add_parser("orchestrate", help="批量编排（write/heal/nightly）")
    p_orchestrate.add_argument("mode", choices=["write", "heal", "nightly"], help="write=全流程, heal=修复, nightly=健康检查")
    p_orchestrate.add_argument("chapters", help="章节范围，如 '5-12' 或 '5,7,9-12'")

    # delete-chapters — safe chapter deletion with projection cleanup
    p_delete = sub.add_parser("delete-chapters", help="删除章节并清理投影")
    p_delete.add_argument("chapters", help="章节范围，如 '5-12' 或 '5,7,9-12'")
    p_delete.add_argument("--dry-run", action="store_true", help="预览不执行")

    # entity-clean — dirty entity detection
    p_entity_clean = sub.add_parser("entity-clean", help="扫描并标记 index.db 中的脏实体（拼音/英文ID）")
    p_entity_clean.add_argument("--mark-invalid", action="store_true", help="写入 invalid_facts 表")

    # ssot — single source of truth enforcer
    p_ssot = sub.add_parser("ssot", help="SSOT 真理源管理（事件日志/投影重建/一致性校验）")
    p_ssot_sub = p_ssot.add_subparsers(dest="ssot_action")
    p_ssot_sub.add_parser("verify", help="校验 state.json 与事件日志一致性")
    p_ssot_sub.add_parser("rebuild", help="从事件日志重建 state.json")
    ssot_events = p_ssot_sub.add_parser("events", help="读取事件日志")
    ssot_events.add_argument("--event-type", help="按事件类型过滤")
    ssot_events.add_argument("--chapter", type=int, help="按章节过滤")

    # workflow — checkpoint engine
    p_wf = sub.add_parser("workflow", help="章节工作流检查点（阶段追踪/中断恢复）")
    p_wf_sub = p_wf.add_subparsers(dest="workflow_action")
    wf_check = p_wf_sub.add_parser("checkpoint", help="记录阶段转换")
    wf_check.add_argument("--chapter", type=int, required=True)
    wf_check.add_argument("--stage", choices=["PLANNING", "DRAFTING", "REVIEWING", "REVISING", "COMMITTED"], required=True)
    wf_check.add_argument("--metadata", help="JSON 元数据")
    wf_status = p_wf_sub.add_parser("status", help="查看章节进度")
    wf_status.add_argument("--chapter", type=int, help="指定章节，不填则全部")
    p_wf_sub.add_parser("interrupted", help="查找中断未完成的章节")

    # override — versioned constraint management
    p_ovr = sub.add_parser("override", help="Override Contract 版本化规则管理")
    p_ovr_sub = p_ovr.add_subparsers(dest="override_action")
    ovr_add = p_ovr_sub.add_parser("add", help="新增规则覆盖（自动升版本）")
    ovr_add.add_argument("--constraint-id", required=True, help="如 power.flight_limit")
    ovr_add.add_argument("--old-rule", required=True)
    ovr_add.add_argument("--new-rule", required=True)
    ovr_add.add_argument("--rationale", required=True)
    ovr_add.add_argument("--chapter", type=int, required=True)
    ovr_add.add_argument("--domain", default="world_rule")
    ovr_list = p_ovr_sub.add_parser("list", help="列出当前生效规则")
    ovr_list.add_argument("--domain")
    ovr_hist = p_ovr_sub.add_parser("history", help="查看规则版本历史")
    ovr_hist.add_argument("--constraint-id", required=True)
    ovr_ctx = p_ovr_sub.add_parser("context", help="生成上下文提示")
    ovr_ctx.add_argument("--chapter", type=int, required=True)

    p_dsh_sync = sub.add_parser(
        "dsh-sync",
        help="镜像 .opencode 资产为 DeepSeek Harness 适配层（.dsh/skills + .dsh/agents）",
    )
    p_dsh_sync.add_argument("--check", action="store_true", help="只校验不写盘（幂等性检查）")

    # 兼容：允许 `--project-root` 出现在任意位置（减少 agents/skills 拼命令的出错率）
    from .cli_args import normalize_global_project_root

    argv = normalize_global_project_root(sys.argv[1:])
    args, unknown_args = parser.parse_known_args(argv)

    # where/use 直接执行
    if hasattr(args, "func"):
        if unknown_args:
            parser.error(f"unrecognized arguments: {' '.join(unknown_args)}")
        code = int(args.func(args) or 0)
        raise SystemExit(code)

    tool = args.tool
    if unknown_args and tool not in PASSTHROUGH_TOOLS:
        parser.error(f"unrecognized arguments: {' '.join(unknown_args)}")

    rest = _passthrough_tail(argv, tool) if tool in PASSTHROUGH_TOOLS else list(getattr(args, "args", []) or [])
    # argparse.REMAINDER 可能以 `--` 开头占位，这里去掉
    if rest[:1] == ["--"]:
        rest = rest[1:]
    rest = _strip_project_root_args(rest)

    # init 是创建项目，不应该依赖/注入已存在 project_root
    if tool == "init":
        raise SystemExit(_run_script("init_project.py", rest))

    # dsh-sync 作用于仓库根（.opencode → .dsh），不需要书项目 root
    if tool == "dsh-sync":
        from .dsh_sync import main as _dsh_sync_main
        saved_argv = sys.argv
        sys.argv = ["dsh-sync"] + (["--check"] if getattr(args, "check", False) else [])
        try:
            rc = _dsh_sync_main()
        finally:
            sys.argv = saved_argv
        raise SystemExit(rc)

    # 其余工具：统一解析 project_root 后前置给下游
    project_root = _resolve_root(args.project_root)
    forward_args = ["--project-root", str(project_root)]

    if tool == "index":
        raise SystemExit(_run_data_module("index_manager", [*forward_args, *rest]))
    if tool == "state":
        if rest and rest[0] == "render":
            from .state_projection_renderer import render_all_projections
            results = render_all_projections(project_root)
            for name, path in results.items():
                print(f"  {name} -> {path}")
            print(f"Rendered {len(results)} projection files.")
            raise SystemExit(0)
        raise SystemExit(_run_data_module("state_manager", [*forward_args, *rest]))
    if tool == "rag":
        raise SystemExit(_run_data_module("rag_adapter", [*forward_args, *rest]))
    if tool == "style":
        raise SystemExit(_run_data_module("style_sampler", [*forward_args, *rest]))
    if tool == "entity":
        raise SystemExit(_run_data_module("entity_linker", [*forward_args, *rest]))
    if tool == "context":
        raise SystemExit(_run_data_module("context_manager", [*forward_args, *rest]))
    if tool == "memory":
        raise SystemExit(_run_data_module("memory.store", [*forward_args, *rest]))
    if tool == "migrate":
        raise SystemExit(_run_data_module("migrate_state_to_sqlite", [*forward_args, *rest]))

    if tool == "status":
        raise SystemExit(_run_script("status_reporter.py", [*forward_args, *rest]))
    if tool == "doctor":
        raise SystemExit(_run_data_module("doctor", forward_args))
    if tool == "update-state":
        raise SystemExit(_run_script("update_state.py", [*forward_args, *rest]))
    if tool == "backup":
        raise SystemExit(_run_script("backup_manager.py", [*forward_args, *rest]))
    if tool == "archive":
        raise SystemExit(_run_script("archive_manager.py", [*forward_args, *rest]))
    if tool == "extract-context":
        return_args = [*forward_args, "--chapter", str(args.chapter), "--format", str(args.format)]
        raise SystemExit(_run_script("extract_chapter_context.py", return_args))
    if tool == "story-system":
        raise SystemExit(_run_script("story_system.py", [*forward_args, *rest]))
    if tool == "story-events":
        return_args = [*forward_args, "--limit", str(args.limit)]
        if args.chapter:
            return_args.extend(["--chapter", str(args.chapter)])
        if args.health:
            return_args.append("--health")
        raise SystemExit(_run_script("story_events.py", return_args))
    if tool == "chapter-commit":
        return_args = [*forward_args, "--chapter", str(args.chapter)]
        if args.review_result:
            return_args.extend(["--review-result", str(args.review_result)])
        if args.fulfillment_result:
            return_args.extend(["--fulfillment-result", str(args.fulfillment_result)])
        if args.disambiguation_result:
            return_args.extend(["--disambiguation-result", str(args.disambiguation_result)])
        if args.extraction_result:
            return_args.extend(["--extraction-result", str(args.extraction_result)])
        raise SystemExit(_run_script("chapter_commit.py", return_args))
    if tool == "memory-contract":
        raise SystemExit(_run_script("memory_cli.py", [*forward_args, *rest]))
    if tool == "project-memory":
        raise SystemExit(_run_script("project_memory.py", [*forward_args, *rest]))
    if tool == "review-pipeline":
        return_args = [
            *forward_args,
            "--chapter", str(args.chapter),
            "--review-results", str(args.review_results),
        ]
        if args.metrics_out:
            return_args.extend(["--metrics-out", str(args.metrics_out)])
        if args.report_file:
            return_args.extend(["--report-file", str(args.report_file)])
        if args.save_metrics:
            return_args.append("--save-metrics")
        raise SystemExit(_run_script("review_pipeline.py", return_args))
    if tool == "placeholder-scan":
        raise SystemExit(_run_data_module("placeholder_scanner", [*forward_args, "--format", str(args.format)]))
    if tool == "master-outline-sync":
        return_args = [*forward_args, "--volume", str(args.volume), "--format", str(args.format)]
        if args.writeback_file:
            return_args.extend(["--writeback-file", str(args.writeback_file)])
        raise SystemExit(_run_script("update_master_outline.py", return_args))

    if tool == "export":
        raise SystemExit(_run_script("export_manager/__init__.py", [*forward_args, *rest]))

    if tool == "publish":
        # 拦截 --help，转发到 publisher 的 argparse
        if rest and set(rest) & {"-h", "--help"}:
            raise SystemExit(_run_script("publisher/__init__.py", ["--help"]))
        raise SystemExit(_run_script("publisher/__init__.py", [*forward_args, *rest]))

    if tool == "knowledge":
        from .knowledge_query import KnowledgeQuery
        from .cli_output import print_success
        kq = KnowledgeQuery(project_root)
        if args.knowledge_action == "query-entity-state":
            result = kq.entity_state_at_chapter(args.entity, args.at_chapter)
            print_success(result, message="entity_state_at_chapter")
            raise SystemExit(0)
        elif args.knowledge_action == "query-relationships":
            result = kq.entity_relationships_at_chapter(args.entity, args.at_chapter)
            print_success(result, message="entity_relationships_at_chapter")
            raise SystemExit(0)

    if tool == "checkers":
        if args.checkers_action == "structural":
            return_args = [*forward_args, "--chapter", str(args.chapter)]
            if args.format:
                return_args.extend(["--format", args.format])
            raise SystemExit(_run_data_module("structural_checker", return_args))

    if tool == "orchestrate":
        return_args = [*forward_args, args.mode, args.chapters]
        raise SystemExit(_run_data_module("orchestrate", return_args))

    if tool == "delete-chapters":
        return_args = [*forward_args, args.chapters]
        if getattr(args, "dry_run", False):
            return_args.append("--dry-run")
        raise SystemExit(_run_data_module("chapter_delete_service", return_args))

    if tool == "entity-clean":
        return_args = [*forward_args]
        if getattr(args, "mark_invalid", False):
            return_args.append("--mark-invalid")
        raise SystemExit(_run_data_module("entity_cleanup", return_args))

    if tool == "ssot":
        return_args = [*forward_args, args.ssot_action]
        if hasattr(args, "event_type") and args.event_type:
            return_args.extend(["--event-type", args.event_type])
        if hasattr(args, "chapter") and args.chapter:
            return_args.extend(["--chapter", str(args.chapter)])
        raise SystemExit(_run_data_module("ssot_enforcer", return_args))

    if tool == "workflow":
        return_args = [*forward_args, args.workflow_action]
        if hasattr(args, "chapter") and args.chapter is not None:
            return_args.extend(["--chapter", str(args.chapter)])
        if hasattr(args, "stage") and args.stage:
            return_args.extend(["--stage", args.stage])
        if hasattr(args, "metadata") and args.metadata:
            return_args.extend(["--metadata", args.metadata])
        raise SystemExit(_run_data_module("workflow_checkpoint", return_args))

    if tool == "override":
        return_args = [*forward_args, args.override_action]
        if hasattr(args, "constraint_id") and args.constraint_id:
            return_args.extend(["--constraint-id", args.constraint_id])
        for cli_flag, attr in [
            ("--old-rule", "old_rule"),
            ("--new-rule", "new_rule"),
            ("--rationale", "rationale"),
            ("--domain", "domain"),
        ]:
            val = getattr(args, attr, None)
            if val:
                return_args.extend([cli_flag, str(val)])
        if hasattr(args, "chapter") and args.chapter:
            return_args.extend(["--chapter", str(args.chapter)])
        raise SystemExit(_run_data_module("override_contract_engine", return_args))

    raise SystemExit(2)


if __name__ == "__main__":
    main()
