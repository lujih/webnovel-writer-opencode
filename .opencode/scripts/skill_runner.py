#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Skill runner - CJK-safe intermediate layer.

All CJK text flows through stdin or files, never CLI args.
Forces UTF-8 stdio on startup via runtime_compat.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3

import sys
from pathlib import Path

_scripts_root = Path(__file__).resolve().parent
if str(_scripts_root) not in sys.path:
    sys.path.insert(0, str(_scripts_root))

from runtime_compat import enable_windows_utf8_stdio


INFRA_CHECKS = {"contract_coverage"}


def filter_structural_checks(result: dict) -> dict:
    for c in result["checks"]:
        if c["name"] in INFRA_CHECKS:
            c["severity"] = "warning"
            c["passed"] = True
            if not c.get("fix"):
                c["fix"] = "infrastructure issue, does not block writing"
    result["passed"] = not any(
        c["severity"] == "blocking" and not c["passed"]
        for c in result["checks"]
    )
    return result


def cmd_story_system(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    goal = sys.stdin.read().strip()
    if not goal:
        print("ERROR: stdin missing CHAPTER_GOAL", file=sys.stderr)
        return 1
    if re.search(r'\{章纲[^}]*\}|\{chapter[^}]*\}|第N章', goal, re.IGNORECASE):
        print(f"ERROR: CHAPTER_GOAL 疑似占位符未替换: {goal[:80]}", file=sys.stderr)
        return 1

    # P2 修复：直读 state.json 改走 read_json_safe（并发 os.replace 中途容错，
    # 失败降级 {} 而不是抛 JSONDecodeError 崩掉整个 skill step）
    state_path = root / ".webnovel" / "state.json"
    if not state_path.is_file():
        print("ERROR: state.json 不存在", file=sys.stderr)
        return 1
    try:
        from security_utils import read_json_safe
    except ImportError:  # pragma: no cover
        from scripts.security_utils import read_json_safe
    s = read_json_safe(state_path, default={})
    genre = s.get("project_info", {}).get("genre", "")

    old_argv = sys.argv
    try:
        sys.argv = [
            "story_system.py",
            "--project-root", str(root),
            goal,
            "--genre", genre,
            "--chapter", str(args.chapter),
            "--persist", "--emit-runtime-contracts", "--format", "both",
        ]
        from story_system import main as story_main
        story_main()
        return 0
    except SystemExit as e:
        return int(e.code or 0)
    finally:
        sys.argv = old_argv


def cmd_check_structural(args: argparse.Namespace) -> int:
    from data_modules.structural_checker import run_checks

    root = Path(args.project_root)
    intended = (args.intended_strand or "").strip().lower()
    result = run_checks(root, args.chapter, intended_strand=intended)
    result = filter_structural_checks(result)

    if getattr(args, 'output', None):
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if result["passed"] else "BLOCKED"
        print(f"Chapter {result['chapter']} structural: {status}")
        for c in result["checks"]:
            icon = "OK" if c["passed"] else "FAIL"
            print(f"  {icon} [{c['severity']}] {c['name']}")
            if not c["passed"]:
                print(f"         {c['detail']}")
                print(f"      -> {c['fix']}")

    return 0 if result["passed"] else 1


def cmd_check_file(path: str) -> int:
    p = Path(path)
    if p.is_file() and p.stat().st_size > 0:
        print("OK")
        return 0
    print("MISSING")
    return 1


def cmd_check_commit(project_root: str, chapter: int) -> int:
    p = Path(project_root) / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json"
    return cmd_check_file(str(p))


def cmd_check_index(project_root: str, chapter: int) -> int:
    db_path = Path(project_root) / ".webnovel" / "index.db"
    if not db_path.is_file():
        print("MISSING")
        return 1
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT COUNT(*) FROM chapters WHERE chapter=?", (chapter,)
        ).fetchone()
        if row and row[0] > 0:
            print("OK")
            return 0
        print("MISSING")
        return 1
    finally:
        conn.close()


