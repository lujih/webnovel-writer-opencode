#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Foreshadowing loop-content coercion — single source of truth.

`open_loop_created` / `open_loop_closed` 事件从 payload 提取"悬念内容"字符串，
state 投影（plot_threads.foreshadowing）、memory 投影（scratchpad open_loops）
与 ssot_enforcer rebuild 三侧必须提取出**同一个** content 字符串，否则
`open_loop_closed` 事件在两路投影中匹配不到同一条目，会造成"活跃伏笔"残留
或顶层 foreshadowing 与嵌套 plot_threads.foreshadowing 永久失配。

统一规则（优先级从高到低）：
    content → unanswered_question
    → loop_type+"："+description（结构化）
    → description → loop_type
    → subject（event["subject"] 或 payload["_subject"]，rebuild 回放时 subject
      已被 publish_event 扁平化进 payload["_subject"]）

`event` 为完整事件 dict（含 "subject" 键，增量/live 路径）；rebuild 回放时
payload 中的 "_subject" 承载同一信息。传入 None 也可。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

__all__ = ["coerce_loop_content"]


def coerce_loop_content(payload: Dict[str, Any], event: Optional[Dict[str, Any]] = None) -> str:
    """Extract the foreshadowing content string from an open_loop event.

    与 state/memory/enforcer 三侧历史实现保持等价：subject 兜底同时看
    event 顶层 "subject"（live）与 payload["_subject"]（rebuild 扁平化），
    保证两路路径提取结果一致。
    """
    p = payload if isinstance(payload, dict) else {}
    for key in ("content", "unanswered_question"):
        value = str(p.get(key) or "").strip()
        if value:
            return value

    description = str(p.get("description") or "").strip()
    loop_type = str(p.get("loop_type") or "").strip()

    if description and loop_type:
        return f"{loop_type}：{description}"
    if description:
        return description
    if loop_type:
        return loop_type

    # subject 兜底：live 事件顶层 subject 优先，rebuild 扁平化的 _subject 次之
    subject = ""
    if isinstance(event, dict):
        subject = str(event.get("subject") or "").strip()
    if not subject:
        subject = str(p.get("_subject") or "").strip()
    return subject
