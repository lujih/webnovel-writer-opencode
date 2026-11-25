#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""observer 输出分节解析的标题语义。

审计背景：`_parse_markdown_sections` 此前只认 ``## ``，其余标题行一律当正文
塞进上一段。后果有两个，都直接污染 SSOT（settler 是事件日志的上游）：

1. ``### 角色状态变化``（模型偶尔多写一个 #）→ 整段内容被当成上一段的正文，
   该 section 在 `sections` 里根本不存在，提取器取到空列表 → 事件全丢；
2. ``### 张三`` 这类子条目 → 标题文字本身进事件描述，写进不可变事件日志。

本文件钉住标题处理的四条规则。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from data_modules.observer_settler import (
    KNOWN_SECTION_HEADINGS,
    _parse_markdown_sections,
)


class TestHeadingLevels:
    def test_h2_opens_section(self):
        sections = _parse_markdown_sections(
            "## 角色状态变化\n- 张三：境界 斗者 → 斗师\n"
        )
        assert sections["角色状态变化"] == ["- 张三：境界 斗者 → 斗师"]

    def test_h3_known_heading_opens_section(self):
        """`### 角色状态变化` 必须被识别——不认它等于整段静默消失。"""
        sections = _parse_markdown_sections(
            "### 角色状态变化\n- 张三：境界 斗者 → 斗师\n"
        )
        assert "角色状态变化" in sections
        assert sections["角色状态变化"] == ["- 张三：境界 斗者 → 斗师"]

    def test_h3_unknown_heading_is_not_content(self):
        """子条目标题（`### 张三`）不能作为正文进事件描述。"""
        sections = _parse_markdown_sections(
            "## 关系变化\n### 张三\n- 张三 与 李四 结为盟友\n"
        )
        lines = sections["关系变化"]
        assert "- 张三 与 李四 结为盟友" in lines
        assert "### 张三" not in lines
        assert "张三" not in lines, "子标题文字不该混进事件正文"
        # 子条目下的内容仍归属上一段（不新建未知 section）
        assert set(sections) == {"关系变化"}

    def test_numbered_h2_prefix_is_stripped(self):
        sections = _parse_markdown_sections("## 1. 力量突破\n- 张三 突破到斗师\n")
        assert "力量突破" in sections

    def test_blank_lines_and_preamble_ignored(self):
        sections = _parse_markdown_sections(
            "一些前言，没有标题\n\n## 新出场实体\n\n- 李四（entity_id: lisi）\n"
        )
        # 没有 current_heading 时的正文被丢弃（此前会因 current_heading=None 而丢，行为不变）
        assert set(sections) == {"新出场实体"}
        assert sections["新出场实体"] == ["- 李四（entity_id: lisi）"]

    def test_all_known_headings_reachable_at_h3(self):
        """白名单里每一条都要能在 3 级标题下被识别。"""
        for heading in KNOWN_SECTION_HEADINGS:
            sections = _parse_markdown_sections(f"### {heading}\n- 一条事实\n")
            assert heading in sections, f"{heading} 在 ### 下未被识别"
