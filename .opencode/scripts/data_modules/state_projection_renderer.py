#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Render human-readable markdown projections from state.json + index.db.

Pure Python — no LLM calls. Each renderer produces one markdown file.
Triggered after chapter-commit, ssot rebuild, or manually via CLI.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

HEADER = "> 此文件由系统自动生成，请勿手动编辑。数据源: state.json + index.db\n\n"


def _render_world_state(state: dict, project_root: Path) -> str:
    """世界观状态：实体状态 + 主角信息 + 世界规则。"""
    lines = [HEADER, "# 世界观状态\n"]
    ps = state.get("protagonist_state") or {}
    if ps:
        lines.append("## 主角状态\n")
        for k, v in ps.items():
            if k != "location":
                lines.append(f"- **{k}**: {v}")
        loc = ps.get("location", {})
        if isinstance(loc, dict) and loc:
            lines.append(f"- **位置**: {loc.get('current', '未知')}")
        lines.append("")

    entities = state.get("entities_v3") or {}
    if entities:
        lines.append("## 实体状态\n")
        for eid, info in sorted(entities.items()):
            if not isinstance(info, dict):
                continue
            cs = info.get("current_state") or {}
            state_str = ", ".join(f"{k}={v}" for k, v in cs.items()) if cs else "无特殊状态"
            lines.append(f"- **{info.get('name', eid)}** ({info.get('entity_type', '未知')}): {state_str}")
        lines.append("")

    rules = state.get("world_rules") or []
    if rules:
        lines.append("## 世界规则\n")
        for r in rules:
            status_icon = "\U0001f7e2" if r.get("status") == "active" else "\U0001f534"
            lines.append(f"- {status_icon} {r.get('description', '')}")
            if r.get("status") == "broken":
                lines.append(f"  - 打破于第{r.get('broken_chapter', '?')}章: {r.get('broken_reason', '')}")
        lines.append("")

    if not entities and not rules and not ps:
        lines.append("（暂无数据）\n")

    return "\n".join(lines)


