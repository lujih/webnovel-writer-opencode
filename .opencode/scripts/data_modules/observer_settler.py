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
    from .entity_index_utils import flatten_entities
    from .story_event_schema import StoryEvent
except ImportError:
    # __main__ fallback — add parent dir to path for sibling imports
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from data_modules.entity_index_utils import flatten_entities  # noqa: F811
    from runtime_compat import enable_windows_utf8_stdio  # noqa: F811
    from story_event_schema import StoryEvent  # noqa: F811
else:
    from runtime_compat import enable_windows_utf8_stdio

logger = logging.getLogger(__name__)


def _load_known_entities(project_root: Path) -> dict[str, dict]:
    """Load known entities from state.json entities_v3 for disambiguation.

    返回**扁平**的 ``{entity_id: record}`` 视图：消歧逻辑只按 id / name 匹配，
    而 `entities_v3` 既可能是权威嵌套形状（类型 → id → 记录），也可能是旧
    rebuild 产出的扁平形状，两种都要能用。
    """
    state_path = project_root / ".webnovel" / "state.json"
    if not state_path.is_file():
        return {}
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        return flatten_entities(state.get("entities_v3"))
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


#: 「括号内 entity_id 段」的容错匹配。
#:
#: prompt（observer-agent.md:67）明确要求「过度提取……不确定的实体可以写
#: 描述」，模型因此常在括号里补一句说明：
#:
#:     - 萧炎（entity_id: xiaoyan，置信度0.9）：突破到斗师
#:     - 萧炎（entity_id: 未知，不确定）：突破到斗师
#:
#: 旧写法 `（entity_id:\s*([^），]+?)）` 用字符类禁掉了逗号与右括号，
#: 于是模型每多写一个字，**整行就被静默丢弃**——没有日志、没有计数。
#: 这里改为「一直吃到右括号」，再由 `_split_entity_id_segment()` 把
#: 首个成分当作 id、其余当作注释丢掉。
_ENTITY_ID_SEGMENT = r'（entity_id\s*[：:]\s*(?P<eid>[^）]*?)）'


def _split_entity_id_segment(segment: str) -> str:
    """从 ``xiaoyan，置信度0.9`` 里取出 ``xiaoyan``。

    分隔符覆盖模型实际会用到的几种：中文/英文逗号、分号、空白、
    以及"（如不确定写…）"这类括号补充。
    """
    text = str(segment or "").strip()
    if not text:
        return ""
    # 先按显式分隔符切
    for sep in ("，", ",", "；", ";", "。", "\t", " "):
        if sep in text:
            text = text.split(sep, 1)[0]
            break
    return text.strip()


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
        m = re.match(r'- (.+?)' + _ENTITY_ID_SEGMENT + r'\s*[：:]\s*(.+)', line)
        if m:
            name, eid_raw, desc = m.groups()
            eid_raw = _split_entity_id_segment(eid_raw)
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
        m = re.match(r'- (.+?)' + _ENTITY_ID_SEGMENT + r'\s*[：:]\s*从(.+?)突破至(.+)', line)
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
        m = re.match(
            r'- (.+?)（类型[：:]\s*([^，,）]+?)[，,]\s*entity_id[：:]\s*([^）]*?)）\s*[：:]\s*(.*)',
            line,
        )
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
        m = re.match(r'- (.+?)' + _ENTITY_ID_SEGMENT + r'\s*[：:]\s*被(\S+?)获得/使用', line)
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


def _consumed_line_count(heading: str, produced: list[dict]) -> int:
    """估算某个 section 里"被成功解析"的行数。

    提取器是「逐行尝试 `re.match`，成功就 append 一条事件」的形态，
    正常情况下 1 行 → ≤1 事件。所以取 `len(produced)` 作为已消费行数
    的下界估计：未解析行数 ≈ 总行数 - 产出数。

    这对告警足够（不会漏报，可能在"一行产生多事件"的提取器上少报），
    而且不需要改动 9 个提取器的签名。
    """
    return len(produced)


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
    # 逐 (section, 行) 统计：正则没匹配上的行必须能报出来。
    # 否则「observer 提 30 条、settler 出 3 条」在日志里看起来一切正常。
    unparsed_lines: list[str] = []
    for heading, extractor in heading_extractors:
        lines = sections.get(heading, [])
        if heading in _NEEDS_KNOWN:
            produced = extractor(lines, known, chapter)
        else:
            produced = extractor(lines, chapter)
        all_events.extend(produced)

        # 提取器只能告诉我们"产出了几条"，无法逐行对应；用计数差估算
        # 未解析行数——足够触发告警，也不会误报为 0。
        consumed = _consumed_line_count(heading, produced)
        if len(lines) > consumed:
            unparsed_lines.extend(lines[consumed:])

    # 标题不在白名单里的 section：内容会整段消失。这类最危险——
    # observer 换了措辞（或用了 prompt 没列的同义标题）就全丢。
    known_headings = {h for h, _ in heading_extractors}
    unknown_sections = {
        name: lines for name, lines in sections.items()
        if name not in known_headings and lines
    }
    if unknown_sections:
        logger.warning(
            "settler: %d 个 section 标题不在白名单里，内容已忽略：%s",
            len(unknown_sections), list(unknown_sections),
        )

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
    if unparsed_lines:
        logger.warning(
            "settler: %d 行未能解析成事件（共 %d 行输入）：%s",
            len(unparsed_lines), sum(len(v) for v in sections.values()),
            unparsed_lines[:5],
        )

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

    result = {
        "accepted_events": validated,
        "state_deltas": state_deltas,
        "entity_deltas": entity_deltas,
        "entities_appeared": entities_appeared,
        "scenes": [],       # 由 data-agent 在 Step 5.1c 补全
        "chapter_meta": {},  # 由 data-agent 在 Step 5.1c 补全
        "dominant_strand": "",  # 由 data-agent 在 Step 5.1c 补全
        "summary_text": "",    # 由 data-agent 在 Step 5.1c 补全
    }

    # 提取质量诊断：只在有问题时出现，避免给正常路径加噪声字段。
    # 三个数字回答「observer 提的东西去哪了」：
    #   unparsed_lines    —— 正则没认出来的行
    #   unknown_sections  —— 标题不在白名单、整段内容被忽略
    #   dropped_events    —— 解析出来了但过不了 StoryEvent 校验
    # 没有这三项时，settler 静默退化成"只提取到很少"是看不出来的。
    if unparsed_lines:
        result["unparsed_lines"] = unparsed_lines
    if unknown_sections:
        result["unknown_sections"] = {k: len(v) for k, v in unknown_sections.items()}
    if dropped:
        result["dropped_events"] = dropped
    return result


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
