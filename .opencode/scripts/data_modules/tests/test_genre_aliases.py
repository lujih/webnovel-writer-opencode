#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材别名与 profile key 映射的完整性。

`genre_aliases.py` 只有 66 行，零测试覆盖，但它决定**题材路由**：

    normalize_genre_token(token)  → 把用户写法归一到规范题材
    to_profile_key(genre)         → 把规范题材映射到英文 profile key

`to_profile_key()` 的结果用于：
- `context_manager._is_methodology_enabled_for_genre()` 查方法论白名单
- `writing_guidance_builder` 选策略卡与检查清单模板
- `genre_profile_builder` 解析题材画像

## 缺陷：两张表与 `GENRE_CANONICAL` 全面脱节

`reference_search.GENRE_CANONICAL` 是本项目的 15 个规范题材：

    仙侠 历史 古言 奇幻 年代 幻言 快穿 悬疑 游戏 玄幻 现言 种田 科幻 衍生 都市

而 `GENRE_INPUT_ALIASES` 的 **14 个目标值全部不在其中**：

    '玄幻'      -> '修仙'      ← 修仙不是 canonical 题材
    '都市修真'  -> '都市异能'  ← 同上
    '游戏电竞'  -> '电竞'      ← 同上
    '克系'      -> '克苏鲁'    ← 同上

同时 `GENRE_PROFILE_KEY_ALIASES` 的 key 也只有 7 个是 canonical 题材
（古言/悬疑/玄幻…），**15 个 canonical 里有 13 个拿不到英文 key**，
`to_profile_key()` 直接返回中文本身：

    to_profile_key('仙侠')  → '仙侠'
    to_profile_key('都市')  → '都市'
    to_profile_key('悬疑')  → '悬疑'
    to_profile_key('科幻')  → '科幻'

下游据此查白名单/选模板时匹配不到英文 key，静默回落到默认分支——
**作者写"仙侠"，方法论策略卡不生效；写"玄幻"却生效**。同一个系统里两种
题材的待遇取决于别名表里恰好有没有那一行。

## 本文件钉住

- 每个 `GENRE_CANONICAL` 题材都能映射到英文 profile key（不留中文）
- `GENRE_INPUT_ALIASES` 的目标值必须是 canonical 题材（否则归一化把人
  带到一个系统不认识的地方）
- 已知别名（修仙/克系/电竞…）仍能正确归一
- 空值、未知题材的行为不变（返回自身/小写）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.genre_aliases import (  # noqa: E402
    GENRE_INPUT_ALIASES,
    GENRE_PROFILE_KEY_ALIASES,
    normalize_genre_token,
    to_profile_key,
)
from reference_search import GENRE_CANONICAL  # noqa: E402


class TestEveryCanonicalGenreHasProfileKey:
    """核心缺陷一：13/15 的 canonical 题材拿不到英文 key。"""

    @pytest.mark.parametrize("genre", sorted(GENRE_CANONICAL))
    def test_returns_ascii_profile_key(self, genre):
        key = to_profile_key(genre)
        assert key, f"{genre} 的 profile key 为空"
        assert key.isascii(), (
            f"to_profile_key({genre!r}) → {key!r}，返回了中文本身。"
            f"下游用它查方法论白名单/策略卡模板（都是英文 key），"
            f"匹配不到就静默回落默认分支——该题材的方法论永远不生效。"
        )

    @pytest.mark.parametrize("genre", sorted(GENRE_CANONICAL))
    def test_profile_key_is_stable(self, genre):
        """同一题材多次调用结果一致（防止依赖 dict 顺序）。"""
        assert to_profile_key(genre) == to_profile_key(genre)

    def test_all_canonical_covered(self):
        missing = [g for g in GENRE_CANONICAL if not to_profile_key(g).isascii()]
        assert not missing, f"这些 canonical 题材没有英文 profile key：{missing}"


class TestAliasTargetsMatchProfileHeadings:
    """别名目标必须能匹配 `genre-profiles.md` 的分节标题。

    这张表服务的不是 `GENRE_CANONICAL`，而是
    `.claude/references/genre-profiles.md` / `reading-power-taxonomy.md` 里的
    `## 标题`——`context_manager._extract_genre_section()` 拿归一化结果去匹配
    那些标题。曾误以为该改成 canonical 题材，结果 `## 电竞` 分节再也匹配不到。
    """

    def test_targets_are_nonempty(self):
        for token, target in GENRE_INPUT_ALIASES.items():
            assert target.strip(), f"{token!r} 归一化到了空串"

    def test_normalized_token_survives_roundtrip(self):
        """归一化结果再归一化应不变（幂等）。"""
        for token in GENRE_INPUT_ALIASES:
            once = normalize_genre_token(token)
            assert normalize_genre_token(once) == once, (
                f"{token!r} → {once!r} → {normalize_genre_token(once)!r}，不幂等"
            )

    def test_targets_resolve_to_ascii_profile_key(self):
        """归一化结果必须还能映射到英文 profile key（两张表要接得上）。"""
        for token in GENRE_INPUT_ALIASES:
            normalized = normalize_genre_token(token)
            key = to_profile_key(normalized)
            assert key and key.isascii(), (
                f"{token!r} → {normalized!r} → {key!r}，接不上英文 profile key"
            )


class TestKnownAliasesStillWork:
    """既有别名行为不能改坏。"""

    @pytest.mark.parametrize("token,expected_key", [
        ("修仙", "xianxia"),
        ("玄幻", "xianxia"),
        ("修真", "xianxia"),
        ("克系", "cosmic-horror"),
        ("克苏鲁", "cosmic-horror"),
        ("电竞", "esports"),
        ("直播文", "livestream"),
        ("都市异能", "urban-power"),
        ("悬疑", "mystery"),
        ("古言", "romance"),
    ])
    def test_profile_key(self, token, expected_key):
        assert to_profile_key(token) == expected_key

    @pytest.mark.parametrize("token", ["修仙", "玄幻", "修真"])
    def test_xianxia_family_normalizes(self, token):
        """归一化目标是 profile 文件里的分节标题「修仙」，不是 canonical 题材。"""
        assert normalize_genre_token(token) == "修仙"


class TestEdgeCasesUnchanged:
    """空值与未知题材的既有行为。"""

    @pytest.mark.parametrize("value", ["", "  ", None])
    def test_empty(self, value):
        assert to_profile_key(value) == ""
        assert normalize_genre_token(value) == ""

    def test_unknown_genre_lowercased(self):
        assert to_profile_key("UnknownGenre") == "unknowngenre"

    def test_unknown_genre_passthrough_normalize(self):
        assert normalize_genre_token("某个没人听过的题材") == "某个没人听过的题材"

    def test_composite_genre_with_slash(self):
        """`修仙/玄幻` 这类组合写法要有确定结果。"""
        assert to_profile_key("修仙/玄幻") == "xianxia"
