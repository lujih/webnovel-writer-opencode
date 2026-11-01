#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""prompt 与代码的契约：observer 分段名、data-agent 的歧义消费。

## 为什么需要这组测试

prompt 与代码之间有两处**字符串级的隐式契约**，漂移起来完全无声：

1. **observer 的 9 个 `## 段落名` 是 `observer_settler.py` 的查找键**。
   settler 用 `_parse_markdown_sections()` 按 `## ` 切分，再用**字符串全等**
   去查 `heading_extractors`。实测：把「角色状态变化」写成「角色状态」不会
   报错——那一节变成**孤儿 section**，里面的事件**静默全部丢弃**，而且
   `unparsed_lines` 只统计**已识别**的 section，所以日志里也看不出来。

2. **`data-agent.md` 必须要求消费 `ambiguous_mentions`**。settler 检出同名
   歧义后只写文件 + 打 warning；若 agent 不把它转成 `disambiguation_result`
   的 `pending`，提交侧看到 `pending` 为空 → `artifact_validator` 放行 →
   错误实体绑定被固化为事实。`pending` 非空时门禁是 error 级阻断。

本文件把两处契约钉住：改 prompt 忘了改代码（或反之）会立刻失败。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
AGENTS_DIR = REPO_ROOT / ".opencode" / "agents"
SCRIPTS = REPO_ROOT / ".opencode" / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules import observer_settler  # noqa: E402


def _read_agent(name: str) -> str:
    return (AGENTS_DIR / name).read_text(encoding="utf-8")


def _settler_headings() -> list[str]:
    """从 settler 源码里取出 `heading_extractors` 的全部键。

    直接解析源码而不是抄一份清单——抄写会在两边各自漂移，而那正是要防的事。
    """
    import re

    source = (SCRIPTS / "data_modules" / "observer_settler.py").read_text(encoding="utf-8")
    # 形如  ("角色状态变化", _extract_character_state_changes),
    pattern = re.compile(r'\("([^"]+)",\s*(?:_extract_\w+|_extract\w*)\)')
    block_start = source.index("heading_extractors = [")
    block_end = source.index("]", source.index("_extract_open_loops", block_start))
    block = source[block_start:block_end + 1]
    headings = pattern.findall(block)
    assert headings, "未能从源码解析出 heading 列表——正则或源码结构变了"
    return headings


def _declared_headings(prompt: str) -> list[str]:
    """取出 prompt 里以 `### ## 名字` 形式逐个列出的分段名。

    只认这种「列出」形式；正文里作为反例/警示提到的 `## 名字` 不算——
    否则子串检查会被自己的说明文字满足。
    """
    import re

    return re.findall(r"^### ## (.+?)\s*$", prompt, re.MULTILINE)


class TestObserverHeadingsMatchSettler:
    """observer prompt 的分段名必须与 settler 的查找键逐字一致。"""

    def test_prompt_declares_exactly_settler_keys(self):
        """逐个列出的分段名集合必须与 settler 查找键**完全相等**。

        不能用 `"## 名字" in prompt` 这种子串判断——prompt 里讲「不要把它写成
        `## 角色状态`」的**警示文案本身**就含有这些字符串，子串检查会被自己的
        注释满足（本会话在别处踩过同一个坑：静态检查匹配到自己的说明）。
        故这里只认「以 `### ## 名字` 形式列出的段落」。
        """
        prompt = _read_agent("observer-agent.md")
        expected = set(_settler_headings())
        declared_list = _declared_headings(prompt)
        declared = set(declared_list)

        missing = expected - declared
        extra = declared - expected
        assert not missing, (
            f"prompt 缺少这些 settler 需要的分段：{sorted(missing)}。"
            f"缺一个 = 那一节的事件静默全丢。"
        )
        assert not extra, (
            f"prompt 列出了 settler 不认识的分段：{sorted(extra)}。"
            f"写了也不会被提取（孤儿 section）。"
        )
        assert len(declared_list) == len(declared), (
            f"分段被重复列出：{declared_list}"
        )

    def test_heading_count_matches(self):
        prompt = _read_agent("observer-agent.md")
        assert len(_declared_headings(prompt)) == len(_settler_headings())

    def test_prompt_warns_headings_are_exact_keys(self):
        """光有几个标题不够——必须明说「逐字照抄、不得改写」，否则模型会意译。"""
        prompt = _read_agent("observer-agent.md")
        assert "逐字照抄" in prompt or "精确匹配" in prompt, (
            "prompt 必须明确告知分段名是代码查找键（否则会被模型意译/缩写）"
        )
        assert "静默" in prompt or "丢弃" in prompt, (
            "prompt 应说明违反契约的后果（静默丢弃），否则模型不会当回事"
        )

    def test_orphan_heading_is_actually_dropped(self):
        """用真实解析器证明「改名 = 丢事件」，防止这条契约被当成危言耸听。"""
        text = (
            "## 角色状态变化\n"
            "- 林三（entity_id: 未知）：境界从斗者变为斗师\n"
            "\n"
            "## 角色状态\n"
            "- 李四（entity_id: 未知）：境界从斗者变为斗师\n"
        )
        sections = observer_settler._parse_markdown_sections(text)
        assert "角色状态变化" in sections
        assert "角色状态" in sections, "改名后确实生成了另一个键（孤儿 section）"
        known_keys = set(_settler_headings())
        assert "角色状态" not in known_keys, (
            "孤儿键不在 settler 的查找列表里 → 该节事件永远不会被提取"
        )

    def test_numbered_heading_is_normalized(self):
        """带编号的标题会被剥离前缀，故 prompt 允许 `## 1. 角色状态变化`。"""
        sections = observer_settler._parse_markdown_sections(
            "## 1. 力量突破\n- 林三（entity_id: x）：从斗师突破至大斗师\n"
        )
        assert "力量突破" in sections


