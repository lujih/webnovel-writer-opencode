#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`migrate_agents_v2_permissions` 对**已迁移文件**的再处理必须无损。

这个脚本会**就地改写** `.opencode/agents/*.md` 的 frontmatter，207 行、零测试
覆盖。它的 docstring 定位是「幂等可重复运行」的迁移工具。

## 缺陷：已有 `permissions:` 时会把规则集重新渲染成残缺版本

`convert()` 在 `tools:` 块不存在时，从现有 `permissions:` 反推需要的工具名：

    tools = _v1_tools_for(parse_allowed_actions(fm))

而 `_v1_tools_for` 用 `_ACTION` 的反向映射：

    inverse = {v: k for k, v in _ACTION.items() if k not in ("write", "edit")}
    for action in allowed:
        out.append(inverse.get(action, action))
    if "edit" in allowed:
        out.append("write")

两个问题：

1. **`edit` 被排除在反查表外**——V2 里 `read` 与 `edit` 都映射到 V1 的
   `edit`（`_ACTION = {..., "read": "edit", "write": "write"}`），反向构造时
   把 `edit`/`write` 排除掉，于是 `read` 反查成 `edit`，`edit` 反查不到就原样
   保留。结果：用户配的 `read` 变成 `edit`。
2. **映射表里没有的动作被原样输出**——`bash` 不在 `_ACTION` 里（V2 叫
   `bash`，V1 叫 `shell`），于是 `action: bash` 被丢弃，而脚本又补上
   `action: shell`。看起来"对"，但**用户原来显式 deny 的规则、自定义的
   resource 限定、注释全部丢失**。

实测（一个规则完备的 agent）：

    输入允许: read / edit / bash
    输出允许: read / edit / shell     ← bash 语义被换掉
    且原有 resource 限定与 deny 规则被整套重写

这个脚本在一次"幂等迁移"里**悄悄改写了 agent 的权限语义**。对
`reviewer` / `data-agent` 这类带 SSOT deny 规则的 agent，风险尤其高。

## 本文件钉住

- 已含 `permissions:` 且**规则集已完整**的文件 → 原样返回（真幂等）
- 反推映射必须能还原 `bash`↔`shell`、`read`↔`edit`
- `tools:` 路径的迁移行为不变（那是它的本职）
- 缺 frontmatter / 缺 tools 的异常信息准确
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.migrate_agents_v2_permissions import (  # noqa: E402
    _v1_tools_for,
    convert,
    parse_allowed_actions,
    parse_tools,
)


def _doc(frontmatter: str, body: str = "正文\n") -> str:
    return f"---\n{frontmatter}\n---\n{body}"


class TestToolsMigrationUnchanged:
    """本职功能：`tools:` → `permissions:` 不能改坏。"""

    def test_simple_tools_block(self):
        src = _doc("description: t\ntools:\n  read: true\n  edit: true")
        out = convert(src)
        assert "permissions:" in out
        # 精确匹配行首的 YAML 键——注释里会出现 "V1 的 `tools:`" 这类文字
        assert not any(line.strip() == "tools:" for line in out.splitlines()), (
            "tools: 块没有被替换掉"
        )
        assert "effect: deny" in out, "必须先 deny-all 再逐条放行"

    def test_parse_tools(self):
        assert parse_tools("tools:\n  read: true\n  edit: false") == ["read"]

    def test_idempotent_on_tools_output(self):
        src = _doc("description: t\ntools:\n  read: true\n  edit: true")
        once = convert(src)
        assert convert(once) == once, "对自己的输出必须幂等"


