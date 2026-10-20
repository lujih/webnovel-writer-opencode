#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""七猫发布适配器的题材匹配。

`publisher/adapters/qimao.py`（238 行、零测试覆盖）负责把书发布到七猫平台。
其中 `_find_category_id()` 决定**书被归到哪个分类**：

    def _find_category_id(self, categories, genre):
        for cat in categories:                       # 1. 精确匹配
            if name == genre: return int(cat["category_id"])
        for cat in categories:                       # 2. 双向子串
            if genre in name or name in genre: return int(cat["category_id"])
        return int(categories[0]["category_id"]) if categories else 0   # 3. 静默回落

## 缺陷：匹配失败时静默取第一个分类

实测（categories = [玄幻(1), 都市(2)]）：

    genre='玄幻'          → 1 (玄幻)  ✅
    genre='都市'          → 2 (都市)  ✅
    genre='科幻'          → 1 (玄幻)  ⚠️
    genre='完全不存在的题材' → 1 (玄幻)  ⚠️
    genre=''             → 1 (玄幻)  ⚠️

**作者写「科幻」，书会被发布到「玄幻」分类下**，且不报错、不提示。
七猫的分类决定推荐池与读者群，归错类的后果不是"发不出去"，而是
"发出去了但没人看"——比直接失败更难发现。

## 本文件钉住

- 精确匹配与子串匹配的既有行为不变
- **匹配失败时不能静默回落到第一个分类**：要么返回 0（表示"未匹配"），
  要么给出可被调用方识别的信号
- 空列表返回 0
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from publisher.adapters.qimao import QimaoAdapter  # noqa: E402


@pytest.fixture
def adapter() -> QimaoAdapter:
    return QimaoAdapter.__new__(QimaoAdapter)


CATS = [
    {"name": "玄幻", "category_id": 1},
    {"name": "都市", "category_id": 2},
    {"name": "科幻", "category_id": 3},
]


class TestExactMatchUnchanged:
    """精确匹配优先。"""

    @pytest.mark.parametrize("genre,expected", [
        ("玄幻", 1), ("都市", 2), ("科幻", 3),
    ])
    def test_exact(self, adapter, genre, expected):
        assert adapter._find_category_id(CATS, genre) == expected

    def test_exact_beats_substring(self, adapter):
        """`玄幻` 与 `玄幻脑洞` 同时存在时，精确匹配应胜出。"""
        cats = [
            {"name": "玄幻脑洞", "category_id": 9},
            {"name": "玄幻", "category_id": 1},
        ]
        assert adapter._find_category_id(cats, "玄幻") == 1

    def test_category_name_fallback_key(self, adapter):
        cats = [{"category_name": "仙侠", "category_id": 7}]
        assert adapter._find_category_id(cats, "仙侠") == 7


class TestSubstringMatchUnchanged:
    """双向子串匹配的既有行为。"""

    def test_genre_inside_name(self, adapter):
        cats = [{"name": "东方玄幻", "category_id": 5}]
        assert adapter._find_category_id(cats, "玄幻") == 5

    def test_name_inside_genre(self, adapter):
        cats = [{"name": "玄幻", "category_id": 5}]
        assert adapter._find_category_id(cats, "玄幻小说") == 5


class TestNoSilentFallback:
    """核心缺陷：匹配不到时不能静默归到第一个分类。"""

    # 注意：`科幻末世` 不算"匹配失败"——子串匹配会把它归到「科幻」，那是合理的。
    # 真正的失败是题材与所有分类都无关。
    @pytest.mark.parametrize("genre", ["悬疑灵异", "完全不存在的题材", "", "   "])
    def test_does_not_pick_first_category(self, adapter, genre):
        result = adapter._find_category_id(CATS, genre)
        # 只有 genre 真的能匹配到 CATS 里的某一项才允许返回非 0
        assert result == 0, (
            f"_find_category_id({genre!r}) 返回了 {result}，"
            f"这是第一个分类「{CATS[0]['name']}」的 id。"
            f"作者写「{genre}」会被静默发布到「{CATS[0]['name']}」分类下——"
            f"不报错、不提示，但书进了错误的推荐池。"
        )

    def test_empty_categories_returns_zero(self, adapter):
        assert adapter._find_category_id([], "玄幻") == 0

    def test_matched_genre_still_returns_id(self, adapter):
        """真正匹配上时仍要正常返回。"""
        assert adapter._find_category_id(CATS, "科幻") == 3

    def test_compound_genre_matches_by_substring(self, adapter):
        """`科幻末世` 归到「科幻」是合理的子串命中，不算失败。"""
        assert adapter._find_category_id(CATS, "科幻末世") == 3


class TestLabelMatching:
    """标签匹配：同样不能静默回落到无关标签。"""

    LABELS = [
        {"name": "热血", "label_id": "l1"},
        {"name": "升级流", "label_id": "l2"},
        {"name": "都市", "label_id": "l3"},
    ]

    def test_matches_by_genre(self, adapter):
        ids = adapter._find_label_ids(self.LABELS, "都市")
        assert "l3" in ids

    def test_respects_max_count(self, adapter):
        labels = [{"name": "a", "label_id": f"l{i}"} for i in range(10)]
        assert len(adapter._find_label_ids(labels, "a", max_count=2)) <= 2

    def test_skips_incomplete_entries(self, adapter):
        labels = [{"name": "无id"}, {"label_id": "only_id"},
                  {"name": "都市", "label_id": "l3"}]
        ids = adapter._find_label_ids(labels, "都市")
        assert "l3" in ids
        assert all(ids), "不应产出空 id"

    def test_no_match_fallback_is_documented(self, adapter):
        """无匹配时的兜底行为：取前两个有效标签。

        这是既有行为（有兜底总比空标签好），但至少要保证：
        不产出空 id、不超过 max_count。
        """
        labels = [{"name": "甲", "label_id": "a"}, {"name": "乙", "label_id": "b"}]
        ids = adapter._find_label_ids(labels, "完全无关")
        assert ids == ["a", "b"]
        assert len(ids) <= 4