class TestDataAgentConsumesAmbiguity:
    """data-agent prompt 必须要求消费 ambiguous_mentions。"""

    def test_mentions_ambiguous_mentions_field(self):
        """必须存在**要求块**，不能只有别处顺带一提。

        `data-agent.md` 的校验清单里也有 `ambiguous_mentions` 字样——只在清单里
        提一句是不够的，agent 不会知道该怎么转换。故要求 B-1 要求块本身存在。
        """
        prompt = _read_agent("data-agent.md")
        assert "**B-1" in prompt, (
            "缺少 B-1 要求块——歧义消费不能只在别处提一句，必须有明确步骤"
        )
        assert "`ambiguous_mentions`" in prompt
        assert '"source": "observer_settler.ambiguous_mentions"' in prompt, (
            "缺少 `source` 字段示例——agent 会写出下游读不到的形状"
        )

    def test_requires_pending_population(self):
        prompt = _read_agent("data-agent.md")
        assert "pending" in prompt, "必须说明要写进 disambiguation_result 的 pending"

    def test_states_fail_closed_consequence(self):
        """必须讲清「不填 pending 会怎样」：门禁放行 → 错误绑定被固化。"""
        prompt = _read_agent("data-agent.md")
        assert "固化为事实" in prompt or "fail-closed" in prompt or "阻断" in prompt, (
            "应说明 pending 非空时门禁阻断（fail-closed）的后果链"
        )

    def test_empty_case_explicit(self):
        """没有歧义时也要显式表态 `pending: []`，不能省略该键。"""
        prompt = _read_agent("data-agent.md")
        assert "pending: []" in prompt, (
            "必须要求「无歧义时写 pending: []」——省略键会让下游无从区分"
            "「没歧义」与「忘了写」"
        )

    def test_prompt_shape_matches_code_helper(self):
        """prompt 里给的 pending 形状应与 `ambiguous_to_pending()` 输出一致。"""
        pending = observer_settler.ambiguous_to_pending([
            {"mention": "萧炎",
             "candidates": ["char_a", "char_b"],
             "entity_type": "角色"},
        ])
        assert len(pending) == 1
        prompt = _read_agent("data-agent.md")
        for key in pending[0]:
            assert f'"{key}"' in prompt, (
                f"`ambiguous_to_pending()` 产出的字段 {key!r} 未在 prompt 的示例里出现——"
                f"agent 会写出下游读不到的形状"
            )


class TestAgentPromptsStillValid:
    """两处改动不得破坏既有的结构约束。"""

    @pytest.mark.parametrize("name", ["observer-agent.md", "data-agent.md"])
    def test_frontmatter_has_no_name_key(self, name):
        """OpenCode V2 下 frontmatter 的 `name:` 会让 permissions 整个错位。"""
        text = _read_agent(name)
        frontmatter = text.split("---")[1]
        assert "\nname:" not in frontmatter, (
            "agent frontmatter 不得有 name 键（V2 会把 permissions 错位到 "
            "request.body.permissions）"
        )

    @pytest.mark.parametrize("name", ["observer-agent.md", "data-agent.md"])
    def test_frontmatter_has_description(self, name):
        text = _read_agent(name)
        frontmatter = text.split("---")[1]
        assert "description:" in frontmatter
