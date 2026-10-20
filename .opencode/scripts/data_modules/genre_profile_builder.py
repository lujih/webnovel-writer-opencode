#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Genre profile parsing helpers for ContextManager.
"""

from __future__ import annotations

import re
from typing import List

from .genre_aliases import normalize_genre_token


def parse_genre_tokens(
    genre_raw: str,
    *,
    support_composite: bool,
    separators: tuple[str, ...],
) -> List[str]:
    text = str(genre_raw or "").strip()
    if not text:
        return []

    if not support_composite:
        normalized_single = normalize_genre_token(text)
        return [normalized_single] if normalized_single else [text]

    pattern = "|".join(re.escape(str(token)) for token in separators if str(token))
    if not pattern:
        normalized_single = normalize_genre_token(text)
        return [normalized_single] if normalized_single else [text]

    tokens = [chunk.strip() for chunk in re.split(pattern, text) if chunk and chunk.strip()]
    deduped: List[str] = []
    seen = set()
    for token in tokens:
        normalized_token = normalize_genre_token(token)
        if not normalized_token:
            continue
        lower = normalized_token.lower()
        if lower in seen:
            continue
        seen.add(lower)
        deduped.append(normalized_token)
    if deduped:
        return deduped

    fallback_token = normalize_genre_token(text)
    return [fallback_token] if fallback_token else [text]


#: 分节标题里"标题本体"与"补充说明"之间的分隔符。
#:
#: `## 都市（含异能）` 这类写法很常见：标题本体是题材名，括号里是说明。
#: 匹配时只看本体，否则 `都市` 永远匹配不到带括号的标题。
_HEADING_NOTE_SEPARATORS = ("（", "(", "：", ":", "—", "–", "-", " ", "\t")


def _heading_genre_title(line: str) -> str:
    """从一行标题里取出"题材名"部分（去掉 `##`、补充说明、空白）。"""
    stripped = line.strip()
    for prefix in ("###", "##", "#"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break
    title = stripped.strip()
    # 按第一个分隔符截断：`都市（含异能）` → `都市`
    cut = len(title)
    for sep in _HEADING_NOTE_SEPARATORS:
        idx = title.find(sep)
        if idx != -1:
            cut = min(cut, idx)
    return title[:cut].strip().lower()


def extract_genre_section(text: str, genre: str) -> str:
    """摘出与 ``genre`` 对应的分节。

    匹配的是**标题本体**，不是子串包含。此前用 `target in normalized`：

        active = target in normalized      # normalized 是 "## 都市异能"

    题材名互为前缀时，谁在前就匹配谁。实测：

        ## 都市异能      ← 排在前
        - 异能规则
        ## 都市
        - 都市规则

        extract_genre_section(doc, "都市")
        → "## 都市异能\n- 异能规则"    ❌ 拿到「都市异能」的规则

    作者写「都市」，上下文里注入的却是「都市异能」的写作要求，不报错、无提示。
    同类前缀对：都市/都市异能、游戏/游戏电竞、悬疑/悬疑脑洞、修仙/修仙玄幻。
    """
    if not text:
        return ""
    lines = text.splitlines()
    capture: List[str] = []
    active = False
    target = genre.strip().lower()

    for line in lines:
        normalized = line.strip()
        if normalized.startswith("#"):
            if active:
                break
            # 精确比较标题本体，避免前缀碰撞
            active = bool(target) and _heading_genre_title(normalized) == target
            if active:
                capture.append(line)
            continue
        if active:
            capture.append(line)

    if capture:
        return "\n".join(capture).strip()

    return "\n".join(lines[:80]).strip()


def extract_markdown_refs(text: str, max_items: int = 8) -> List[str]:
    if not text:
        return []
    refs: List[str] = []
    for line in text.splitlines():
        row = line.strip().lstrip("-*").strip()
        if not row or row.startswith("#"):
            continue
        refs.append(row)
        if len(refs) >= max(1, max_items):
            break
    return refs


def build_composite_genre_hints(genres: List[str], refs: List[str]) -> List[str]:
    if len(genres) <= 1:
        return []

    primary = genres[0]
    secondaries = genres[1:]
    hints: List[str] = []
    hints.append(
        f"以“{primary}”作为主引擎推进主线，每章至少保留1处“{'/'.join(secondaries)}”特征表达。"
    )
    if refs:
        hints.append(f"复合题材执行参考：{refs[0]}")
    hints.append("主辅题材冲突时，优先保证主题材读者承诺，辅题材用于制造新鲜感。")
    return hints

