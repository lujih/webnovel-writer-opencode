#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`observer_settler` 的自由文本容错。

Observer→Reflector 的设计前提是 **coverage-first**：
`observer-agent.md:67` 明确写「从章节正文中**过度提取**事实……宁可多提 10 条，
不漏 1 条。不确定的实体可以写描述而不是精确 entity_id。后续有专门的校验
步骤过滤」。

但下游 `observer_settler` 的正则要求**逐字精确**。实测：

    '- 萧炎（entity_id: xiaoyan）：突破'                → 1 事件 ✅
    '- 萧炎（entity_id: xiaoyan，置信度0.9）：突破'      → 0 事件 ❌ 静默丢弃
    '- 萧炎（entity_id: 未知，不确定）：突破'            → 0 事件 ❌ 静默丢弃

正则 `（entity_id:\\s*([^），]+?)）` 用 `[^），]` 禁掉了逗号与右括号，
模型只要在括号内多写一个补充说明（这恰恰是 prompt 鼓励的行为——"不确定的
实体可以写描述"），整行就被丢掉，**没有任何日志、计数或警告**。

更糟的是 `settle()` 只统计 Pydantic 校验丢弃（`logger.warning`），
正则不匹配的行连进入统计的资格都没有：

    all_events  ← 正则匹配的行
    dropped     ← Pydantic 校验失败的
    （正则没匹配的行：完全不可见）

于是「observer 提了 30 条，settler 产出 3 条」这种严重退化在日志里
看起来一切正常，只是状态更新得少——而这正是本项目要解决的核心问题
（AI 长篇写作的"遗忘"）的反面。

本文件钉住两点：
1. 括号内的补充说明不该让整行失效（容错解析）
2. 真正无法解析的行必须**可计数/可观测**，不能静默蒸发
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import data_modules.observer_settler as settler  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return tmp_path


class TestCanonicalFormStillWorks:
    """prompt 里的标准写法必须继续工作（别修坏正常路径）。"""

    @pytest.mark.parametrize("line", [
        '- 萧炎（entity_id: xiaoyan）：突破到斗师',
        '- 萧炎（entity_id: 未知）：突破到斗师',
        '- 萧炎（entity_id: 新）：突破到斗师',
    ])
    def test_character_state_canonical(self, line):
        events = settler._extract_character_state_changes([line], {}, 1)
        assert len(events) == 1
        assert events[0]["event_type"] == "character_state_changed"

    def test_entity_creation_canonical(self):
        line = '- 药老（类型：角色，entity_id: 新）：神秘老者'
        events = settler._extract_entity_creations([line], {}, 1)
        assert len(events) == 1
        assert events[0]["payload"]["entity_name"] == "药老"

    def test_relationship_canonical(self):
        line = '- 萧炎 ↔ 药老：关系从陌生变为师徒'
        events = settler._extract_relationships([line], {}, 1)
        assert len(events) == 1
        assert events[0]["payload"]["relationship_type"] == "师徒"

    def test_open_loop_canonical(self):
        assert len(settler._extract_open_loops(
            ['- [新伏笔] 药老身份（紧迫度：80）'], 1)) == 1
        assert len(settler._extract_open_loops(
            ['- [闭合] 药老身份'], 1)) == 1


