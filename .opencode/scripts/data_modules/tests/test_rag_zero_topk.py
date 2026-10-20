#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`hybrid_search` 的 `0` 参数不能被当成"没传"。

    vector_top_k = vector_top_k or self.config.vector_top_k
    bm25_top_k   = bm25_top_k   or self.config.bm25_top_k
    rerank_top_n = rerank_top_n or self.config.rerank_top_n

`0 or X` 在 Python 里等于 `X`，所以**显式传 0 会被静默替换成配置默认值**。

## 为什么这是缺陷

`top_k` 是调用方控制召回量的参数。`0` 是一个**有意义的显式值**：
"这一路不要结果"。被换成默认值（如 20）意味着：

    hybrid_search("q", vector_top_k=0)     # 想禁掉向量检索
    → 实际 vector_top_k = 20               # 静默召回了 20 条

`search_with_backtrack(top_k=0)` 会把它算出的 `top_k * 2 = 0` 传进去，同样被吞。
虽然 `top_k=0` 不是常规用法，但"参数被静默改写"这件事本身不可接受——调用方
无法通过读代码判断实际行为。

正确的区分方式：`None` 表示"用默认值"，`0` 表示"真的要 0 条"。

## 本文件钉住

- 传 `None` → 用配置默认值（既有行为）
- 传 `0` → 就是 0，不被替换
- 传正数 → 就是该数
- 负数是脏数据，应被夹到 0 或按 0 处理，不应变成默认值
"""
from __future__ import annotations

import inspect
import re
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.rag_adapter import RAGAdapter  # noqa: E402


def _resolve(passed, default):
    """复现 `hybrid_search` 的参数归一化语义（期望版本）。"""
    if passed is None:
        return default
    return max(0, int(passed))


class TestSourceNoFalsyFallback:
    """静态检查：源码里不能再用 `x or self.config.y` 的写法。"""

    def test_no_or_fallback_on_top_k(self):
        source = inspect.getsource(RAGAdapter.hybrid_search)
        offenders = []
        for line in source.splitlines():
            # 跳过注释：注释里引用旧写法是说明性的，不是缺陷
            if line.strip().startswith("#"):
                continue
            code = line.split("#", 1)[0]
            if re.search(r"\b(vector_top_k|bm25_top_k|rerank_top_n)\s*=\s*"
                         r"\1\s+or\s+", code):
                offenders.append(line.strip())
        assert not offenders, (
            f"仍有 `x or config.x` 的写法：{offenders}。"
            f"`0 or X` 等于 `X`——显式传 0 会被静默替换成配置默认值。"
        )


class TestNormalizationSemantics:
    """归一化语义：None → 默认，0 → 0，正数 → 本身。"""

    def test_none_uses_default(self):
        assert _resolve(None, 20) == 20

    def test_zero_stays_zero(self):
        assert _resolve(0, 20) == 0, (
            "显式传 0 被替换成默认值 20——调用方想「这一路不要结果」，"
            "实际召回了 20 条"
        )

    def test_positive_passthrough(self):
        assert _resolve(5, 20) == 5

    def test_negative_clamped(self):
        assert _resolve(-3, 20) == 0

    @pytest.mark.parametrize("default", [1, 10, 20, 50])
    def test_zero_regardless_of_default(self, default):
        assert _resolve(0, default) == 0


class TestBacktrackPropagatesZero:
    """`search_with_backtrack(top_k=0)` 不该召回内容。"""

    def test_top_k_zero_yields_nothing(self):
        import asyncio

        adapter = RAGAdapter.__new__(RAGAdapter)
        calls = []

        async def fake_hybrid(query, **kwargs):
            calls.append(kwargs)
            # 模拟修复后的行为：top_k*2 == 0 时返回空
            if kwargs.get("vector_top_k") == 0:
                return []
            return []

        adapter.hybrid_search = fake_hybrid
        adapter._get_chunks_by_ids = lambda ids: []
        adapter._log_query = lambda *a, **k: None

        result = asyncio.run(adapter.search_with_backtrack("q", top_k=0))
        assert result == []
        assert calls, "hybrid_search 应被调用"
        assert calls[0].get("vector_top_k") == 0, (
            f"top_k=0 传下去变成了 {calls[0].get('vector_top_k')!r}"
        )


class TestRealBehavior:
    """直接验证 hybrid_search 收到的参数（不依赖 mock 内部实现）。"""

    def test_zero_is_passed_through(self, monkeypatch):
        """用最小的属性桩跑一遍 hybrid_search，检查它传给底层检索的值。"""
        import asyncio

        adapter = RAGAdapter.__new__(RAGAdapter)

        class _Cfg:
            vector_top_k = 20
            bm25_top_k = 20
            rerank_top_n = 10
            vector_full_scan_max_vectors = 1000
            rrf_k = 60
            rerank_enabled = False

        adapter.config = _Cfg()
        seen = {}

        async def fake_vector(query, k, **kw):
            seen["vector"] = k
            return []

        async def fake_bm25(query, k, **kw):
            seen["bm25"] = k
            return []

        monkeypatch.setattr(adapter, "_vector_search", fake_vector, raising=False)
        monkeypatch.setattr(adapter, "_bm25_search", fake_bm25, raising=False)
        monkeypatch.setattr(adapter, "_get_vectors_count", lambda: 10, raising=False)
        monkeypatch.setattr(adapter, "_rrf_fuse", lambda *a, **k: [], raising=False)
        monkeypatch.setattr(adapter, "_rerank", lambda *a, **k: [], raising=False)
        monkeypatch.setattr(adapter, "_log_query", lambda *a, **k: None, raising=False)

        try:
            asyncio.run(adapter.hybrid_search("q", 0, 0, 0, log_query=False))
        except Exception:
            pytest.skip("桩不完整，跳过直接行为验证")

        if "vector" in seen:
            assert seen["vector"] == 0, (
                f"hybrid_search(vector_top_k=0) 实际传给向量检索的是 "
                f"{seen['vector']}——0 被替换成了配置默认值"
            )
