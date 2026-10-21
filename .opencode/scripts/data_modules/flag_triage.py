#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""flag-triage：仲裁 review_flags（门禁分层后 LLM 语义问题的留痕出口）。

门禁分层（2026-10）把 LLM 报的 blocking 级语义问题从「拒章」降级为
`review_flags`（commit JSON 里的观察标记，triage=None 即待仲裁）。本工具
是这些标记的**人工裁决入口**：

    python flag_triage.py --project-root P list   --chapter N [--limit 20]
    python flag_triage.py --project-root P confirm --chapter N --index I
    python flag_triage.py --project-root P dismiss --chapter N --index I

仲裁语义（只记录，不改 status）：
- `confirm`  人工确认「这一条是真的问题」→ 触发 webnovel-heal 的入口
  （flag 标 confirmed，不再进 context 警戒段；heal 由作者显式发起）
- `dismiss`  人工否认「这是 LLM 误判」→ 标 dismissed，不再注入警戒
- **两者都不改 commit 的 status**——门禁分层的核心是「语义问题不拒章」，
  仲裁只是给观察标记一个终态，避免同一 flag 反复出现在 context 里。

幂等：重复 confirm/dismiss 同一 index 只是覆盖 triage 值（记录最新裁决），
不报「已仲裁」错误。triage 写进 commit JSON 的 `review_flags[i].triage`
（"confirmed" / "dismissed" / null）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict


def _emit(result: Dict[str, Any], message: str, rc: int = 0) -> None:
    """统一 JSON 输出 + 可选人类可读 message（与本仓其它 CLI 一致）。"""
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if message:
        print(message, file=sys.stderr)
    raise SystemExit(rc)


def _load_flags(project_root: Path, chapter: int) -> Dict[str, Any]:
    """读本章 commit JSON，返回 (payload, flags, 路径)。"""
    from data_modules.story_contracts import StoryContractPaths

    paths = StoryContractPaths.from_project_root(project_root)
    commit_file = paths.commit_json(chapter)
    if not commit_file.is_file():
        return {"ok": False, "error": f"第 {chapter} 章 commit 不存在：{commit_file}"}
    payload = json.loads(commit_file.read_text(encoding="utf-8"))
    flags = payload.get("review_flags") or []
    return {"ok": True, "file": str(commit_file), "payload": payload, "flags": flags}


def _save_flags(project_root: Path, chapter: int, payload: Dict[str, Any]) -> None:
    from data_modules.story_contracts import StoryContractPaths

    paths = StoryContractPaths.from_project_root(project_root)
    commit_file = paths.commit_json(chapter)
    commit_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")


def cmd_list(args) -> None:
    got = _load_flags(Path(args.project_root), args.chapter)
    if not got["ok"]:
        _emit({"chapter": args.chapter, "flags": [], "pending": 0},
              f"⚠️ {got['error']}", rc=1)
        return
    flags = got["flags"]
    pending = [f for f in flags if isinstance(f, dict) and f.get("triage") is None]
    _emit({
        "chapter": args.chapter,
        "commit_status": (got["payload"].get("meta") or {}).get("status"),
        "total_flags": len(flags),
        "pending": len(pending),
        "flags": [
            {"index": i, "severity": f.get("severity"), "category": f.get("category"),
             "description": f.get("description"), "triage": f.get("triage")}
            for i, f in enumerate(flags) if isinstance(f, dict)
        ][:args.limit],
    }, f"第 {args.chapter} 章：{len(pending)} 条待仲裁 / {len(flags)} 条总数")


def cmd_triage(args) -> None:
    decision = "confirmed" if args.action == "confirm" else "dismissed"
    got = _load_flags(Path(args.project_root), args.chapter)
    if not got["ok"]:
        _emit({"chapter": args.chapter, "index": args.index, "triage": None,
               "ok": False}, f"❌ {got['error']}", rc=1)
        return
    flags = got["flags"]
    if not isinstance(flags, list) or args.index < 0 or args.index >= len(flags):
        _emit({"chapter": args.chapter, "index": args.index, "triage": None,
               "ok": False, "error": f"index {args.index} 越界（共 {len(flags)} 条）"},
              f"❌ 无法定位 flag #{args.index}", rc=1)
        return
    flag = flags[args.index]
    if not isinstance(flag, dict):
        _emit({"chapter": args.chapter, "index": args.index, "triage": None,
               "ok": False}, "❌ flag 条目形状异常", rc=1)
        return
    previous = flag.get("triage")
    flag["triage"] = decision
    got["payload"]["review_flags"] = flags
    _save_flags(Path(args.project_root), args.chapter, got["payload"])
    _emit({
        "chapter": args.chapter,
        "index": args.index,
        "triage": decision,
        "previous_triage": previous,
        "ok": True,
    }, (f"✅ 第 {args.chapter} 章 flag #{args.index} 已 {decision}"
        + (f"（原 {previous}）" if previous else "")))


def main() -> None:
    parser = argparse.ArgumentParser(description="flag-triage（review_flags 人工仲裁）")
    parser.add_argument("--project-root", required=True)
    sub = parser.add_subparsers(dest="action")

    p_list = sub.add_parser("list", help="列出本章 flags 与待仲裁项")
    p_list.add_argument("--chapter", type=int, required=True)
    p_list.add_argument("--limit", type=int, default=20)

    for action in ("confirm", "dismiss"):
        p = sub.add_parser(action, help=f"{action} 一条 flag")
        p.add_argument("--chapter", type=int, required=True)
        p.add_argument("--index", type=int, required=True)

    args = parser.parse_args()
    if args.action == "list":
        cmd_list(args)
    elif args.action in ("confirm", "dismiss"):
        cmd_triage(args)
    else:
        parser.error("需要子命令：list | confirm | dismiss")


if __name__ == "__main__":
    from runtime_compat import enable_windows_utf8_stdio

    enable_windows_utf8_stdio()
    main()
