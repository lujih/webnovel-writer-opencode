#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Settler: parse observer free-text output → validated StoryEvent list.

Pure Python — no LLM calls. Extracts structured events from markdown sections
via regex/keyword matching, resolves entity references against known entities,
and validates via Pydantic StoryEvent schema.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path

try:
    from .story_event_schema import StoryEvent
except ImportError:
    # __main__ fallback — add parent dir to path for sibling imports
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from runtime_compat import enable_windows_utf8_stdio  # noqa: F811
    from story_event_schema import StoryEvent  # noqa: F811
else:
    from runtime_compat import enable_windows_utf8_stdio

logger = logging.getLogger(__name__)


def _load_known_entities(project_root: Path) -> dict[str, dict]:
    """Load known entities from state.json entities_v3 for disambiguation."""
    state_path = project_root / ".webnovel" / "state.json"
    if not state_path.is_file():
        return {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return state.get("entities_v3", {})
    except (OSError, ValueError):
        return {}


def _resolve_entity(name_or_id: str, known: dict[str, dict]) -> str:
    """Resolve an entity reference to its canonical entity_id."""
    if name_or_id in known:
        return name_or_id
    for eid, info in known.items():
        if info.get("name", "") == name_or_id:
            return eid
    return name_or_id


def _normalize_heading(heading: str) -> str:
    """Normalize heading: strip numbering prefix and whitespace."""
    # Remove leading numbering like "1. ", "2. ", "① "
    import re as _re
    return _re.sub(r'^[\d①②③④⑤⑥⑦⑧⑨⑩]+[.、]\s*', '', heading).strip()


def _parse_markdown_sections(text: str) -> dict[str, list[str]]:
    """Parse observer output into sections keyed by heading name."""
    sections: dict[str, list[str]] = {}
    current_heading: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            current_heading = _normalize_heading(stripped[3:])
            sections.setdefault(current_heading, [])
        elif current_heading and stripped:
            sections[current_heading].append(stripped)
    return sections


def _extract_character_state_changes(lines: list[str], known: dict[str, dict], chapter: int) -> list[dict]:
    events: list[dict] = []
    for line in lines:
        m = re.match(r'- (.+?)（entity_id:\s*([^），]+?)）\s*[：:]\s*(.+)', line)
        if m:
            name, eid_raw, desc = m.groups()
            eid_raw = eid_raw.strip()
            eid = _resolve_entity(name, known) if eid_raw in ("未知", "新") else _resolve_entity(eid_raw, known)
            events.append({
                "event_id": f"evt-ch{chapter:03d}-state-{len(events):03d}",
                "chapter": chapter,
                "event_type": "character_state_changed",
                "subject": eid,
                "payload": {
                    "entity_id": eid,
                    "entity_name": name,
                    "description": desc,
                },
            })
    return events


def _extract_relationships(lines: list[str], known: dict[str, dict], chapter: int) -> list[dict]:
    events: list[dict] = []
    for line in lines:
        m = re.match(r'- (.+?)\s*↔\s*(.+?)\s*：关系从(.+?)变为(.+)', line)
        if m:
            a, b, old_rel, new_rel = m.groups()
            events.append({
                "event_id": f"evt-ch{chapter:03d}-rel-{len(events):03d}",
                "chapter": chapter,
                "event_type": "relationship_changed",
                "subject": _resolve_entity(a.strip(), known),
                "payload": {
                    "from_entity": _resolve_entity(a.strip(), known),
                    "to_entity": _resolve_entity(b.strip(), known),
                    "relationship_type": new_rel.strip(),
                    "description": f"从{old_rel.strip()}变为{new_rel.strip()}",
                },
            })
    return events


def _extract_power_breakthroughs(lines: list[str], known: dict[str, dict], chapter: int) -> list[dict]:
    events: list[dict] = []
    for line in lines:
        m = re.match(r'- (.+?)（entity_id:\s*([^），]+?)）\s*[：:]\s*从(.+?)突破至(.+)', line)
        if m:
            name, eid_raw, old_realm, new_realm = m.groups()
            eid_raw = eid_raw.strip()
            eid = _resolve_entity(name, known) if eid_raw in ("未知", "新") else _resolve_entity(eid_raw, known)
            events.append({
                "event_id": f"evt-ch{chapter:03d}-power-{len(events):03d}",
                "chapter": chapter,
                "event_type": "power_breakthrough",
                "subject": eid,
                "payload": {
                    "entity_id": eid,
                    "entity_name": name.strip(),
                    "old_realm": old_realm.strip(),
                    "new_realm": new_realm.strip(),
                },
            })
    return events


def _extract_entity_creations(lines: list[str], known: dict[str, dict], chapter: int) -> list[dict]:
    events: list[dict] = []
    for line in lines:
        m = re.match(r'- (.+?)（类型[：:]\s*(\S+?)[，,]\s*entity_id[：:]\s*(\S+?)）\s*[：:]\s*(.*)', line)
        if m:
            name, etype, eid_raw, desc = m.groups()
            eid = eid_raw.strip() if eid_raw.strip() != "新" else name.strip()
            events.append({
                "event_id": f"evt-ch{chapter:03d}-entity-{len(events):03d}",
                "chapter": chapter,
                "event_type": "entity_created",
                "subject": eid,
                "payload": {
                    "entity_id": eid,
                    "entity_type": etype.strip(),
                    "entity_name": name.strip(),
                },
            })
    return events


def _extract_artifact_acquisitions(lines: list[str], known: dict[str, dict], chapter: int) -> list[dict]:
    events: list[dict] = []
    for line in lines:
        m = re.match(r'- (.+?)（entity_id:\s*([^），]+?)）\s*[：:]\s*被(\S+?)获得/使用', line)
        if m:
            item_name, eid_raw, owner = m.groups()
            eid = eid_raw.strip() if eid_raw.strip() != "新" else item_name.strip()
            events.append({
                "event_id": f"evt-ch{chapter:03d}-artifact-{len(events):03d}",
                "chapter": chapter,
                "event_type": "artifact_obtained",
                "subject": eid,
                "payload": {
                    "artifact_id": eid,
                    "name": item_name.strip(),
                    "owner": _resolve_entity(owner.strip(), known),
                },
            })
    return events


def _extract_world_rule_revealed(lines: list[str], chapter: int) -> list[dict]:
    events: list[dict] = []
    for line in lines:
        m = re.match(r'- 新规则[：:]\s*(.+)', line)
        if m:
            desc = m.group(1).strip()
            events.append({
                "event_id": f"evt-ch{chapter:03d}-wrreveal-{len(events):03d}",
                "chapter": chapter,
                "event_type": "world_rule_revealed",
                "subject": f"rule_ch{chapter}",
                "payload": {
                    "rule_id": f"rule_ch{chapter}_{len(events):03d}",
                    "description": desc,
                },
            })
    return events


def _extract_world_rule_broken(lines: list[str], chapter: int) -> list[dict]:
    events: list[dict] = []
    for line in lines:
        m = re.match(r'- 被打破的规则[：:]\s*(.+?)[。.]\s*打破方式[：:]\s*(.+)', line)
        if m:
            rule_desc, how = m.groups()
            events.append({
                "event_id": f"evt-ch{chapter:03d}-wrbreak-{len(events):03d}",
                "chapter": chapter,
                "event_type": "world_rule_broken",
                "subject": f"rule_ch{chapter}",
                "payload": {
                    "description": rule_desc.strip(),
                    "reason": how.strip(),
                },
            })
    return events


def _extract_promises(lines: list[str], chapter: int) -> list[dict]:
    events: list[dict] = []
    for line in lines:
        m_new = re.match(r'- \[新埋设\]\s*(.+)', line)
        m_paid = re.match(r'- \[偿还\]\s*(.+)', line)
        if m_new:
            desc = m_new.group(1).strip()
            events.append({
                "event_id": f"evt-ch{chapter:03d}-promise-{len(events):03d}",
                "chapter": chapter,
                "event_type": "promise_created",
                "subject": f"promise_ch{chapter}",
                "payload": {
                    "promise_id": f"promise_ch{chapter}_{len(events):03d}",
                    "description": desc,
                },
            })
        elif m_paid:
            desc = m_paid.group(1).strip()
            events.append({
                "event_id": f"evt-ch{chapter:03d}-promise-{len(events):03d}",
                "chapter": chapter,
                "event_type": "promise_paid_off",
                "subject": f"promise_ch{chapter}",
                "payload": {
                    "description": desc,
                },
            })
    return events


def _extract_open_loops(lines: list[str], chapter: int) -> list[dict]:
    events: list[dict] = []
    for line in lines:
        m_new = re.match(r'- \[新伏笔\]\s*(.+?)（紧迫度[：:]\s*(\d+)）', line)
        m_closed = re.match(r'- \[闭合\]\s*(.+)', line)
        if m_new:
            content, urgency = m_new.groups()
            events.append({
                "event_id": f"evt-ch{chapter:03d}-loop-{len(events):03d}",
                "chapter": chapter,
                "event_type": "open_loop_created",
                "subject": f"loop_ch{chapter}",
                "payload": {
                    "content": content.strip(),
                    "urgency": int(urgency),
                },
            })
        elif m_closed:
            content = m_closed.group(1).strip()
            events.append({
                "event_id": f"evt-ch{chapter:03d}-loop-{len(events):03d}",
                "chapter": chapter,
                "event_type": "open_loop_closed",
                "subject": f"loop_ch{chapter}",
                "payload": {
                    "content": content,
                },
            })
    return events


def settle(raw_facts_path: Path, project_root: Path, chapter: int) -> dict:
    """Parse observer output → validated StoryEvent list."""
    text = raw_facts_path.read_text(encoding="utf-8")
    sections = _parse_markdown_sections(text)
    known = _load_known_entities(project_root)

    heading_extractors = [
        ("角色状态变化", _extract_character_state_changes),
        ("关系变化", _extract_relationships),
        ("力量突破", _extract_power_breakthroughs),
        ("新出场实体", _extract_entity_creations),
        ("宝物/物品获得", _extract_artifact_acquisitions),
        ("世界规则揭示", _extract_world_rule_revealed),
        ("世界规则打破", _extract_world_rule_broken),
        ("对读者的承诺/伏笔", _extract_promises),
        ("伏笔创建与闭合", _extract_open_loops),
    ]

    _NEEDS_KNOWN = {"角色状态变化", "关系变化", "力量突破", "新出场实体", "宝物/物品获得"}

    all_events: list[dict] = []
    for heading, extractor in heading_extractors:
        lines = sections.get(heading, [])
        if heading in _NEEDS_KNOWN:
            all_events.extend(extractor(lines, known, chapter))
        else:
            all_events.extend(extractor(lines, chapter))

    validated: list[dict] = []
    dropped = 0
    for evt in all_events:
        try:
            validated.append(StoryEvent.model_validate(evt).model_dump())
        except Exception:
            dropped += 1
    if dropped:
        logger.warning("settler: %d/%d events dropped by Pydantic validation",
                       dropped, len(all_events))

    entities_appeared = list({e["subject"] for e in validated})

    # 从 accepted_events 提取 state_deltas 和 entity_deltas
    state_deltas = []
    entity_deltas = []
    for evt in validated:
        event_type = evt.get("event_type", "")
        payload = evt.get("payload", {})
        subject = evt.get("subject", "")
        if event_type in ("character_state_changed", "power_breakthrough"):
            # 缺 field 时的兜底必须**按事件类型区分**：power_breakthrough 缺省
            # 就是 realm 没错，但 character_state_changed 兜底成 realm 会把一段
            # 自由文本（如 new_state="重伤"）写进 current_state["realm"]——
            # 用散文污染结构化字段，下游按枚举值比较 realm 的地方会直接失效。
            if event_type == "power_breakthrough":
                field = payload.get("field") or payload.get("field_path") or "realm"
            else:
                field = payload.get("field") or payload.get("field_path") or "state"
            old_val = payload.get("old") or payload.get("old_value") or payload.get("previous_state")
            new_val = payload.get("new") or payload.get("new_value") or payload.get("new_state") or payload.get("new_realm")
            if subject and field and new_val is not None:
                state_deltas.append({
                    "entity_id": subject,
                    "field": field,
                    "old": old_val,
                    "new": new_val,
                    "reason": event_type,
                    "chapter": evt.get("chapter"),
                })
        if event_type == "relationship_changed":
            from_e = payload.get("from_entity") or subject
            to_e = payload.get("to_entity") or payload.get("to")
            rel_type = payload.get("relationship_type") or payload.get("type")
            if from_e and to_e and rel_type:
                entity_deltas.append({
                    "from_entity": from_e,
                    "to_entity": to_e,
                    "relationship_type": rel_type,
                    "description": payload.get("description", ""),
                    "chapter": evt.get("chapter"),
                })

    return {
        "accepted_events": validated,
        "state_deltas": state_deltas,
        "entity_deltas": entity_deltas,
        "entities_appeared": entities_appeared,
        "scenes": [],       # 由 data-agent 在 Step 5.1c 补全
        "chapter_meta": {},  # 由 data-agent 在 Step 5.1c 补全
        "dominant_strand": "",  # 由 data-agent 在 Step 5.1c 补全
        "summary_text": "",    # 由 data-agent 在 Step 5.1c 补全
    }


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Settle observer raw facts into extraction_result.json")
    ap.add_argument("--raw-facts", required=True, help="Path to observer raw_facts.txt")
    ap.add_argument("--project-root", required=True)
    ap.add_argument("--chapter", type=int, required=True)
    ap.add_argument("--output", required=True, help="Path to write extraction_result.json")
    args = ap.parse_args()

    result = settle(Path(args.raw_facts), Path(args.project_root), args.chapter)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"settler: {len(result['accepted_events'])} events written to {args.output}")


if __name__ == "__main__":
    if sys.platform == "win32":
        enable_windows_utf8_stdio()
    main()