def cmd_verify_chapter_files(args: argparse.Namespace) -> int:
    root = Path(args.project_root)
    ch = int(args.chapter)
    errors = []

    text_dir = root / "正文"
    # P1 修复：旧实现 rglob(f"第*{ch}*章*.md") 的通配 `*` 会把 第11章 误匹配到 ch=1
    # （子串 "1" 命中 "11"）、把 第021章 误匹配到 ch=2，多章数书籍下 false-positive。
    # 改为：从文件名提取所有 "第N章" / "chapterN" / "chapter_N" 编号段，
    # 与 ch 做整数相等比较——兼容 0 填充（第001章 == ch 1），排除 第11章 != ch 1。
    import re as _re
    chapter_files: list[Path] = []
    if text_dir.is_dir():
        for p in text_dir.rglob("*.md"):
            for m in _re.finditer(r"第\s*(\d+)\s*章|chapter[_](\d+)", p.name, re.IGNORECASE):
                if int(m.group(1) or m.group(2)) == ch:
                    chapter_files.append(p)
                    break
    if not chapter_files:
        errors.append(f"章节文件缺失: 第{ch}章")
    elif not chapter_files[0].stat().st_size:
        errors.append(f"章节文件为空: 第{ch}章")

    commit_file = root / ".story-system" / "commits" / f"chapter_{ch:03d}.commit.json"
    if not commit_file.is_file():
        errors.append(f"commit缺失: chapter_{ch:03d}.commit.json")
    else:
        commit = json.loads(commit_file.read_text("utf-8"))
        proj = commit.get("projection_status", {})
        for name in ("state", "index", "summary", "memory", "vector"):
            status = proj.get(name, "missing")
            if status not in ("done", "skipped"):
                errors.append(f"projection {name}={status}")

    if errors:
        print("FAIL: " + "; ".join(errors))
        return 1
    print("OK")
    return 0


