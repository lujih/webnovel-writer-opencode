#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""关系驱动的上下文（外部实践：NovelCrafter 的 Codex relations）。

调研发现：索引层早有 `relationships` 表（from/to/type/chapter）与
`get_entity_relationships()`，但 `context_manager._build_pack()` **完全没有消费**
它——写章时只知道"谁最近出场过"，不知道"这些人之间是什么关系"，同一段关系
于是在章节间被反复重新发明。

本文件钉住：
1. 只查**出场实体**的一跳关系（不做多跳扩展）；
2. 实体数上限（`_RELATION_MAX_ENTITIES`）与每实体条数上限（`_RELATION_PER_ENTITY`）都生效；
3. 按 (from, to, type) 去重，但 A→B 与 B→A 各自保留；
4. 索引层抛异常时降级为空，不让整次上下文组装失败；
5. **真的接进了 `_build_pack`**——本项目历史上「写了但没接线」出现过多次。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.context_manager import ContextManager  # noqa: E402
from data_modules.index_manager import RelationshipMeta  # noqa: E402


@pytest.fixture
def manager(tmp_path) -> ContextManager:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    return ContextManager(DataModulesConfig.from_project_root(str(tmp_path)))


def _rel(manager: ContextManager, a: str, b: str, rel_type: str = "盟友",
         chapter: int = 1, description: str = "") -> None:
    manager.index_manager.upsert_relationship(RelationshipMeta(
        from_entity=a, to_entity=b, type=rel_type,
        description=description, chapter=chapter,
    ))


class TestOneHopLookup:
    def test_hits_relationship_of_appearing_entity(self, manager):
        _rel(manager, "xiaoyan", "yaolao", "师徒", chapter=3, description="药老收其为徒")
        rows = manager._load_relationship_context(["xiaoyan"])
        assert len(rows) == 1
        assert rows[0] == {
            "from": "xiaoyan",
            "to": "yaolao",
            "type": "师徒",
            "description": "药老收其为徒",
            "chapter": 3,
        }

    def test_both_directions_kept(self, manager):
        _rel(manager, "a", "b", "盟友")
        _rel(manager, "b", "a", "仇敌")
        rows = manager._load_relationship_context(["a"])
        assert {(r["from"], r["to"], r["type"]) for r in rows} == {
            ("a", "b", "盟友"), ("b", "a", "仇敌"),
        }

    def test_entity_without_relations(self, manager):
        _rel(manager, "a", "b")
        assert manager._load_relationship_context(["nobody"]) == []

    def test_empty_input(self, manager):
        assert manager._load_relationship_context([]) == []
        assert manager._load_relationship_context(None) == []


class TestCapsAndDedup:
    def test_per_entity_cap(self, manager):
        for i in range(8):
            _rel(manager, "a", f"n{i}", "同门")
        rows = manager._load_relationship_context(["a"], per_entity=3)
        assert len(rows) == 3

    def test_entity_cap(self, manager):
        for i in range(8):
            _rel(manager, f"e{i}", f"target{i}", "同门")
        ids = [f"e{i}" for i in range(8)]
        rows = manager._load_relationship_context(ids, max_entities=5)
        assert len(rows) == 5
        assert {r["from"] for r in rows} == {f"e{i}" for i in range(5)}

    def test_duplicate_entity_ids_do_not_duplicate_rows(self, manager):
        _rel(manager, "a", "b")
        rows = manager._load_relationship_context(["a", "a", "a"])
        assert len(rows) == 1

    def test_default_caps_are_documented_limits(self, manager):
        assert manager._RELATION_MAX_ENTITIES == 5
        assert manager._RELATION_PER_ENTITY == 5

    def test_index_error_degrades_without_raising(self, manager, monkeypatch):
        def boom(_entity_id):
            raise RuntimeError("index unavailable")

        monkeypatch.setattr(manager.index_manager, "get_entity_relationships", boom)
        assert manager._load_relationship_context(["a"]) == []


class TestWiring:
    def test_build_pack_actually_calls_it(self):
        """防止「写了但没接线」——本项目历史上出现过多次。"""
        source = inspect.getsource(ContextManager._build_pack)
        assert "_load_relationship_context(" in source, (
            "_build_pack 没有调用 _load_relationship_context，"
            "关系上下文等于没实现"
        )
        assert '"relationship_context"' in source, (
            "scene 段没有 relationship_context 键，下游读不到"
        )

    def test_scene_section_exists_in_payload_contract(self):
        """scene 是 SECTION_ORDER 里的段，关系上下文随它一起进 payload。"""
        assert "scene" in ContextManager.SECTION_ORDER
