"""紧急度归一化。

模型输出的 ``urgency`` 字段形状不可控（数字、数字字符串、"high" 之类标签、
``null``、空串、甚至被截断的垃圾），这里统一归成**有限 float**。
"""
from __future__ import annotations

import math

#: 不可转换时的最终兜底值。`default` 本身不可转 float 时用这个。
_FALLBACK = 0.0


def _to_float(value, fallback):
    """把 ``value`` 转成**有限** float，失败或非有限时返回 ``fallback``。

    为什么要显式排除非有限值：``float("nan")`` / ``float("inf")`` 都能通过
    ``float()``，于是 ``coerce_urgency("nan")`` 曾返回 ``nan``。而下游的
    筛选是比较：

        # extract_chapter_context.py:158
        urgent = [row for row in active if row.get("urgency", 0) > 50]

    ``nan > 50`` 恒为 ``False``——标了紧急的伏笔会被**静默漏掉**，不报错、
    不告警。``inf`` 则会永久占据"最紧急"位置。
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    if not math.isfinite(number):
        return fallback
    return number


def coerce_urgency(value, default=0.0):
    """Normalize urgency to a finite float. **Never raises.**

    Handles: int/float, numeric strings ("100", "3.14"),
    natural language ("high"/"medium"/"low", case-insensitive),
    None, empty string, bool, and unparseable values.
    String mapping aligns with config.foreshadowing_urgency_score_*.

    ``nan`` / ``inf`` / ``-inf``（字符串或浮点）一律回落到 ``default``——
    它们能通过 ``float()``，但会让下游 ``> threshold`` 比较静默失效。

    ``default`` 自身不可转成有限 float 时回落到 ``0.0``，以保证"永不抛"
    这条契约不依赖调用方传了什么。
    """
    # default 先归一一次：契约写的是 Never raises，就不能因为调用方传了
    # None / "x" / [] 而抛 TypeError/ValueError。
    fallback = _to_float(default, _FALLBACK)

    if value is None:
        return fallback
    if isinstance(value, bool):
        # bool 是 int 的子类，但 True/False 在语义上不是紧急度。
        return fallback
    if isinstance(value, (int, float)):
        return _to_float(value, fallback)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return fallback
        label = stripped.lower()
        if label in ("high", "紧急"):
            return 100.0
        if label in ("medium", "一般"):
            return 60.0
        if label in ("low", "远期"):
            return 20.0
        return _to_float(stripped, fallback)
    return fallback
