#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`coerce_urgency` 的边界值与"永不抛"承诺。

这个模块只有 29 行，却处在**记忆/伏笔紧急度**的入口：模型输出的
`urgency` 字段经它归一后写入记忆条目，再被下游筛选。

## 缺陷一：非有限浮点被当成有效值

    coerce_urgency("nan")  → nan
    coerce_urgency("inf")  → inf

`float("nan")` / `float("inf")` 都能通过 `float()`，于是被当作合法紧急度。
而下游的筛选是**比较**：

    # extract_chapter_context.py:158
    urgent = [row for row in active if row.get("urgency", 0) > 50]

`nan > 50` 恒为 `False`——**标了紧急的伏笔会被静默漏掉**。不报错、不告警，
作者永远不知道自己漏了一条紧急伏笔。`inf` 则会永久占据"最紧急"位置。

## 缺陷二：docstring 承诺 "Never raises"，但 `default` 不可转换时会抛

    coerce_urgency("bad", default=None)  → TypeError
    coerce_urgency("bad", default="x")   → ValueError

调用方（`memory_contract_adapter.py:332`、`memory/writer.py:229,307`）都用默认
`default=0.0`，所以现在没炸；但函数契约写着"永不抛异常"，实现却依赖调用方
恰好传了合法 default。这是一个随时会被下一个调用方踩中的雷。

## 本文件钉住的语义

- `nan` / `inf` / `-inf`（无论字符串还是浮点）→ 回落到 `default`
- 任何 `default` 都不得导致异常；不可转换时回落到 `0.0`
- 正常值、语言标签（high/medium/low）、中文标签的映射保持不变
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.urgency_utils import coerce_urgency  # noqa: E402


class TestNormalValuesUnchanged:
    """既有映射不能改坏。"""

    @pytest.mark.parametrize("value,expected", [
        (100, 100.0),
        (3.14, 3.14),
        ("100", 100.0),
        ("3.14", 3.14),
        (0, 0.0),
        ("0", 0.0),
    ])
    def test_numeric(self, value, expected):
        assert coerce_urgency(value) == pytest.approx(expected)

    @pytest.mark.parametrize("value,expected", [
        ("high", 100.0), ("HIGH", 100.0), ("紧急", 100.0),
        ("medium", 60.0), ("Medium", 60.0), ("一般", 60.0),
        ("low", 20.0), ("LOW", 20.0), ("远期", 20.0),
    ])
    def test_labels(self, value, expected):
        assert coerce_urgency(value) == pytest.approx(expected)

    @pytest.mark.parametrize("value", [None, "", "   ", [], {}, "看不懂"])
    def test_falls_back_to_default(self, value):
        assert coerce_urgency(value) == pytest.approx(0.0)

    def test_bool_uses_default(self):
        """bool 是 int 的子类，但语义上不是紧急度——保持既有排除。"""
        assert coerce_urgency(True) == pytest.approx(0.0)
        assert coerce_urgency(False) == pytest.approx(0.0)

    def test_custom_default(self):
        assert coerce_urgency(None, default=42.0) == pytest.approx(42.0)


class TestNonFiniteRejected:
    """核心缺陷一：nan/inf 不能当有效紧急度。"""

    @pytest.mark.parametrize("value", ["nan", "NaN", "inf", "Inf", "-inf", "-Inf"])
    def test_string_non_finite_falls_back(self, value):
        result = coerce_urgency(value)
        assert math.isfinite(result), (
            f"coerce_urgency({value!r}) 返回了非有限值 {result!r}。"
            f"下游用 `urgency > 50` 筛选，nan 与任何数比较都是 False——"
            f"标了紧急的伏笔会被静默漏掉。"
        )
        assert result == pytest.approx(0.0)

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_float_non_finite_falls_back(self, value):
        result = coerce_urgency(value)
        assert math.isfinite(result), f"浮点 {value!r} 未回落，返回 {result!r}"

    def test_non_finite_honours_default(self):
        assert coerce_urgency("nan", default=7.0) == pytest.approx(7.0)

    def test_nan_does_not_survive_urgency_filter(self):
        """端到端：模拟 extract_chapter_context 的筛选。"""
        rows = [
            {"content": "神秘玉佩", "urgency": coerce_urgency("nan")},
            {"content": "血海深仇", "urgency": coerce_urgency(100)},
        ]
        urgent = [r["content"] for r in rows if r["urgency"] > 50]
        assert "血海深仇" in urgent
        # nan 那条归一后是 0.0，正确地"不紧急"，而不是因为 nan 比较而消失
        assert all(math.isfinite(r["urgency"]) for r in rows), (
            "归一后的紧急度里仍有非有限值，下游筛选结果不可信"
        )


class TestNeverRaises:
    """核心缺陷二：docstring 承诺永不抛，就不能因为 default 而抛。"""

    @pytest.mark.parametrize("default", [None, "x", [], {}, object(), b"bytes"])
    def test_unconvertible_default_does_not_raise(self, default):
        try:
            result = coerce_urgency("bad", default=default)
        except Exception as exc:
            pytest.fail(
                f"coerce_urgency('bad', default={default!r}) 抛了 "
                f"{type(exc).__name__}: {exc}。docstring 写的是 Never raises。"
            )
        assert isinstance(result, float)

    @pytest.mark.parametrize("value", [None, "", "看不懂", float("nan"), object()])
    def test_any_value_any_default_never_raises(self, value):
        for default in (0.0, None, "x", [], 1.5):
            try:
                coerce_urgency(value, default=default)
            except Exception as exc:
                pytest.fail(
                    f"coerce_urgency({value!r}, default={default!r}) 抛了 "
                    f"{type(exc).__name__}: {exc}"
                )

    def test_valid_default_still_works(self):
        assert coerce_urgency(None, default=1.5) == pytest.approx(1.5)
        assert coerce_urgency(None, default="2.5") == pytest.approx(2.5)


class TestResultIsAlwaysFiniteFloat:
    """不变式：返回值永远是有限 float。"""

    @pytest.mark.parametrize("value", [
        None, "", "high", "medium", "low", 0, 100, 3.14, -5,
        "nan", "inf", "-inf", float("nan"), float("inf"),
        [], {}, object(), b"x", True, False,
    ])
    def test_finite(self, value):
        result = coerce_urgency(value)
        assert isinstance(result, float), f"{value!r} → {type(result)}"
        assert math.isfinite(result), f"{value!r} → 非有限值 {result!r}"
