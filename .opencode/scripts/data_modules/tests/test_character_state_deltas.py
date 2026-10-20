#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`character_state_changed` 必须产出下游能识别的结构化字段。

## 契约漂移的确凿证据

`observer_settler._extract_character_state_changes()` 只产出描述：

    "payload": {
        "entity_id": eid,
        "entity_name": name,
        "description": desc,        # ← 只有自由文本
    }

而 `state_projection_writer._collect_state_deltas()` 要求 `field`：

    elif event_type == "character_state_changed":
        field = str(payload.get("field") or payload.get("field_path") or "").strip()
    ...
    key = (entity_id, field)
    if not field or key in seen:
        continue                     # ← field 为空 → 直接跳过

实测：1 条 `character_state_changed` 事件 → 提取出 **0 条** `state_deltas`。
**角色状态变化完全丢失**，不进 state.json 的任何结构化字段。

同一个模块里的 `_extract_power_breakthroughs()` 却产出了结构化的
`old_realm`/`new_realm`，而且 `state_projection_writer` 专门为它写了兼容：

    # 兼容 observer_settler 产出的 new_realm/old_realm 格式
    if "new" not in payload and "new_realm" in payload:
        payload["new"] = payload["new_realm"]

也就是说：**同一个 settler 的两种事件，一种被下游接纳、一种被丢弃**，
差别只在提取器有没有给出结构化字段。这不是 prompt 的问题，是代码内部不一致。

## 本文件钉住

- 能解析出结构化变化（`从X变为Y` / `突破到Y` / `X：Y` 等）时，
  payload 要带 `field` + `old` + `new`
- 解析不出时**仍保留 `description`**（不能因为拿不到结构就丢事件）
- 下游 `_collect_state_deltas()` 能真正拿到非空结果
- 既有 `description` 字段不丢（向后兼容）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.observer_settler import settle  # noqa: E402
from data_modules.state_projection_writer import StateProjectionWriter  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text(json.dumps({
        "entities_v3": {"角色": {
            "xiaoyan": {"entity_type": "角色", "canonical_name": "萧炎"},
        }},
    }, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _settle(project: Path, lines: list[str]) -> list[dict]:
    facts = project / "facts.md"
    facts.write_text("## 角色状态变化\n" + "\n".join(lines) + "\n", encoding="utf-8")
    result = settle(facts, project, 1)
    return list(result.get("accepted_events") or [])


def _deltas(project: Path, events: list[dict]) -> list[dict]:
    writer = StateProjectionWriter(project)
    payload = {"extraction_result": {"state_deltas": [], "entity_deltas": [],
                                     "accepted_events": events}}
    return writer._collect_state_deltas(payload)


class TestStructuredChangesProduceDeltas:
    """核心：能解析出结构时必须产出 state_deltas。"""

    def test_transition_from_to(self, project):
        events = _settle(project, ["- 萧炎（entity_id: xiaoyan）：境界从斗者变为斗师"])
        assert events, "settler 应产出事件"
        payload = events[0]["payload"]
        assert "field" in payload, (
            f"payload 缺 field：{payload}。下游 `_collect_state_deltas` 见 field "
            f"为空就 continue，事件被丢弃。"
        )
        assert "old" in payload and "new" in payload, f"payload 缺 old/new：{payload}"

        deltas = _deltas(project, events)
        assert deltas, "1 条 character_state_changed 却提取出 0 条 state_deltas——数据全丢"

    def test_breakthrough_style(self, project):
        events = _settle(project, ["- 萧炎（entity_id: xiaoyan）：突破到斗师"])
        deltas = _deltas(project, events)
        assert deltas, f"「突破到斗师」应能产出 delta，实际 {deltas}"

    def test_explicit_field_colon_value(self, project):
        events = _settle(project, ["- 萧炎（entity_id: xiaoyan）：境界：斗师"])
        deltas = _deltas(project, events)
        assert deltas, f"「境界：斗师」应能产出 delta，实际 {deltas}"


class TestDescriptionPreserved:
    """向后兼容：description 不能丢。"""

    def test_description_still_present(self, project):
        events = _settle(project, ["- 萧炎（entity_id: xiaoyan）：突破到斗师"])
        assert events
        assert events[0]["payload"].get("description"), (
            f"description 被移除了：{events[0]['payload']}。"
            f"它仍是人类可读摘要与 memory/vector 投影的输入。"
        )

    def test_description_matches_original_text(self, project):
        events = _settle(project, ["- 萧炎（entity_id: xiaoyan）：突破到斗师"])
        desc = events[0]["payload"]["description"]
        assert "斗师" in desc


class TestUnstructuredStillKept:
    """解析不出结构时仍要保留事件（不能丢）。"""

    def test_freeform_still_emitted(self, project):
        events = _settle(project, ["- 萧炎（entity_id: xiaoyan）：心情沉重，独自离开"])
        assert len(events) == 1, (
            f"无法结构化的事件被丢弃了：{events}。"
            f"拿不到 field 不等于事件不存在——description 仍然有价值。"
        )
        assert events[0]["payload"].get("description")

    def test_freeform_produces_no_delta_but_no_crash(self, project):
        events = _settle(project, ["- 萧炎（entity_id: xiaoyan）：心情沉重"])
        deltas = _deltas(project, events)
        assert deltas == [], "自由文本不该产出结构化 delta"


class TestMultipleLines:
    """多行混合：能结构的产出 delta，不能的只留 description。"""

    def test_mixed(self, project):
        events = _settle(project, [
            "- 萧炎（entity_id: xiaoyan）：境界从斗者变为斗师",
            "- 萧炎（entity_id: xiaoyan）：心情沉重",
        ])
        assert len(events) == 2, f"应保留两条事件，实际 {len(events)}"
        deltas = _deltas(project, events)
        assert len(deltas) == 1, f"应恰好一条 delta（结构化的那条），实际 {deltas}"


class TestDownstreamCompatibility:
    """下游 writer 的既有兼容路径仍要工作。"""

    def test_power_breakthrough_compat_unchanged(self, project):
        """`power_breakthrough` 的 new_realm→new 兼容不能被我改坏。"""
        events = [{
            "event_id": "e1", "chapter": 1,
            "event_type": "power_breakthrough", "subject": "xiaoyan",
            "payload": {"entity_id": "xiaoyan", "old_realm": "斗者",
                        "new_realm": "斗师"},
        }]
        deltas = _deltas(project, events)
        assert deltas, "power_breakthrough 的兼容路径失效了"
        assert deltas[0]["new"] == "斗师"
