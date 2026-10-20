#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""紧急伏笔筛选不能因 state.json 里的脏 `urgency` 而崩溃或漏项。

`extract_chapter_context.py` 组装上下文摘要时：

    foreshadowing = plot_threads.get("foreshadowing", [])
    active = [row for row in foreshadowing if row.get("status") in {"active", "未回收"}]
    urgent = [row for row in active if row.get("urgency", 0) > 50]

这里读的是 **state.json 里的原始值**，不经过 `coerce_urgency`。而 state.json
是用户可编辑、也是历史数据长期累积的地方，`urgency` 完全可能是：

    80      →  正常比较 ✅
    "80"    →  TypeError: '>' not supported between 'str' and 'int'   ❌
    "nan"   →  TypeError                                              ❌
    None    →  TypeError                                              ❌
    "high"  →  TypeError                                              ❌
    nan     →  恒 False，紧急伏笔静默消失                              ❌

前四种会让 `build_context()` 整个崩掉——而这是**每一章写作的必经之路**，
一条脏数据就能让作者再也无法写下一章，且错误信息是
`'>' not supported between instances of 'str' and 'int'`，完全指不出是哪条
伏笔的问题。

最后一种（`nan`）更隐蔽：不崩，但标了紧急的伏笔永远进不了"紧急伏笔"摘要。

本文件钉住：无论 `urgency` 是什么形状，筛选都要么正常工作、要么按"不紧急"
处理，绝不抛异常。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.urgency_utils import coerce_urgency  # noqa: E402


def _urgent_filter(rows):
    """复现 extract_chapter_context.py:157-158 的筛选语义。"""
    active = [r for r in rows if r.get("status") in {"active", "未回收"}]
    return [r for r in active if coerce_urgency(r.get("urgency")) > 50]


class TestDirtyUrgencyDoesNotCrash:
    """核心：任何形状的 urgency 都不能让筛选抛异常。"""

    @pytest.mark.parametrize("raw", [
        80, 80.5, "80", "80.5", "high", "紧急", "nan", "inf",
        None, "", "看不懂", True, False, [], {}, float("nan"), float("inf"),
    ])
    def test_no_exception(self, raw):
        rows = [{"content": "神秘玉佩", "status": "active", "urgency": raw}]
        try:
            result = _urgent_filter(rows)
        except Exception as exc:
            pytest.fail(
                f"urgency={raw!r} 让紧急伏笔筛选抛了 {type(exc).__name__}: {exc}。"
                f"state.json 是用户可编辑的历史数据，一条脏数据就能让 "
                f"build_context 崩溃——而那是每章写作的必经之路。"
            )
        assert isinstance(result, list)


class TestDirtyUrgencySemantics:
    """形状合法但语义脏的值，按"不紧急"处理。"""

    @pytest.mark.parametrize("raw", [None, "", "看不懂", "nan", float("nan"),
                                     [], {}, False])
    def test_not_urgent(self, raw):
        rows = [{"content": "神秘玉佩", "status": "active", "urgency": raw}]
        assert _urgent_filter(rows) == [], (
            f"urgency={raw!r} 被判为紧急——无从判断紧急度时不该算紧急"
        )

    @pytest.mark.parametrize("raw,urgent", [
        (80, True), ("80", True), (80.5, True), ("high", True), ("紧急", True),
        (50, False), ("50", False), (0, False),
    ])
    def test_threshold(self, raw, urgent):
        rows = [{"content": "x", "status": "active", "urgency": raw}]
        assert bool(_urgent_filter(rows)) is urgent


class TestNormalCaseStillWorks:
    """正常数据的行为不变。"""

    def test_picks_only_active_urgent(self):
        rows = [
            {"content": "A", "status": "active", "urgency": 90},
            {"content": "B", "status": "active", "urgency": 10},
            {"content": "C", "status": "closed", "urgency": 99},
            {"content": "D", "status": "未回收", "urgency": "80"},
        ]
        picked = [r["content"] for r in _urgent_filter(rows)]
        assert picked == ["A", "D"]

    def test_missing_urgency_is_not_urgent(self):
        rows = [{"content": "A", "status": "active"}]
        assert _urgent_filter(rows) == []

    def test_empty_list(self):
        assert _urgent_filter([]) == []


class TestSourceFileUsesCoercion:
    """静态检查：源文件里那一行不能再裸比较原始值。"""

    def test_no_raw_urgency_comparison(self):
        source = (SCRIPTS / "extract_chapter_context.py").read_text(encoding="utf-8-sig")
        offenders = [
            line.strip()
            for line in source.splitlines()
            if 'row.get("urgency"' in line and ">" in line and "coerce" not in line
        ]
        assert not offenders, (
            f"仍有对原始 urgency 的裸比较：{offenders}。"
            f"state.json 里的值可能是 '80' / None / 'nan'，裸比较会抛 TypeError "
            f"或静默漏掉紧急伏笔。"
        )