class TestAnnotationInParensIsTolerated:
    """括号内加补充说明是 prompt 鼓励的行为，不该让整行失效。"""

    @pytest.mark.parametrize("line", [
        '- 萧炎（entity_id: xiaoyan，置信度0.9）：突破到斗师',
        '- 萧炎（entity_id: 未知，不确定）：突破到斗师',
        '- 萧炎（entity_id: xiaoyan，如不确定写"未知"）：突破到斗师',
        '- 萧炎（entity_id: xiaoyan；置信度0.9）：突破到斗师',
        '- 萧炎（entity_id: xiaoyan 置信度0.9）：突破到斗师',
    ])
    def test_character_state_with_extra_notes(self, line):
        events = settler._extract_character_state_changes([line], {}, 1)
        assert len(events) == 1, (
            f"括号内的补充说明让整行被丢弃：{line!r}\n"
            f"prompt 明确写「不确定的实体可以写描述」，而这里一个逗号就丢数据。"
        )

    def test_entity_creation_with_extra_notes(self):
        line = '- 药老（类型：角色，entity_id: 新，置信度0.9）：神秘老者'
        events = settler._extract_entity_creations([line], {}, 1)
        assert len(events) == 1

    def test_extracts_correct_entity_id_from_annotated_line(self):
        """容错解析不能把补充说明当成 entity_id。"""
        line = '- 萧炎（entity_id: xiaoyan，置信度0.9）：突破到斗师'
        events = settler._extract_character_state_changes([line], {}, 1)
        assert events[0]["payload"]["entity_id"] == "xiaoyan", (
            f"entity_id 解析成了 {events[0]['payload']['entity_id']!r}"
        )

    def test_name_not_polluted_by_notes(self):
        line = '- 萧炎（entity_id: xiaoyan，置信度0.9）：突破到斗师'
        events = settler._extract_character_state_changes([line], {}, 1)
        assert events[0]["payload"]["entity_name"] == "萧炎"


class TestUnparsedLinesAreVisible:
    """真正无法解析的行必须可观测，不能静默蒸发。"""

    def test_settle_reports_parse_failures(self, project):
        facts = project / "facts.md"
        facts.write_text(
            "## 角色状态变化\n"
            "- 这一行完全不符合格式，没有任何括号\n"
            "- 萧炎（entity_id: xiaoyan）：突破到斗师\n",
            encoding="utf-8",
        )
        result = settler.settle(facts, project, 1)

        # 要么给出行级统计，要么在返回里带上未解析计数
        blob = str(result)
        has_stat = any(
            k in result for k in ("unparsed", "parse_failures", "unparsed_lines",
                                  "skipped_lines", "warnings")
        )
        assert has_stat or "unparsed" in blob.lower(), (
            "settle() 没有报告任何「有行没被解析」的信息。"
            "observer 提 30 条、settler 出 3 条时，日志里看起来一切正常，"
            "只是状态更新得少——这正是本项目要解决的问题的反面。"
        )

    def test_parse_failure_count_is_accurate(self, project):
        facts = project / "facts.md"
        facts.write_text(
            "## 角色状态变化\n"
            "- 坏行一，没有括号\n"
            "- 坏行二，也没有括号\n"
            "- 萧炎（entity_id: xiaoyan）：突破到斗师\n",
            encoding="utf-8",
        )
        result = settler.settle(facts, project, 1)

        count = None
        for key in ("unparsed_lines", "parse_failures", "unparsed"):
            if key in result:
                value = result[key]
                count = len(value) if isinstance(value, list) else value
                break
        assert count == 2, (
            f"未解析行数统计不对：期望 2，实际 {count!r}。"
            f"返回键：{sorted(result.keys())}"
        )

    def test_unknown_section_is_not_silently_ignored(self, project):
        """observer 用了 prompt 里没有的标题时，整段内容会消失。"""
        facts = project / "facts.md"
        facts.write_text(
            "## 角色状态变化\n"
            "- 萧炎（entity_id: xiaoyan）：突破到斗师\n"
            "\n"
            "## 剧情推进\n"
            "- 这里有三条重要事实但标题不在白名单里\n",
            encoding="utf-8",
        )
        result = settler.settle(facts, project, 1)
        blob = str(result)
        assert (
            "剧情推进" in blob
            or any(k in result for k in ("unknown_sections", "unrecognized_sections"))
        ), (
            "未识别的 section 标题被完全忽略，里面的内容全部消失且无提示。"
            f"settle 返回键：{sorted(result.keys())}"
        )


class TestKnownEntityResolution:
    """提示词要求已知实体写 entity_id，未知写上描述——解析要能吃下两种。"""

    def test_resolves_known_entity_by_name(self):
        known = {"xiaoyan": {"canonical_name": "萧炎", "type": "角色"}}
        events = settler._extract_character_state_changes(
            ['- 萧炎（entity_id: xiaoyan）：突破'], known, 1)
        assert events[0]["subject"] == "xiaoyan"

    def test_unresolved_name_falls_back_to_name(self):
        events = settler._extract_character_state_changes(
            ['- 某个新角色（entity_id: 未知）：登场'], {}, 1)
        assert events[0]["subject"]
