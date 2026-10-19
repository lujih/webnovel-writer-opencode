#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""实体解析的同名歧义应被上报给 data-agent。

`observer_settler._resolve_entity()` 按 `{entity_id: record}` 解析实体引用：

    def _resolve_entity(name_or_id, known):
        if name_or_id in known:           # 命中 id
            return name_or_id
        for eid, info in known.items():
            if info.get("name", "") == name_or_id:   # 命中名称
                return eid
        return name_or_id                  # 都没命中 → 原样返回

最后那行"原样返回"在**同名歧义**下会出问题：两个实体都叫「萧炎」时，
`for` 循环命中**字典遍历顺序里的第一个**——即实体 id 的字典序最小者。
没有警告、没有计数，用户/agent 无从知道"这个名字指向了两个实体"。

这与 `entity_linker.lookup_alias()` 的旧缺陷是同一类（已在 `e0b3918` 修过
并加了 `lookup_alias_detailed()`），但**这条路径没接上**：
`observer_settler` 完全不碰 `entity_linker`，它自己遍历 `known` 字典。

而 `data-agent.md:93` 的消歧策略是「置信度>0.8 自动采用，0.5-0.8 采用+
warning，<0.5 标记待人工」——它拿到的 `entity_id` 已经是被静默挑好的那个，
**没有任何信号能触发这个分级**。所以同名歧义永远走不到人工复核。

本文件钉住：settle() 应把「某名称命中多个实体」报出来，供 data-agent 消费。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import data_modules.observer_settler as settler  # noqa: E402


def _entity(eid: str, name: str, etype: str = "角色") -> dict:
    return {"entity_type": etype, "canonical_name": name, "tier": "主要"}


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    return tmp_path


def _set_entities(project: Path, entities: dict[str, dict]) -> None:
    """写入嵌套形状的 entities_v3（权威形状）。"""
    by_type: dict[str, dict] = {}
    for eid, rec in entities.items():
        by_type.setdefault(rec.get("entity_type", "角色"), {})[eid] = rec
    (project / ".webnovel" / "state.json").write_text(
        json.dumps({"entities_v3": by_type}, ensure_ascii=False), encoding="utf-8")


def _facts(project: Path, lines: list[str]) -> Path:
    path = project / "facts.md"
    path.write_text("## 角色状态变化\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return path


class TestUnambiguousResolutionUnchanged:
    """唯一匹配时行为不变（别把正常路径改坏）。"""

    def test_id_hit(self):
        known = {"xiaoyan": _entity("xiaoyan", "萧炎")}
        assert settler._resolve_entity("xiaoyan", known) == "xiaoyan"

    def test_name_hit(self):
        known = {"xiaoyan": _entity("xiaoyan", "萧炎")}
        assert settler._resolve_entity("萧炎", known) == "xiaoyan"

    def test_miss_returns_input(self):
        assert settler._resolve_entity("查无此人", {}) == "查无此人"

    def test_settle_has_no_ambiguity_report_when_clean(self, project):
        _set_entities(project, {"xiaoyan": _entity("xiaoyan", "萧炎")})
        facts = _facts(project, ["- 萧炎（entity_id: 未知）：突破到斗师"])
        result = settler.settle(facts, project, 1)
        assert not result.get("ambiguous_mentions"), (
            f"唯一匹配却报了歧义：{result.get('ambiguous_mentions')}"
        )


class TestDuplicateNameIsReported:
    """核心：两个实体同名时必须报出来。"""

    def _dup(self, project: Path) -> None:
        _set_entities(project, {
            "xiaoyan_a": _entity("xiaoyan_a", "萧炎"),
            "xiaoyan_b": _entity("xiaoyan_b", "萧炎"),
        })

    def test_settle_reports_ambiguous_mention(self, project):
        self._dup(project)
        facts = _facts(project, ["- 萧炎（entity_id: 未知）：突破到斗师"])
        result = settler.settle(facts, project, 1)

        assert result.get("ambiguous_mentions"), (
            "两个实体都叫「萧炎」，settle() 静默挑了一个，没有任何信号。"
            "data-agent 的置信度分级（>0.8 自动 / <0.5 待人工）拿不到触发条件，"
            "同名歧义永远走不到人工复核。"
        )

    def test_report_names_the_mention_and_candidates(self, project):
        self._dup(project)
        facts = _facts(project, ["- 萧炎（entity_id: 未知）：突破到斗师"])
        result = settler.settle(facts, project, 1)

        blob = json.dumps(result.get("ambiguous_mentions"), ensure_ascii=False)
        assert "萧炎" in blob, f"报告里没有提及的名字：{blob}"
        assert "xiaoyan_a" in blob and "xiaoyan_b" in blob, (
            f"报告里没有全部候选：{blob}"
        )

    def test_resolved_entity_is_one_of_candidates(self, project):
        """歧义时仍要给出一个结果（保持既有行为），但必须在候选集内。"""
        self._dup(project)
        facts = _facts(project, ["- 萧炎（entity_id: 未知）：突破到斗师"])
        result = settler.settle(facts, project, 1)

        subjects = {e["subject"] for e in result.get("accepted_events") or []}
        assert subjects <= {"xiaoyan_a", "xiaoyan_b"}, (
            f"解析出了候选集之外的 id：{subjects}"
        )

    def test_cross_type_same_name_is_not_ambiguity(self, project):
        """跨类型同名是设计支持的（天云宗 = 地点 + 势力），不算歧义。"""
        _set_entities(project, {
            "tian_a": _entity("tian_a", "天云宗", "地点"),
            "tian_b": _entity("tian_b", "天云宗", "势力"),
        })
        facts = _facts(project, ["- 天云宗（entity_id: 未知）：长老出关"])
        result = settler.settle(facts, project, 1)
        assert not result.get("ambiguous_mentions"), (
            "跨类型同名被误报成歧义——那是设计支持的（地点+势力）"
        )


class TestDuplicateIdIsImpossible:
    """`known` 是 dict，同 id 不可能重复；这条只防回归。"""

    def test_single_entry_per_id(self, project):
        _set_entities(project, {"xiaoyan": _entity("xiaoyan", "萧炎")})
        known = settler._load_known_entities(project)
        assert list(known) == ["xiaoyan"]