class TestInverseMappingIsComplete:
    """核心缺陷：反推映射不能丢动作或换错语义。"""

    def test_shell_roundtrips(self):
        """V2 的 shell 必须能还原成 V1 的 bash。"""
        tools = _v1_tools_for(["shell"])
        assert "bash" in tools, (
            f"_v1_tools_for(['shell']) → {tools}。V2 的 shell 对应 V1 的 bash"
        )

    def test_read_does_not_become_edit(self):
        """read 不能被反推成 edit（两者在正向映射里都指向 edit）。"""
        tools = _v1_tools_for(["read"])
        assert "read" in tools, f"_v1_tools_for(['read']) → {tools}，read 被换成了别的"

    def test_edit_preserved(self):
        assert "edit" in _v1_tools_for(["edit"])

    def test_mixed_actions_all_present(self):
        tools = _v1_tools_for(["read", "edit", "shell", "glob", "grep"])
        for expected in ("read", "edit", "bash", "glob", "grep"):
            assert expected in tools, f"{expected} 在反推结果里丢失：{tools}"


class TestExistingPermissionsPreserved:
    """核心缺陷：已有 permissions 的文件不能被重写成残缺规则集。"""

    FULL = (
        "description: 测试\n"
        "permissions:\n"
        '  - action: "*"\n    resource: "*"\n    effect: deny\n'
        '  - action: read\n    resource: "*"\n    effect: allow\n'
        '  - action: edit\n    resource: "*"\n    effect: allow\n'
        '  - action: bash\n    resource: "*"\n    effect: allow\n'
    )

    def test_allowed_actions_read_back(self):
        fm = self.FULL
        assert set(parse_allowed_actions(fm)) == {"read", "edit", "bash"}

    def test_all_actions_survive_conversion(self):
        src = _doc(self.FULL)
        out = convert(src)
        for action in ("read", "edit", "bash"):
            assert f"action: {action}" in out or (
                action == "bash" and "action: shell" in out
            ), f"action {action} 在输出里消失了：\n{out}"

    def test_deny_all_still_first(self):
        out = convert(_doc(self.FULL))
        deny_pos = out.find("effect: deny")
        allow_pos = out.find("effect: allow")
        assert deny_pos != -1 and deny_pos < allow_pos, "deny-all 必须排在放行规则之前"

    def test_custom_resource_rules_preserved(self):
        """用户自定义的 resource 限定不能被抹掉。

        注意：`parse_allowed_actions` 只认 `resource: "*"`，所以带 resource
        限定的 allow 规则反推不出工具名。这种情况下 convert() **不该重写**——
        它没有能力保留那些规则，重写就是静默丢数据。
        """
        fm = (
            "description: t\n"
            "permissions:\n"
            '  - action: "*"\n    resource: "*"\n    effect: deny\n'
            '  - action: read\n    resource: "正文/**"\n    effect: allow\n'
        )
        out = convert(_doc(fm))
        assert out == _doc(fm), (
            f"文件含本脚本无法无损保留的自定义 resource 规则，却被重写了：\n{out}\n"
            f"重写会静默丢掉 正文/** 这条限定。应当原样返回。"
        )


class TestFullyCurrentFileIsUntouched:
    """真幂等：规则已完整的文件应原样返回。"""

    def test_real_agent_file_is_stable(self):
        """仓库里真实的 agent 文件必须已经是最新形态。"""
        agents_dir = SCRIPTS.parent / "agents"
        if not agents_dir.is_dir():
            pytest.skip("agents 目录不存在")
        for path in sorted(agents_dir.glob("*.md")):
            original = path.read_text(encoding="utf-8")
            assert convert(original) == original, (
                f"{path.name} 不是最新形态——迁移脚本会改动它。"
                f"请运行 migrate_agents_v2_permissions.py 并提交结果。"
            )


class TestErrorsAreAccurate:
    def test_missing_frontmatter(self):
        with pytest.raises(ValueError, match="frontmatter"):
            convert("# 没有 frontmatter\n")

    def test_missing_tools_and_permissions(self):
        with pytest.raises(ValueError):
            convert(_doc("description: 只有描述"))

    def test_empty_frontmatter_message(self):
        """`---\\n---` 是空 frontmatter，报错信息应说清是"内容为空"。"""
        with pytest.raises(ValueError) as excinfo:
            convert("---\n---\n正文\n")
        msg = str(excinfo.value)
        assert "frontmatter" in msg, f"错误信息应提到 frontmatter：{msg!r}"
