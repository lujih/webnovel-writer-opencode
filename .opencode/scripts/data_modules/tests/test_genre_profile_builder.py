#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`extract_genre_section` 的分节匹配必须精确，不能用子串包含。

`genre_profile_builder.py`（107 行、零测试覆盖）被 `context_manager` 用来从
`.claude/references/genre-profiles.md` 里摘出当前题材的那一节，作为写作上下文。

实现是子串匹配：

    active = target in normalized          # normalized 是 "## 都市异能"

于是题材名互为前缀时，**谁在前就匹配谁**。实测：

    ## 都市异能        ← 排在前
    - 异能规则
    ## 都市
    - 都市规则

    extract_genre_section(doc, "都市")
    → "## 都市异能\\n- 异能规则"      ❌ 拿到的是「都市异能」的规则

作者写「都市」，上下文里注入的却是「都市异能」的写作要求。不报错、无提示。
反过来（`## 都市` 在前、查 `都市异能`）则是对的——因为 `都市` 是 `都市异能`
的子串，`"都市异能" in "## 都市"` 为 False。所以这个 bug 只在**特定分节顺序**
下出现，更难发现。

同类前缀对：都市/都市异能、游戏/游戏电竞、悬疑/悬疑脑洞、修仙/修仙玄幻。

## 本文件钉住

- 精确匹配分节标题，不做子串包含
- 支持标题带补充说明（`## 都市（含异能）` 应能匹配查询 `都市`）
- 找不到时回落到前 80 行（既有行为）
- `parse_genre_tokens` 的复合题材拆分行为不变
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.genre_profile_builder import (  # noqa: E402
    build_composite_genre_hints,
    extract_genre_section,
    extract_markdown_refs,
    parse_genre_tokens,
)


class TestExactSectionMatching:
    """核心缺陷：前缀碰撞导致摘错分节。"""

    PREFIX_COLLISION = """## 都市异能
- 异能规则
## 都市
- 都市规则
"""

    def test_prefix_shorter_after_longer(self):
        result = extract_genre_section(self.PREFIX_COLLISION, "都市")
        assert "都市规则" in result, (
            f"查「都市」却拿到了「都市异能」的分节：\n{result}\n"
            f"子串匹配下谁在前就匹配谁——作者写都市，注入的却是都市异能的规则。"
        )
        assert "异能规则" not in result

    def test_prefix_longer_after_shorter(self):
        doc = """## 都市
- 都市规则
## 都市异能
- 异能规则
"""
        result = extract_genre_section(doc, "都市异能")
        assert "异能规则" in result and "都市规则" not in result

    @pytest.mark.parametrize("shorter,longer", [
        ("都市", "都市异能"),
        ("游戏", "游戏电竞"),
        ("悬疑", "悬疑脑洞"),
        ("修仙", "修仙玄幻"),
    ])
    def test_all_prefix_pairs(self, shorter, longer):
        # 长名在前，短名在后——最容易踩坑的顺序
        doc = f"## {longer}\n- 长名规则\n## {shorter}\n- 短名规则\n"
        result = extract_genre_section(doc, shorter)
        assert "短名规则" in result, (
            f"查「{shorter}」拿到了「{longer}」的分节：\n{result}"
        )
        assert "长名规则" not in result


class TestSectionMatchingVariants:
    """标题的常见写法都要能匹配。"""

    def test_exact_heading(self):
        doc = "## 仙侠\n- 规则A\n## 都市\n- 规则B\n"
        assert "规则A" in extract_genre_section(doc, "仙侠")

    def test_heading_with_suffix_annotation(self):
        """`## 都市（含异能）` 应能被查询「都市」命中。"""
        doc = "## 都市（含异能）\n- 规则A\n## 悬疑\n- 规则B\n"
        result = extract_genre_section(doc, "都市")
        assert "规则A" in result, f"带补充说明的标题匹配失败：\n{result}"

    def test_heading_with_ascii_colon(self):
        doc = "## 仙侠: 核心\n- 规则A\n## 都市\n- 规则B\n"
        assert "规则A" in extract_genre_section(doc, "仙侠")

    def test_case_insensitive(self):
        doc = "## Xianxia\n- rule A\n## 都市\n- 规则B\n"
        assert "rule A" in extract_genre_section(doc, "xianxia")

    def test_three_level_heading(self):
        doc = "### 仙侠\n- 规则A\n"
        assert "规则A" in extract_genre_section(doc, "仙侠")


class TestFallbackUnchanged:
    """找不到时的回落行为不变。"""

    def test_missing_genre_falls_back_to_head(self):
        doc = "\n".join(f"第{i}行" for i in range(1, 101))
        result = extract_genre_section(doc, "不存在的题材")
        lines = result.splitlines()
        assert len(lines) == 80, f"回落应取前 80 行，实际 {len(lines)}"

    def test_empty_text(self):
        assert extract_genre_section("", "仙侠") == ""

    def test_no_heading_at_all(self):
        doc = "- 只有列表\n- 没有分节标题\n"
        result = extract_genre_section(doc, "仙侠")
        assert "只有列表" in result


class TestParseGenreTokensUnchanged:
    """复合题材拆分行为不变。"""

    def test_single(self):
        assert parse_genre_tokens("仙侠", support_composite=False,
                                  separators=("+",)) == ["仙侠"]

    def test_composite(self):
        result = parse_genre_tokens("仙侠+都市", support_composite=True,
                                    separators=("+",))
        assert len(result) == 2

    def test_dedup(self):
        result = parse_genre_tokens("仙侠+仙侠", support_composite=True,
                                    separators=("+",))
        assert len(result) == 1

    def test_empty(self):
        assert parse_genre_tokens("", support_composite=True,
                                  separators=("+",)) == []


class TestOtherHelpersUnchanged:
    def test_extract_markdown_refs(self):
        text = "# 标题\n- a\n- b\n- c\n"
        assert extract_markdown_refs(text, max_items=2) == ["a", "b"]

    def test_build_composite_hints_single(self):
        assert build_composite_genre_hints(["仙侠"], []) == []

    def test_build_composite_hints_multi(self):
        hints = build_composite_genre_hints(["仙侠", "都市"], ["ref"])
        assert hints and "仙侠" in hints[0]