def _render_foreshadowing_panel(state: dict, project_root: Path) -> str:
    """伏笔面板：活跃伏笔 + 已闭合伏笔。

    数据源为 state.plot_threads.foreshadowing（SSOT writer 从 open_loop
    事件聚合的真相，dashboard 伏笔面板也读同一路径）；兼容顶层
    foreshadowing 旧占位。
    """
    lines = [HEADER, "# 伏笔面板\n"]
    plot_threads = state.get("plot_threads")
    nested = plot_threads.get("foreshadowing") if isinstance(plot_threads, dict) else None
    legacy = state.get("foreshadowing") or []
    fs = nested if isinstance(nested, list) else legacy
    active = [f for f in fs if f.get("status") == "active"]
    closed = [f for f in fs if f.get("status") in ("resolved", "closed")]

    lines.append(f"## 活跃伏笔（{len(active)}）\n")
    for f in active:
        urgency_raw = f.get("urgency")
        urgency = urgency_raw if isinstance(urgency_raw, (int, float)) and not isinstance(urgency_raw, bool) else 50
        bar = "█" * min(10, max(1, urgency // 10)) + "░" * (10 - min(10, max(1, urgency // 10)))
        tier = f.get("tier")
        tier_label = f"（{tier}）" if tier else ""
        target = f.get("target_chapter")
        target_label = f"，目标第{target}章回收" if target else ""
        lines.append(f"- **第{f.get('planted_chapter', '?')}章**: {f.get('content', '')}{tier_label}{target_label}")
        lines.append(f"  - 紧迫度: [{bar}] {urgency}%")
    if not active:
        lines.append("（暂无活跃伏笔）\n")

    lines.append(f"\n## 已闭合伏笔（{len(closed)}）\n")
    for f in closed[-10:]:
        resolved_chapter = f.get("resolved_chapter") or f.get("closed_chapter")
        lines.append(f"- ~~第{f.get('planted_chapter', '?')}章: {f.get('content', '')}~~ → 第{resolved_chapter if resolved_chapter is not None else '?'}章闭合")
    if not closed:
        lines.append("（暂无已闭合伏笔）\n")

    return "\n".join(lines)


def _render_character_matrix(state: dict, project_root: Path) -> str:
    """角色关系矩阵。"""
    lines = [HEADER, "# 角色关系矩阵\n"]
    rels = state.get("relationships") or []
    entities = state.get("entities_v3") or {}

    # Handle both dict and list formats
    if isinstance(rels, dict):
        # Convert dict format to list format
        rel_list = []
        for key, value in rels.items():
            if not isinstance(value, dict):
                continue
            # Try to parse "from-to" key format
            parts = key.split("-", 1)
            if len(parts) == 2:
                value = {**value, "from": parts[0], "to": parts[1]}
            rel_list.append(value)
        rels = rel_list

    if not rels:
        lines.append("（暂无关系数据）\n")
        return "\n".join(lines)

    def name_for(eid):
        return entities.get(eid, {}).get("name", eid)

    lines.append("| 角色A | 关系 | 角色B | 最后出现章 |")
    lines.append("|-------|------|-------|-----------|")
    for r in rels:
        lines.append(f"| {name_for(r.get('from', ''))} | {r.get('type', '')} | {name_for(r.get('to', ''))} | 第{r.get('last_seen_chapter', '?')}章 |")

    return "\n".join(lines)


def _render_power_system(state: dict, project_root: Path) -> str:
    """力量体系：角色境界 + 世界规则中的力量相关规则。"""
    lines = [HEADER, "# 力量体系\n"]

    entities = state.get("entities_v3") or {}
    power_entities = []
    for eid, info in entities.items():
        if not isinstance(info, dict):
            continue
        cs = info.get("current_state") or {}
        realm = cs.get("realm")
        if realm:
            power_entities.append((info.get("name", eid), realm, info.get("entity_type", "")))

    if power_entities:
        lines.append("## 角色境界\n")
        for name, realm, etype in sorted(power_entities):
            lines.append(f"- **{name}** ({etype}): {realm}")
    else:
        lines.append("（暂无境界数据）\n")

    rules = state.get("world_rules") or []
    power_rules = [r for r in rules if any(kw in r.get("description", "") for kw in ("境界", "力量", "修炼", "突破", "禁制"))]
    if power_rules:
        lines.append("\n## 力量相关规则\n")
        for r in power_rules:
            status_icon = "\U0001f7e2" if r.get("status") == "active" else "\U0001f534"
            lines.append(f"- {status_icon} {r.get('description', '')}")

    return "\n".join(lines)


def _render_chapter_index(state: dict, project_root: Path) -> str:
    """章节摘要索引。"""
    lines = [HEADER, "# 章节摘要\n"]
    summaries_dir = project_root / ".webnovel" / "summaries"

    progress = state.get("progress") or {}
    ch_status = progress.get("chapter_status") or {}

    if not ch_status:
        lines.append("（暂无章节记录）\n")
        return "\n".join(lines)

    lines.append("| 章节 | 状态 |")
    lines.append("|------|------|")
    for ch in sorted(ch_status.keys(), key=lambda x: int(x) if x.isdigit() else 0):
        raw = ch_status[ch]
        if isinstance(raw, dict):
            status = raw.get("status", "unknown")
        else:
            status = str(raw)
        icon = "✅" if "committed" in status else "📝" if "draft" in status else "❓"
        lines.append(f"| 第{ch}章 | {icon} {status} |")

    lines.append(f"\n共 {len(ch_status)} 章。")
    if summaries_dir.is_dir():
        count = len(list(summaries_dir.glob("ch*.md")))
        lines.append(f" 其中 {count} 章有摘要文件。\n")

    return "\n".join(lines)


def render_all_projections(project_root: Path) -> dict[str, Path]:
    """从结构化数据渲染所有 markdown 投影。"""
    state_path = project_root / ".webnovel" / "state.json"
    if not state_path.is_file():
        return {}

    state = json.loads(state_path.read_text(encoding="utf-8"))

    renderers: dict[str, Callable[..., str]] = {
        "世界观状态.md": _render_world_state,
        "伏笔面板.md": _render_foreshadowing_panel,
        "角色关系矩阵.md": _render_character_matrix,
        "力量体系.md": _render_power_system,
        "章节摘要.md": _render_chapter_index,
    }

    output_dir = project_root / "story"
    output_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Path] = {}
    for filename, renderer in renderers.items():
        path = output_dir / filename
        path.write_text(renderer(state, project_root), encoding="utf-8")
        results[filename] = path

    return results


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Render markdown projections from state.json")
    ap.add_argument("--project-root", required=True)
    args = ap.parse_args()
    results = render_all_projections(Path(args.project_root))
    for name, path in results.items():
        print(f"  {name} → {path}")
    print(f"Rendered {len(results)} projection files.")


if __name__ == "__main__":
    import sys
    from runtime_compat import enable_windows_utf8_stdio
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