def cmd_pause_batch(args: argparse.Namespace) -> int:
    state_path = Path(args.project_root) / ".webnovel" / "batch_state.json"
    if not state_path.is_file():
        print("NO_BATCH")
        return 0
    s = json.loads(state_path.read_text("utf-8-sig"))
    prev = s.get("status", "")
    if prev == "running":
        s["status"] = "paused"
        state_path.write_text(json.dumps(s, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"PAUSED at chapter={s.get('current_chapter', '?')}")
    elif prev == "paused":
        print("ALREADY PAUSED")
    else:
        print(f"status={prev}, no action taken")
    return 0


def cmd_mark_step_done(args: argparse.Namespace) -> int:
    """创建步骤完成标记，支持断点续跑。"""
    root = Path(args.project_root)
    marker_dir = root / ".webnovel" / "tmp" / "step_done"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / f"ch{args.chapter:04d}_{args.step}.done"
    marker.write_text("1", encoding="utf-8")
    print(f"[OK] 步骤标记: {marker.name}")
    return 0


def cmd_clean_tmp(args: argparse.Namespace) -> int:
    """清理 .webnovel/tmp/ 下的旧 artifacts（替代 rm -f，跨 shell 兼容）。

    --keep 指定保留的文件名（可多次使用），如 --keep review_results.json
    """
    tmp_dir = Path(args.project_root) / ".webnovel" / "tmp"
    if not tmp_dir.is_dir():
        return 0
    keep = set(args.keep)
    cleaned = 0
    for f in tmp_dir.iterdir():
        if f.is_file() and f.suffix == ".json":
            if f.name in keep:
                continue
            f.unlink()
            cleaned += 1
    print(f"CLEANED {cleaned} tmp files")
    return 0


def cmd_normalize_contracts(args: argparse.Namespace) -> int:
    """统一 .story-system/chapters/ 下合同文件名为 {:03d} 格式。"""
    chapters_dir = Path(args.project_root) / ".story-system" / "chapters"
    if not chapters_dir.is_dir():
        print("NO_CHAPTERS_DIR")
        return 0
    import re
    pat = re.compile(r"chapter_0*(\d+)\.json$")
    renamed = 0
    for f in sorted(chapters_dir.iterdir()):
        m = pat.match(f.name)
        if not m:
            continue
        num = int(m.group(1))
        target = chapters_dir / f"chapter_{num:03d}.json"
        if f != target:
            f.rename(target)
            renamed += 1
            print(f"  {f.name} -> {target.name}")
    print(f"RENAMED {renamed} files" if renamed else "OK (all normalized)")
    return 0


def cmd_compact_memory(args: argparse.Namespace) -> int:
    from data_modules.memory.store import ScratchpadManager
    from data_modules.memory.compactor import collect_garbage
    from data_modules.config import get_config

    config = get_config()
    config.project_root = args.project_root
    store = ScratchpadManager(config)
    data = store.load()
    before = data.count_items()
    data = collect_garbage(data)
    after = data.count_items()
    store.save(data)
    removed = before - after
    print(f"OK: removed {removed} outdated items ({before} -> {after})")
    return 0


def cmd_check_batch_integrity(project_root: str, start: int, end: int) -> int:
    state_path = Path(project_root) / ".webnovel" / "batch_state.json"
    if not state_path.is_file():
        print("MISSING: batch_state.json not found")
        return 1
    s = json.loads(state_path.read_text("utf-8-sig"))
    completed = set(s.get("completed_chapters", []))
    expected = set(range(start, end + 1))
    missing = sorted(expected - completed)
    if missing:
        print(f"MISSING: {missing}")
        return 1
    print("OK")
    return 0


def main() -> None:
    enable_windows_utf8_stdio(skip_in_pytest=True)

    parser = argparse.ArgumentParser(description="skill_runner")
    sub = parser.add_subparsers(dest="action", required=True)

    p_ss = sub.add_parser("story-system")
    p_ss.add_argument("--project-root", required=True)
    p_ss.add_argument("--chapter", type=int, required=True)
    p_ss.set_defaults(func=cmd_story_system)

    p_cs = sub.add_parser("check-structural")
    p_cs.add_argument("--project-root", required=True)
    p_cs.add_argument("--chapter", type=int, required=True)
    p_cs.add_argument("--intended-strand", choices=["quest", "fire", "constellation"], default=None)
    p_cs.add_argument("--output", default=None, help="直接写入文件（避免 shell 重定向编码问题）")
    p_cs.add_argument("--format", choices=["json", "text"], default="json")
    p_cs.set_defaults(func=cmd_check_structural)

    p_cc = sub.add_parser("check-commit")
    p_cc.add_argument("--project-root", required=True)
    p_cc.add_argument("--chapter", type=int, required=True)
    p_cc.set_defaults(func=lambda args: cmd_check_commit(args.project_root, args.chapter))

    p_ci = sub.add_parser("check-index")
    p_ci.add_argument("--project-root", required=True)
    p_ci.add_argument("--chapter", type=int, required=True)
    p_ci.set_defaults(func=lambda args: cmd_check_index(args.project_root, args.chapter))

    p_cf = sub.add_parser("check-file")
    p_cf.add_argument("--path", required=True)
    p_cf.set_defaults(func=lambda args: cmd_check_file(args.path))

    p_cbi = sub.add_parser("check-batch-integrity")
    p_cbi.add_argument("--project-root", required=True)
    p_cbi.add_argument("--start", type=int, required=True)
    p_cbi.add_argument("--end", type=int, required=True)
    p_cbi.set_defaults(func=lambda args: cmd_check_batch_integrity(args.project_root, args.start, args.end))

    p_vcf = sub.add_parser("verify-chapter-files")
    p_vcf.add_argument("--project-root", required=True)
    p_vcf.add_argument("--chapter", type=int, required=True)
    p_vcf.set_defaults(func=cmd_verify_chapter_files)

    p_pb = sub.add_parser("pause-batch")
    p_pb.add_argument("--project-root", required=True)
    p_pb.set_defaults(func=cmd_pause_batch)

    p_ct = sub.add_parser("clean-tmp")
    p_ct.add_argument("--project-root", required=True)
    p_ct.add_argument("--keep", nargs="*", default=[], help="保留的文件名（可多选），如 --keep review_results.json")
    p_ct.set_defaults(func=cmd_clean_tmp)

    p_nc = sub.add_parser("normalize-contracts")
    p_nc.add_argument("--project-root", required=True)
    p_nc.set_defaults(func=cmd_normalize_contracts)

    p_cm = sub.add_parser("compact-memory")
    p_cm.add_argument("--project-root", required=True)
    p_cm.set_defaults(func=cmd_compact_memory)

    p_mark = sub.add_parser("mark-step-done")
    p_mark.add_argument("--project-root", required=True)
    p_mark.add_argument("--step", required=True, help="步骤名 (story-system, check-structural, review, commit)")
    p_mark.add_argument("--chapter", type=int, required=True)
    p_mark.set_defaults(func=cmd_mark_step_done)

    args = parser.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
