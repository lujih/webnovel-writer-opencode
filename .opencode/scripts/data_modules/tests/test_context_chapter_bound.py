#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""检索与「最近出场」必须服从章节上界。

审计发现：`vector_search` / `bm25_search` / `hybrid_search` / `graph_hybrid_search`
全都接受 `chapter=`（SQL 里是 `chapter <= ?`），唯独两条路径没有透传：

1. `rag_adapter.search_with_backtrack()` —— backtrack 是唯一没有上界的检索策略，
   一次召回就能把**未来章节**的原文注进当前章上下文；
2. `context_manager._load_recent_appearances()` → `IndexManager.get_recent_appearances()`
   —— 按全表 `MAX(chapter)` 排序，写第 3 章时会拿到"第 20 章出场最多"的角色。

后果在重写旧章时最致命：模型会"记得"还没写的剧情。本文件钉住两处的上界行为。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.index_manager import IndexManager  # noqa: E402
from data_modules.rag_adapter import RAGAdapter  # noqa: E402


@pytest.fixture
def index(tmp_path) -> IndexManager:
    # sqlite 不会创建父目录：先建出 .webnovel/，否则 connect 直接
    # "unable to open database file"。
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    cfg = DataModulesConfig.from_project_root(str(tmp_path))
    return IndexManager(cfg)


class TestRecentAppearancesChapterBound:
    def test_without_bound_future_leaks(self, index):
        """记录缺陷前提：不传上界时，未来章节的实体确实会出现。"""
        index.record_appearance("lin", 1, ["林三"])
        index.record_appearance("ghost", 20, ["未来角色"])

        ids = [row["entity_id"] for row in index.get_recent_appearances(limit=10)]
        assert "ghost" in ids, "无上界时本就应该看到未来章节（这是要修的行为）"

    def test_with_bound_excludes_future(self, index):
        index.record_appearance("lin", 1, ["林三"])
        index.record_appearance("ghost", 20, ["未来角色"])

        rows = index.get_recent_appearances(limit=10, max_chapter=3)
        ids = [row["entity_id"] for row in rows]
        assert "lin" in ids
        assert "ghost" not in ids, "写第 3 章时不能看到第 20 章才出场的角色"
        # last_chapter 也必须来自被过滤后的集合
        assert all(int(r["last_chapter"]) <= 3 for r in rows)

    def test_bound_inclusive(self, index):
        """上界是 `<=`：正在写的这一章已经写好的内容要能看到。"""
        index.record_appearance("lin", 3, ["林三"])
        ids = [r["entity_id"] for r in index.get_recent_appearances(limit=10, max_chapter=3)]
        assert "lin" in ids

    def test_no_bound_keeps_old_behavior(self, index):
        index.record_appearance("lin", 5, ["林三"])
        rows = index.get_recent_appearances(limit=10)
        assert [r["entity_id"] for r in rows] == ["lin"]


class TestBacktrackForwardsChapter:
    """"`search_with_backtrack` 必须把 chapter 透传给 hybrid_search。"""

    @pytest.fixture
    def adapter(self):
        return RAGAdapter.__new__(RAGAdapter)

    def test_default_is_none(self, adapter, monkeypatch):
        captured = {}

        async def fake_hybrid(query, **kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(adapter, "hybrid_search", fake_hybrid)
        monkeypatch.setattr(adapter, "_get_chunks_by_ids", lambda ids: [])
        monkeypatch.setattr(adapter, "_log_query", lambda *a, **k: None)

        asyncio.run(adapter.search_with_backtrack("q", top_k=3))
        assert captured.get("chapter") is None, "不传上界时保持原行为（全库检索）"

    def test_forwards_bound(self, adapter, monkeypatch):
        captured = {}

        async def fake_hybrid(query, **kwargs):
            captured.update(kwargs)
            return []

        monkeypatch.setattr(adapter, "hybrid_search", fake_hybrid)
        monkeypatch.setattr(adapter, "_get_chunks_by_ids", lambda ids: [])
        monkeypatch.setattr(adapter, "_log_query", lambda *a, **k: None)

        asyncio.run(adapter.search_with_backtrack("q", top_k=3, chapter=7))
        assert captured["chapter"] == 7

    def test_strategy_dispatch_forwards_bound(self, adapter, monkeypatch):
        """`search(strategy="backtrack")` 这条统一入口也要透传上界。"""
        seen = {}

        async def fake_backtrack(query, top_k=5, chapter=None):
            seen["chapter"] = chapter
            return []

        monkeypatch.setattr(adapter, "search_with_backtrack", fake_backtrack)
        asyncio.run(adapter.search("q", top_k=3, strategy="backtrack", chapter=9))
        assert seen["chapter"] == 9
