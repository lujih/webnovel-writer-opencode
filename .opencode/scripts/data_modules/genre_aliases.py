#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""题材别名归一与 profile key 映射。

两张表的分工：

- `GENRE_INPUT_ALIASES`：用户/模型的各种写法 → **规范题材**
  （目标必须落在 `reference_search.GENRE_CANONICAL` 里，否则归一化会把人带到
  系统不认识的名字上，后续路由与模板查找全部落空）
- `GENRE_PROFILE_KEY_ALIASES`：规范题材（及少量历史写法）→ **英文 profile key**
  （下游用它查方法论白名单、策略卡模板、检查清单模板，都是英文 key）

两张表必须覆盖 `GENRE_CANONICAL` 的全部 15 个题材——否则同一个系统里
"写玄幻"与"写仙侠"的待遇会不同：前者命中策略卡，后者静默回落默认分支。
"""

from __future__ import annotations


#: 用户/模型写法 → **题材画像文件里的分节标题**。
#:
#: ⚠️ 这张表的目标值**不是** `GENRE_CANONICAL`，而是
#: `.claude/references/genre-profiles.md` / `reading-power-taxonomy.md` 里的
#: `## 标题`。`context_manager._extract_genre_section()` 拿归一化结果去匹配那些
#: 标题，所以目标必须与文件里的标题逐字一致（`电竞`、`直播文`、`克苏鲁`）。
#:
#: 曾试图把它改成 canonical 题材（`电竞 -> 游戏`），结果 `## 电竞` 分节再也
#: 匹配不到——`test_context_manager_genre_aliases_normalized_for_profile_lookup`
#: 立刻失败。要动这张表，必须先同步改 profile 文件的标题。
GENRE_INPUT_ALIASES: dict[str, str] = {
    "修仙/玄幻": "修仙",
    "玄幻修仙": "修仙",
    "玄幻": "修仙",
    "修真": "修仙",
    "都市修真": "都市异能",
    "都市高武": "高武",
    "都市奇闻": "都市脑洞",
    "古言脑洞": "古言",
    "游戏电竞": "电竞",
    "电竞文": "电竞",
    "直播": "直播文",
    "直播带货": "直播文",
    "主播": "直播文",
    "克系": "克苏鲁",
    "克系悬疑": "克苏鲁",
}


#: 规范题材（及历史写法）→ 英文 profile key。
#:
#: 下游消费方：`context_manager._is_methodology_enabled_for_genre`（白名单）、
#: `writing_guidance_builder`（策略卡 + 检查清单模板）、
#: `genre_profile_builder`（题材画像）。它们都按英文 key 查找。
GENRE_PROFILE_KEY_ALIASES: dict[str, str] = {
    # 15 个规范题材，全覆盖
    "玄幻": "xianxia",
    "仙侠": "xianxia",
    "奇幻": "xianxia",
    "都市": "urban-power",
    "现言": "romance",
    "古言": "romance",
    "幻言": "romance",
    "悬疑": "mystery",
    "历史": "history",
    "科幻": "scifi",
    "年代": "period",
    "种田": "slice-of-life",
    "快穿": "quick-transmigration",
    "游戏": "esports",
    "衍生": "derivative",
    # 历史写法（用户/旧数据可能仍在用）
    "修仙": "xianxia",
    "修仙/玄幻": "xianxia",
    "修真": "xianxia",
    "西幻": "xianxia",
    "高武": "xianxia",
    "爽文/系统流": "shuangwen",
    "系统流": "shuangwen",
    "都市异能": "urban-power",
    "都市脑洞": "urban-power",
    "都市日常": "urban-power",
    "都市高武": "urban-power",
    "狗血言情": "romance",
    "青春甜宠": "romance",
    "替身文": "substitute",
    "规则怪谈": "rules-mystery",
    "悬疑脑洞": "mystery",
    "悬疑灵异": "mystery",
    "克系": "cosmic-horror",
    "克苏鲁": "cosmic-horror",
    "知乎短篇": "zhihu-short",
    "电竞": "esports",
    "电竞文": "esports",
    "直播文": "livestream",
    "直播": "livestream",
}


def normalize_genre_token(token: str) -> str:
    value = str(token or "").strip()
    if not value:
        return ""
    return GENRE_INPUT_ALIASES.get(value, value)


def to_profile_key(genre: str) -> str:
    """规范题材 → 英文 profile key。

    顺序：先查别名表（含历史写法），再归一化一次，最后查 profile 表。
    都没有则返回小写原文（未知题材的既有行为）。
    """
    value = str(genre or "").strip()
    if not value:
        return ""
    # 先直接查：`GENRE_PROFILE_KEY_ALIASES` 里也收了历史写法（如 `都市异能`），
    # 这些不一定在 `GENRE_INPUT_ALIASES` 里。
    direct = GENRE_PROFILE_KEY_ALIASES.get(value)
    if direct:
        return direct
    normalized = normalize_genre_token(value)
    direct = GENRE_PROFILE_KEY_ALIASES.get(normalized)
    if direct:
        return direct
    return normalized.lower()
