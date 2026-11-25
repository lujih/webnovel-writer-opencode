#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目对 OpenCode V2 规范的一致性检查。

依据（2026-11 官方文档，本地副本见 docs 链接）：
- https://opencode.ai/docs/agents/       —— agent frontmatter 字段与 action 名
- https://opencode.ai/docs/skills/       —— skill frontmatter 只解释 name/description/
                                            metadata.opencode/autoinvoke/disable-model-invocation
- https://opencode.ai/docs/commands/     —— 命令 frontmatter 无 name；subtask 已弃用
- https://opencode.ai/docs/permissions/  —— action 名表（shell/subagent/edit，无 bash/task/write/patch）
- https://opencode.ai/docs/build/plugins/migrate-v1 —— 插件 default export 需 id + setup(ctx)

这些断言刻意**只读文件**、不启动 OpenCode：目的是让「格式漂移」在 CI 就能被
发现，而不是等某个用户装完才发现 agent 没被加载。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
OPENCODE = REPO_ROOT / ".opencode"
AGENTS_DIR = OPENCODE / "agents"
SKILLS_DIR = OPENCODE / "skills"
COMMANDS_DIR = OPENCODE / "commands"
PLUGIN = OPENCODE / "plugins" / "write-guard.js"

#: V2 允许的 agent frontmatter 字段。V1 的 tools/permission/disable/maxSteps/
#: temperature/top_p/prompt 都在官方「不要在新配置里使用」清单里。
V2_AGENT_FIELDS = {
    "description", "mode", "model", "system", "permissions",
    "steps", "hidden", "color", "disabled", "request",
}
V2_AGENT_MODES = {"primary", "subagent", "all"}

#: V2 permissions 的内置 action（https://opencode.ai/docs/permissions#actions）
V2_ACTIONS = {
    "read", "edit", "glob", "grep", "shell", "subagent", "skill", "question",
    "webfetch", "websearch", "generate_image", "external_directory", "execute",
    "opencode_list_mcp_resources", "opencode_read_mcp_resource",
}
#: V1 名称，出现在 V2 里就是漂移
LEGACY_ACTIONS = {"bash", "task", "write", "patch"}


def _frontmatter(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    assert m, f"{path.name} 缺少 YAML frontmatter"
    return m.group(1)


def _fm_keys(fm: str) -> set[str]:
    return set(re.findall(r"^([A-Za-z_][\w-]*)\s*:", fm, re.M))


def _agent_files() -> list[Path]:
    return sorted(AGENTS_DIR.glob("*.md"))


def _skill_files() -> list[Path]:
    return sorted(SKILLS_DIR.glob("*/SKILL.md"))


def _command_files() -> list[Path]:
    return sorted(COMMANDS_DIR.glob("*.md"))


class TestAgentFrontmatterIsV2Native:
    def test_six_agents_exist(self):
        assert len(_agent_files()) == 6, [p.name for p in _agent_files()]

    @pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.stem)
    def test_only_v2_fields(self, path):
        unknown = _fm_keys(_frontmatter(path)) - V2_AGENT_FIELDS
        assert not unknown, (
            f"{path.name} 的 frontmatter 含非 V2 字段: {sorted(unknown)}。"
            "V1 字段（tools/permission/disable/maxSteps/temperature/name）"
            "在 V2 里要么被忽略、要么触发 warning。"
        )

    @pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.stem)
    def test_no_legacy_name_key(self, path):
        """frontmatter 的 `name:` 是 V1 遗留，且实测会让 V2 的 permissions 错位。"""
        assert "name" not in _fm_keys(_frontmatter(path)), (
            f"{path.name} 含 name:——V2 会把 permissions 解析进 request.body"
        )

    @pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.stem)
    def test_has_description(self, path):
        """V2 把 description 展示给「选哪个 agent」的模型，子 agent 必填。"""
        fm = _frontmatter(path)
        assert re.search(r"^description\s*:\s*\S", fm, re.M), f"{path.name} 缺 description"

    @pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.stem)
    def test_mode_is_valid(self, path):
        m = re.search(r"^mode\s*:\s*(\S+)", _frontmatter(path), re.M)
        assert m, f"{path.name} 缺 mode（自定义 agent 省略时默认 primary，本项目应显式声明）"
        assert m.group(1) in V2_AGENT_MODES, f"{path.name} 的 mode 非法: {m.group(1)}"

    @pytest.mark.parametrize("path", _agent_files(), ids=lambda p: p.stem)
    def test_permission_actions_are_v2_names(self, path):
        fm = _frontmatter(path)
        # `- action: "*"` 与 `- action: edit` 两种写法都要覆盖；引号要剥掉
        raw = re.findall(r"^\s*-?\s*action\s*:\s*(\S+)", fm, re.M)
        actions = {a.strip("\"'") for a in raw}
        legacy = actions & LEGACY_ACTIONS
        assert not legacy, f"{path.name} 用了 V1 action 名: {sorted(legacy)}"
        # `*` 是合法通配；其余必须是 V2 内置 action 或 MCP 工具名（<server>_<tool>）
        unknown = {
            a for a in actions
            if a != "*" and a not in V2_ACTIONS and not re.match(r"^[a-z0-9_]+_[a-z0-9_]+$", a)
        }
        assert not unknown, f"{path.name} 出现未知 action: {sorted(unknown)}"


