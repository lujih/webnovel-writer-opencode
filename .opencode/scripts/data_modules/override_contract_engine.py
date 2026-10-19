"""Override Contract Engine — versioned constraint rules with precedence resolution.

Industry reference: Versioned configuration management (Kubernetes ConfigMap,
etcd watch + revision, Git merge-conflict resolution).

Problem: When a world rule changes (e.g., "金丹期修士不能飞行" is broken by the
protagonist), the old rule still appears in RAG context alongside the new reality.
The AI sees contradictory information and may produce inconsistent output.

Solution: Every constraint has a version chain. An override creates a new version
with higher precedence. The context system only shows the current-effective rule
(the highest non-superseded version for each constraint domain).

Key concepts:
  constraint_id   — stable identifier for a rule (e.g., "power.flight_limit")
  version         — monotonically increasing per constraint_id
  status          — active | superseded | deprecated
  precedence      — higher = newer (determined by version + timestamp)
  rationale       — WHY the rule changed (recorded for context assembly)
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .ssot_enforcer import publish_event

_OVERRIDE_FILE = ".story-system/override_contracts.json"


def _override_path(project_root: Path) -> Path:
    return project_root / _OVERRIDE_FILE


def _read_overrides(project_root: Path) -> dict:
    path = _override_path(project_root)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"contracts": {}, "history": []}


def _write_overrides(project_root: Path, data: dict) -> None:
    path = _override_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(path))


def add_override(project_root: Path, constraint_id: str,
                 old_rule: str, new_rule: str,
                 rationale: str, chapter: int,
                 domain: str = "world_rule") -> dict:
    """Create a new version of a constraint, superseding the previous one.

    Returns the created override record.
    """
    store = _read_overrides(project_root)
    contracts = store.setdefault("contracts", {})
    chain = contracts.setdefault(constraint_id, [])

    version = len(chain) + 1

    record = {
        "constraint_id": constraint_id,
        "version": version,
        "domain": domain,
        "old_rule": old_rule,
        "new_rule": new_rule,
        "rationale": rationale,
        "chapter": chapter,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    # ── 顺序说明（别改回去）──────────────────────────────────────
    # 事件必须**先**写成功，override_contracts.json 才能落盘。
    #
    # 旧顺序是「chain 里标 superseded → 写文件 → 发事件」。事件写失败时
    # 文件已经写好：新版本 active、旧版本已被标成 superseded，而事件日志
    # 里没有 override_rule_added。`rebuild_state_json` 消费该事件重放
    # override_rules，于是下次 rebuild 之后这个覆盖**消失**，旧规则回来
    # ——用户看到设定"自己变回去了"。
    #
    # 还有一个次要但同样真实的后果：失败的那次已经把版本号吃掉了，
    # 重试会从 v2 开始（版本链出现空洞）。
    #
    # 事件日志是 SSOT 的事实来源，让它先落盘；失败则 store 完全不改。
    publish_event(project_root, "override_rule_added", record, chapter=chapter)

    if chain:
        chain[-1]["status"] = "superseded"
    chain.append(record)
    store.setdefault("history", []).append({
        "constraint_id": constraint_id,
        "version": version,
        "action": "created",
        "chapter": chapter,
        "timestamp": record["created_at"],
    })

    _write_overrides(project_root, store)

    return record


def get_effective_rules(project_root: Path, domain: Optional[str] = None) -> list[dict]:
    """Return only the current-effective (highest-version, non-superseded) rules.

    This is what the context system should feed to the AI.
    """
    store = _read_overrides(project_root)
    contracts = store.get("contracts", {})

    effective = []
    for constraint_id, chain in contracts.items():
        if not chain:
            continue
        current = chain[-1]  # highest version
        if current.get("status") == "active":
            if domain and current.get("domain") != domain:
                continue
            effective.append(current)

    return sorted(effective, key=lambda r: r.get("chapter", 0), reverse=True)


def get_rule_history(project_root: Path, constraint_id: str) -> list[dict]:
    """Return the full version chain for a constraint."""
    store = _read_overrides(project_root)
    return store.get("contracts", {}).get(constraint_id, [])


def build_context_hints(project_root: Path,
                        target_chapter: int,
                        max_rules: int = 5) -> str:
    """Build a compact context hint listing only active rule changes relevant
    to the target chapter.

    Format suitable for injection into the context-agent's task brief.
    """
    effective = get_effective_rules(project_root)
    if not effective:
        return ""

    relevant = [r for r in effective if r["chapter"] <= target_chapter]
    if not relevant:
        return ""

    lines = ["## 已生效的世界规则变更（Override Contracts）", ""]
    for r in relevant[:max_rules]:
        lines.append(f"- **{r['constraint_id']}** v{r['version']}（第{r['chapter']}章）")
        lines.append(f"  旧规则: {r['old_rule']}")
        lines.append(f"  新规则: {r['new_rule']}")
        lines.append(f"  原因: {r['rationale']}")
        lines.append("")

    if len(relevant) > max_rules:
        lines.append(f"（还有 {len(relevant) - max_rules} 条规则变更未展示）")
        lines.append("")

    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────


def cmd_override(args) -> int:
    project_root = Path(args.project_root).expanduser().resolve()

    if args.action == "add":
        record = add_override(
            project_root,
            constraint_id=args.constraint_id,
            old_rule=args.old_rule,
            new_rule=args.new_rule,
            rationale=args.rationale,
            chapter=args.chapter,
            domain=getattr(args, 'domain', 'world_rule'),
        )
        print(f"Override created: {record['constraint_id']} v{record['version']}")
        return 0

    if args.action == "list":
        effective = get_effective_rules(project_root,
                                        domain=getattr(args, 'domain', None))
        if not effective:
            print("No active overrides.")
        for r in effective:
            print(f"  [{r['domain']}] {r['constraint_id']} v{r['version']}: "
                  f"{r['old_rule']} → {r['new_rule']} (ch{r['chapter']})")
        return 0

    if args.action == "history":
        chain = get_rule_history(project_root, args.constraint_id)
        if not chain:
            print(f"No history for constraint: {args.constraint_id}")
        for v in chain:
            status_flag = " *CURRENT*" if v["status"] == "active" else ""
            print(f"  v{v['version']}: {v['old_rule']} → {v['new_rule']} "
                  f"(ch{v['chapter']}){status_flag}")
        return 0

    if args.action == "context":
        hints = build_context_hints(project_root, args.chapter)
        print(hints or "(no active overrides)")
        return 0

    return 1


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Override Contract Engine — versioned rule management")
    ap.add_argument("--project-root", required=True, help="Book project root")
    sub = ap.add_subparsers(dest="action", required=True)

    p_add = sub.add_parser("add", help="Add a new override (supersedes previous)")
    p_add.add_argument("--constraint-id", required=True, help="e.g. power.flight_limit")
    p_add.add_argument("--old-rule", required=True, help="Previous rule text")
    p_add.add_argument("--new-rule", required=True, help="New rule text")
    p_add.add_argument("--rationale", required=True, help="Why the rule changed")
    p_add.add_argument("--chapter", type=int, required=True, help="Chapter where the override occurred")
    p_add.add_argument("--domain", default="world_rule", help="Rule domain (world_rule, power, relationship, etc.)")

    p_list = sub.add_parser("list", help="List effective (current) rules")
    p_list.add_argument("--domain", help="Filter by domain")

    p_hist = sub.add_parser("history", help="Show version history for a constraint")
    p_hist.add_argument("--constraint-id", required=True)

    p_ctx = sub.add_parser("context", help="Generate context hints for target chapter")
    p_ctx.add_argument("--chapter", type=int, required=True)

    args = ap.parse_args()
    raise SystemExit(cmd_override(args))
