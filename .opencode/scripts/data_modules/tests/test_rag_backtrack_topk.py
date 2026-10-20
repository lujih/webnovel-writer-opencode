#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`search_with_backtrack` 的 `top_k` 语义必须被遵守。

`rag_adapter._merge_results()` 把 parent 与 child 合并：

    for child in children:
        parent_id = child.parent_chunk_id
        if parent_id and parent_id in parent_map and parent_id not in seen:
            merged.append(parent_map[parent_id])     # ← parent
            seen.add(parent_id)
        merged.append(child)                          # ← child

每个 child 若命中 parent，就追加**两条**。而 `search_with_backtrack()`：

    child_results = await self.hybrid_search(..., rerank_top_n=top_k, ...)
    parents = self._get_chunks_by_ids(parent_ids)
    merged = self._merge_results(parents, child_results[:top_k])
    return merged

只截断了 `child_results`，没有截断 `merged`。实测：

    top_k=5，5 个 child 各带 parent
    → 输出 10 条  ['p0','c0','p1','c1','p2','c2','p3','c3','p4','c4']

## 为什么这是缺陷

`top_k` 是**调用方用来控制上下文体量**的参数。`context_manager` 等地方按
返回条数估算 token 预算；返回 2 倍会让：
- 注入的上下文超出预期，挤掉真正需要的内容
- token 预算计算失准（按 `top_k` 算，实际吃 2 倍）

而且这个 2 倍**只在有 parent 时发生**——同一本书在不同章节上行为不一致，
更难排查。

## 本文件钉住

- `search_with_backtrack` 返回条数 ≤ `top_k`
- parent 仍要被带出来（那是 backtrack 的目的：给 child 补足上下文）
- 去重（同一 parent 不重复出现）
- 无 parent 时行为不变
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.rag_adapter import RAGAdapter, SearchResult  # noqa: E402


def _mk(chunk_id: str, parent: str | None = None, score: float = 1.0) -> SearchResult:
    return SearchResult(
        chunk_id=chunk_id, chapter=1, scene_index=0, content=f"内容-{chunk_id}",
        score=score, source="hybrid", parent_chunk_id=parent, chunk_type="scene",
    )


@pytest.fixture
def adapter() -> RAGAdapter:
    return RAGAdapter.__new__(RAGAdapter)


class TestMergeResultsUnchanged:
    """`_merge_results` 本身的合并语义（parent 补在 child 前）。"""

    def test_parent_precedes_child(self, adapter):
        parents = [_mk("p0")]
        children = [_mk("c0", parent="p0")]
        merged = adapter._merge_results(parents, children)
        assert [r.chunk_id for r in merged] == ["p0", "c0"]

    def test_dedup_parent(self, adapter):
        """两个 child 共用一个 parent 时，parent 只出现一次。"""
        parents = [_mk("p0")]
        children = [_mk("c0", parent="p0"), _mk("c1", parent="p0")]
        merged = adapter._merge_results(parents, children)
        assert [r.chunk_id for r in merged].count("p0") == 1

    def test_no_parent(self, adapter):
        merged = adapter._merge_results([], [_mk("c0"), _mk("c1")])
        assert [r.chunk_id for r in merged] == ["c0", "c1"]

    def test_unknown_parent_skipped(self, adapter):
        """child 指向的 parent 没被取回来时，只保留 child。"""
        merged = adapter._merge_results([], [_mk("c0", parent="missing")])
        assert [r.chunk_id for r in merged] == ["c0"]


class TestTopKIsRespected:
    """核心缺陷：返回条数不能超过 top_k。"""

    def test_merge_alone_doubles(self, adapter):
        """记录前提：_merge_results 本身确实会产出 2 倍。"""
        parents = [_mk(f"p{i}") for i in range(5)]
        children = [_mk(f"c{i}", parent=f"p{i}") for i in range(5)]
        assert len(adapter._merge_results(parents, children)) == 10

    def test_search_with_backtrack_truncates(self):
        """`search_with_backtrack` 必须把结果截到 top_k。"""
        import asyncio

        adapter = RAGAdapter.__new__(RAGAdapter)

        async def fake_hybrid(query, **kwargs):
            return [_mk(f"c{i}", parent=f"p{i}") for i in range(5)]

        adapter.hybrid_search = fake_hybrid
        adapter._get_chunks_by_ids = lambda ids: [_mk(i) for i in ids]
        adapter._log_query = lambda *a, **k: None

        results = asyncio.run(adapter.search_with_backtrack("查询", top_k=5))
        assert len(results) <= 5, (
            f"top_k=5 却返回 {len(results)} 条："
            f"{[r.chunk_id for r in results]}。"
            f"top_k 是调用方控制上下文体量的参数，返回 2 倍会让 token 预算失准。"
        )

    def test_search_with_backtrack_keeps_parents(self):
        """截断后仍要带出 parent（那是 backtrack 的目的）。"""
        import asyncio

        adapter = RAGAdapter.__new__(RAGAdapter)

        async def fake_hybrid(query, **kwargs):
            return [_mk(f"c{i}", parent=f"p{i}") for i in range(5)]

        adapter.hybrid_search = fake_hybrid
        adapter._get_chunks_by_ids = lambda ids: [_mk(i) for i in ids]
        adapter._log_query = lambda *a, **k: None

        results = asyncio.run(adapter.search_with_backtrack("查询", top_k=5))
        ids = [r.chunk_id for r in results]
        assert any(i.startswith("p") for i in ids), (
            f"截断后 parent 全丢了：{ids}。backtrack 的意义就是补足 parent 上下文。"
        )

    @pytest.mark.parametrize("top_k", [1, 2, 3, 5, 10])
    def test_various_top_k(self, top_k):
        import asyncio

        adapter = RAGAdapter.__new__(RAGAdapter)

        async def fake_hybrid(query, **kwargs):
            return [_mk(f"c{i}", parent=f"p{i}") for i in range(top_k)]

        adapter.hybrid_search = fake_hybrid
        adapter._get_chunks_by_ids = lambda ids: [_mk(i) for i in ids]
        adapter._log_query = lambda *a, **k: None

        results = asyncio.run(adapter.search_with_backtrack("q", top_k=top_k))
        assert len(results) <= top_k, f"top_k={top_k} 返回了 {len(results)} 条"

    def test_empty_results(self):
        import asyncio

        adapter = RAGAdapter.__new__(RAGAdapter)

        async def fake_hybrid(query, **kwargs):
            return []

        adapter.hybrid_search = fake_hybrid
        adapter._get_chunks_by_ids = lambda ids: []
        adapter._log_query = lambda *a, **k: None

        assert asyncio.run(adapter.search_with_backtrack("q", top_k=5)) == []

    def test_no_duplicates_in_output(self):
        import asyncio

        adapter = RAGAdapter.__new__(RAGAdapter)

        async def fake_hybrid(query, **kwargs):
            # 多个 child 共用同一个 parent
            return [_mk("c0", parent="p0"), _mk("c1", parent="p0"),
                    _mk("c2", parent="p0")]

        adapter.hybrid_search = fake_hybrid
        adapter._get_chunks_by_ids = lambda ids: [_mk(i) for i in ids]
        adapter._log_query = lambda *a, **k: None

        results = asyncio.run(adapter.search_with_backtrack("q", top_k=5))
        ids = [r.chunk_id for r in results]
        assert len(ids) == len(set(ids)), f"输出有重复：{ids}"
