"""把 6 个 agent 的 legacy `tools:` 映射迁到 OpenCode V2 原生 `permissions:`。

为什么必须显式 deny-all
------------------------
V1 文档明写 "the legacy `tools` boolean config is deprecated and has been
merged into `permission`"，且 V1/V2 的默认策略都是**放行**：

    { "action": "*", "resource": "*", "effect": "allow" }

所以仓库里那 6 个 `tools: {read: true, grep: true, bash: true}` 实际上**从未
构成白名单**——未列出的工具回落到默认放行。只把 true 翻成 allow 规则的话，
迁移前后行为完全一致，约束依然是零。要让声明的意图真正生效，必须先
deny-all 再逐条放行。

为什么不能只写 deny-all
------------------------
`action: "*"` 连 `external_directory` 一起拒了，而书项目在
E:\\workspace\\webnovel2 / E:\\workspace\\webnovel，**在仓库之外**。
不把 external_directory 放回 ask，所有 agent 立刻读不到书、正文与 state.json。
这正是本脚本里被显式钉住的一条。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

AGENTS_DIR = Path(__file__).resolve().parents[3] / ".opencode" / "agents"

# V1 工具名 → V2 permission action。V2 的 `edit` 覆盖 edit/write/patch 三种操作；
# `bash` 改名 `shell`；`task` 改名 `subagent`。V2 没有独立的 `write` 动作。
_ACTION = {
    "read": "read",
    "glob": "glob",
    "grep": "grep",
    "bash": "shell",
    "write": "edit",
    "edit": "edit",
    "task": "subagent",
    "skill": "skill",
    "question": "question",
    "webfetch": "webfetch",
    "websearch": "websearch",
}

# 规则顺序即优先级（最后命中的规则生效）。
#
# glob 与 grep 无条件放行：它们是只读发现工具，6 个 agent 都要靠它们定位章节、
# 设定与参考文件。data-agent / observer-agent 的原 `tools:` 没列 grep，但 V1
# 默认本就放行——deny-all 会把它一起拒掉，那属于本次迁移自造的回归。
# 只读工具不该因为"没人显式写过"而被收走。
#
# 真正收紧的只有两类：需要提权的动作（委派/联网/追问用户）与需要落盘的 edit
# （按各 agent 原文是否真要写文件逐个判定）。
ALWAYS_ALLOW = ("glob", "grep")

_ORDER = ["read", "glob", "grep", "shell", "edit", "skill", "subagent",
          "question", "webfetch", "websearch"]


def parse_tools(frontmatter: str) -> list[str]:
    m = re.search(r"^tools:\s*$(.*?)(?=^\S|\Z)", frontmatter, re.M | re.S)
    if not m:
        return []
    return re.findall(r"^\s+([a-z_]+):\s*(?:true|false)\s*$", m.group(1), re.M)


def render_permissions(tools: list[str]) -> str:
    wanted = {_ACTION[t] for t in tools if _ACTION.get(t)} | set(ALWAYS_ALLOW)
    unknown = sorted(t for t in tools if not _ACTION.get(t))
    if unknown:
        raise ValueError(f"未知 V1 工具名，无法映射: {unknown}")

    actions = [a for a in _ORDER if a in wanted]
    lines = [
        "permissions:",
        "  # V2 默认策略是 allow-all（见 V2 /docs/permissions 的 Defaults），",
        "  # 不先整体拒绝就等于没有约束——V1 的 `tools:` 布尔映射其实也从未构成白名单。",
        "  - action: \"*\"",
        "    resource: \"*\"",
        "    effect: deny",
        "  # 恢复基础策略里的 external_directory 询问：书项目在仓库之外",
        "  # （E:\\workspace\\webnovel2 等），一并 deny 会让 agent 读不到正文与 state.json。",
        "  - action: external_directory",
        "    resource: \"*\"",
        "    effect: ask",
    ]
    for action in actions:
        lines += [f"  - action: {action}", "    resource: \"*\"", "    effect: allow"]
    return "\n".join(lines)


def convert(text: str) -> str:
    m = re.search(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        raise ValueError("缺少 frontmatter")
    fm = m.group(1)
    if "permissions:" in fm:
        return text  # 已迁移，幂等
    tools = parse_tools(fm)
    if not tools:
        raise ValueError("未找到 tools: 块")
    block = render_permissions(tools) + "\n"
    # 用 lambda 替换：渲染出的文本含 E:\workspace 这类反斜杠，直接当 repl 会
    # 被 re 当转义序列处理（re.error: bad escape \w）。
    fm = re.sub(r"^tools:\s*$.*?(?=^\S|\Z)", lambda _m: block, fm, flags=re.M | re.S)
    return text[:m.start(1)] + fm + text[m.end(1):]


def main() -> int:
    for path in sorted(AGENTS_DIR.glob("*.md")):
        original = path.read_text(encoding="utf-8")
        updated = convert(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            print(f"migrated: {path.name}")
        else:
            print(f"skipped (already migrated): {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