class TestSkillsFollowV2Frontmatter:
    def test_sixteen_skills(self):
        assert len(_skill_files()) == 16, [p.parent.name for p in _skill_files()]

    @pytest.mark.parametrize("path", _skill_files(), ids=lambda p: p.parent.name)
    def test_has_description(self, path):
        """没有 description 的 skill 不会被广告给模型（/docs/skills#frontmatter）。"""
        assert re.search(r"^description\s*:\s*\S", _frontmatter(path), re.M), (
            f"{path.parent.name} 缺 description——模型看不到这个 skill"
        )

    @pytest.mark.parametrize("path", _skill_files(), ids=lambda p: p.parent.name)
    def test_no_allowed_tools(self, path):
        """`allowed-tools` 不在 V2 skill frontmatter 规范里（只解释 name/description/
        metadata.opencode/autoinvoke/disable-model-invocation）。留着它会被静默忽略，
        看起来像"限制了工具"其实没有任何效果——正是本项目反复栽的"声明了但没生效"。
        """
        assert "allowed-tools" not in _fm_keys(_frontmatter(path)), (
            f"{path.parent.name} 仍有 allowed-tools——V2 不解释该字段"
        )

    @pytest.mark.parametrize("path", _skill_files(), ids=lambda p: p.parent.name)
    def test_directory_name_matches_id(self, path):
        """ID 由路径决定；`name:` 只是展示名。二者对齐可避免 @skill-id 猜错。"""
        m = re.search(r"^name\s*:\s*(\S+)", _frontmatter(path), re.M)
        assert m, f"{path.parent.name} 缺 name"
        assert m.group(1) == path.parent.name, (
            f"{path.parent.name}/SKILL.md 的 name={m.group(1)}，与目录名不一致"
        )


class TestCommandsFollowV2Frontmatter:
    def test_five_commands(self):
        assert len(_command_files()) == 5, [p.name for p in _command_files()]

    @pytest.mark.parametrize("path", _command_files(), ids=lambda p: p.stem)
    def test_only_allowed_fields(self, path):
        """V2 命令 frontmatter：description / agent / model / subagent（subtask 已弃用）。"""
        allowed = {"description", "agent", "model", "subagent"}
        unknown = _fm_keys(_frontmatter(path)) - allowed
        assert not unknown, f"{path.name} 含 V2 不认识的字段: {sorted(unknown)}"

    @pytest.mark.parametrize("path", _command_files(), ids=lambda p: p.stem)
    def test_no_name_field(self, path):
        """命令 ID 取文件名，`name:` 是 V1 遗留。"""
        assert "name" not in _fm_keys(_frontmatter(path)), f"{path.name} 含 name:"

    @pytest.mark.parametrize("path", _command_files(), ids=lambda p: p.stem)
    def test_no_deprecated_subtask(self, path):
        assert "subtask" not in _fm_keys(_frontmatter(path)), (
            f"{path.name} 用了已弃用的 subtask（V2 用 subagent）"
        )


class TestNoV1DirectoryLayout:
    """V2 仍会发现 `agent/` `mode/` `command/` `skill/`，但首选是复数目录。

    保留旧目录会让「哪个文件生效」依赖发现顺序——本项目已统一到 V2 首选布局。
    """

    @pytest.mark.parametrize("dirname", ["agent", "mode", "modes", "command", "skill"])
    def test_legacy_dir_absent(self, dirname):
        assert not (OPENCODE / dirname).exists(), (
            f".opencode/{dirname}/ 是 V1 布局，请迁到复数目录（agents/commands/skills）"
        )


class TestPluginIsV2Portable:
    def test_default_export_has_id_and_setup(self):
        text = PLUGIN.read_text(encoding="utf-8")
        assert re.search(r"export\s+default\s*\{", text), "插件必须 default export 一个对象"
        # `id:` 可以是字面量，也可以指向模块级常量（本项目用 PLUGIN_ID）
        assert re.search(r"\bid\s*:\s*(['\"][\w.\-]+['\"]|[A-Za-z_][\w]*)", text), "插件缺少稳定 id"
        assert "async setup(ctx)" in text, "插件缺少 V2 的 setup(ctx) 入口"

    def test_setup_registers_tool_hook(self):
        """V2 的拦截点：ctx.tool.hook('execute.before', cb)，回调收**一个** event。"""
        text = PLUGIN.read_text(encoding="utf-8")
        assert "ctx.tool.hook('execute.before'" in text.replace('"', "'"), (
            "setup 未注册 ctx.tool.hook('execute.before')"
        )
        assert "event.tool" in text and "event.input" in text, (
            "V2 hook 回调应读 event.tool / event.input（V1 是 input.tool / output.args）"
        )

    def test_v1_entrypoint_kept_for_older_clients(self):
        """官方「one package, both generations」写法：同一 default export 上并存 server()。

        本项目主目标是 V2；保留 server() 是为了 1.18.29+ 的 V1 客户端不会静默失去守卫。
        """
        text = PLUGIN.read_text(encoding="utf-8")
        assert "async server()" in text, "缺少 V1 兼容入口 server()"
        assert "'tool.execute.before'" in text.replace('"', "'"), (
            "server() 应返回 V1 的 'tool.execute.before' hook"
        )


class TestConfigHasNoV1Residue:
    LEGACY_TOP_LEVEL = {
        "permission", "tools", "agent", "command", "plugin",
        "snapshot", "attachment", "reference", "provider",
    }

    def test_no_legacy_keys(self):
        raw = (OPENCODE / "opencode.json").read_text(encoding="utf-8")
        stripped = re.sub(r"^\s*//.*$", "", raw, flags=re.M)
        stripped = re.sub(r",(\s*[}\]])", r"\1", stripped)
        data = json.loads(stripped)
        present = self.LEGACY_TOP_LEVEL & set(data)
        assert not present, f"opencode.json 含 V1 顶层键: {sorted(present)}（V2 用复数/新名）"
