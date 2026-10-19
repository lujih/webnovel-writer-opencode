#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`EntityLinker` 的同类型别名歧义测试。

`aliases` 表的主键是 `(alias, entity_id, entity_type)`，`register_alias`
的文档明确写了「支持一对多：同一别名可映射多个实体（如"天云宗" → 地点 +
势力）」——**这是有意设计**：跨类型同名是正常的。

但**同一类型内**的同名不是正常情况，那是真歧义：

    「炎帝」→ 角色/萧炎A
    「炎帝」→ 角色/萧炎B      ← 两个都是「角色」，到底是哪个？

而 `entity_linker.lookup_alias()` 对这种情况**静默取第一条**：

    entries = self._index_manager.get_entities_by_alias(mention)
    ...
    return entries[0].get("id") if entries else None

`entries` 的顺序来自 `SELECT ... FROM aliases WHERE alias = ?`——没有
`ORDER BY`。当前 SQLite 恰好按主键顺序返回（等于按 entity_id 字母序），
所以现象是"稳定"，但这不是代码保证的：换索引、换 SQLite 版本、加
`ORDER BY RANDOM()` 之类重构都会让它变。更关键的是**用户从未被告知
存在歧义**——他被静默分配了"字母序最小的那个实体的 ID"。

这与本项目在别处的 fail-closed 取向一致：`review_pipeline` 对形状异常
的载荷合成 critical 阻断项，理由是「无法判断有没有问题 ≠ 没有问题」。
别名歧义同理：无法判断指代哪个，就不该静默挑一个。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.entity_linker import EntityLinker  # noqa: E402
from data_modules.index_manager import EntityMeta, IndexManager  # noqa: E402


@pytest.fixture
def cfg(tmp_path: Path) -> DataModulesConfig:
    config = DataModulesConfig.from_project_root(tmp_path)
    config.ensure_dirs()
    return config


@pytest.fixture
def manager(cfg) -> IndexManager:
    return IndexManager(config=cfg)


def _entity(eid: str, name: str, etype: str = "角色") -> EntityMeta:
    return EntityMeta(
        id=eid, type=etype, canonical_name=name, tier="主要", desc="",
        current={}, first_appearance=1, last_appearance=1,
        is_protagonist=False, is_archived=False,
    )


@pytest.fixture
def linker(cfg) -> EntityLinker:
    # EntityLinker 接收 config（内部自己建 IndexManager，连同一个 index.db）
    return EntityLinker(cfg)


class TestUnambiguousLookupsStillWork:
    """正常路径不能改坏。"""

    def test_single_match(self, manager, linker):
        manager.upsert_entity(_entity("u1", "萧炎"))
        linker.register_alias("u1", "炎帝")
        assert linker.lookup_alias("炎帝") == "u1"

    def test_missing_alias_returns_none(self, linker):
        assert linker.lookup_alias("不存在的名字") is None

    def test_type_filter_picks_right_one(self, manager, linker):
        """跨类型同名是设计支持的，按类型过滤应当明确。"""
        manager.upsert_entity(_entity("place", "天云宗", "地点"))
        manager.upsert_entity(_entity("faction", "天云宗", "势力"))
        linker.register_alias("place", "天云宗", "地点")
        linker.register_alias("faction", "天云宗", "势力")

        assert linker.lookup_alias("天云宗", entity_type="地点") == "place"
        assert linker.lookup_alias("天云宗", entity_type="势力") == "faction"


class TestSameTypeAmbiguityIsSurfaced:
    """同类型多条命中必须能被调用方看见，而不是静默挑一个。"""

    def _setup_two_roles(self, manager, linker):
        manager.upsert_entity(_entity("role_a", "萧炎甲"))
        manager.upsert_entity(_entity("role_b", "萧炎乙"))
        linker.register_alias("role_a", "炎帝", "角色")
        linker.register_alias("role_b", "炎帝", "角色")
        return "role_a", "role_b"

    def test_lookup_all_reports_both(self, manager, linker):
        self._setup_two_roles(manager, linker)
        hits = linker.lookup_alias_all("炎帝")
        ids = {h["id"] for h in hits}
        assert ids == {"role_a", "role_b"}

    def test_lookup_alias_flags_ambiguity(self, manager, linker):
        """核心断言：同类型歧义时，调用方能知道「这里有歧义」。

        期望行为（二选一，任一成立即可）：
        - 返回 None（拒绝猜测，交给上层走人工/消歧流程），或
        - 提供可查询的歧义标记（如 `lookup_alias_detailed()` 返回 candidates）

        不可接受的是：静默返回其中一个 ID 且没有任何信号。
        """
        self._setup_two_roles(manager, linker)

        has_detailed = hasattr(linker, "lookup_alias_detailed")
        if has_detailed:
            detail = linker.lookup_alias_detailed("炎帝", entity_type="角色")
            assert detail.get("ambiguous") is True, (
                f"同类型两个候选，detailed 查询却未标注歧义：{detail}"
            )
            assert len(detail.get("candidates") or []) == 2
        else:
            result = linker.lookup_alias("炎帝", entity_type="角色")
            assert result is None, (
                "同类型别名命中 2 个实体，lookup_alias 仍静默返回一个 ID"
                f"（{result!r}）——用户从未被告知存在歧义。"
                "要么返回 None 交上层消歧，要么提供带 ambiguous 标记的查询接口。"
            )

    def test_ambiguity_is_deterministic_or_refused(self, manager, linker):
        """无论走哪条路径，多次调用结果必须一致（不能依赖 SQL 返回顺序）。"""
        self._setup_two_roles(manager, linker)
        results = {linker.lookup_alias("炎帝", entity_type="角色") for _ in range(5)}
        assert len(results) == 1, f"反复调用结果不一致: {results}"


class TestAliasResolutionIsOrdered:
    """`get_entities_by_alias` 应给出确定的顺序，让上层可复现。"""

    def test_stable_across_calls(self, manager):
        for eid in ("c_third", "a_first", "b_second"):
            manager.upsert_entity(_entity(eid, eid))
            manager.register_alias("同名", eid, "角色")

        orders = {tuple(h["id"] for h in manager.get_entities_by_alias("同名"))
                  for _ in range(5)}
        assert len(orders) == 1, f"同一查询返回不同顺序: {orders}"

    def test_explicitly_sorted(self, manager):
        """顺序必须是代码保证的（ORDER BY），不是 SQLite 的默认行为。"""
        src = (SCRIPTS / "data_modules" / "index_entity_mixin.py").read_text(encoding="utf-8")
        # 找到 get_entities_by_alias 的实现体
        start = src.index("def get_entities_by_alias")
        body = src[start:start + 2000]
        assert "ORDER BY" in body, (
            "get_entities_by_alias 没有 ORDER BY——返回顺序依赖 SQLite 的"
            "默认行为（当前恰好按主键序）。上层 lookup_alias 取 entries[0]，"
            "排序一旦变了就会静默换实体。"
        )
