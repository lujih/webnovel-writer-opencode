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

# SSOT 受保护文件。与 .opencode/plugins/write-guard.js 的 PROTECTED_SUFFIXES
# 同一份清单，但作用机制不同、且更靠前：
#   - write-guard 是 tool.execute.before 钩子，模型仍**看得见** edit 工具，
#     只是运行时被 throw 拒绝；
#   - V2 文档对 permissions 的 deny 明确说会把工具"从模型可见集整体移除"，
#     模型根本拿不到 edit/write/patch。
# 两者并存：配置层挡"意图"，插件层挡"绕过配置直接调工具"。
#
# 路径按"整条规范化路径"匹配，通配 * 可跨 /，故 * 前缀覆盖任意项目根
# （书项目在 E:\workspace\webnovel2 下，不在仓库内）。
PROTECTED_SUFFIXES = (
    ".webnovel/state.json",
    ".webnovel/index.db",
    ".webnovel/vectors.db",
    ".webnovel/memory_scratchpad.json",
    ".story-system/events/",
    ".story-system/master_setting.json",
)


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
    if "edit" in actions:
        # 排在 allow 之后：规则顺序即优先级，最后命中的生效，所以这些 deny
        # 必须压过上面那条 edit 的 allow。
        lines.append(
            "  # SSOT 受保护文件只能经 CLI 写入（chapter-commit 等）。"
            "deny 会把 edit/write/patch 从模型可见集整体移除，"
            "比 write-guard.js 的运行时拒绝更靠前；两者并存。"
        )
        lines += [
            f'  - action: edit\n    resource: "*{suffix}"\n    effect: deny'
            for suffix in PROTECTED_SUFFIXES
        ]
    return "\n".join(lines)


def _already_current(fm: str, tools: list[str]) -> bool:
    """已迁移且渲染结果与当前一致——此时才跳过。

    不能只看 "permissions:" 是否存在：本脚本的渲染规则会演进（例如给需要
    edit 的 agent 追加 SSOT 受保护路径的 deny），若沿用"见到 permissions
    就跳过"，规则更新后重跑不会生效，且没有任何提示。
    """
    if "permissions:" not in fm:
        return False
    return render_permissions(tools) in fm


def parse_allowed_actions(fm: str) -> list[str]:
    """从已迁移的 permissions 里读回被显式 allow 的动作。

    tools: 块被替换掉之后，这是唯一能还原"该 agent 需要哪些工具"的来源。
    只认显式 `effect: allow` 的规则——deny-all 与 external_directory: ask
    都不算。
    """
    allowed = set()
    for action, resource, effect in re.findall(
        r'^\s*- action:\s*"?([^"\n]+?)"?\s*\n\s*resource:\s*"?([^"\n]+?)"?\s*\n'
        r'\s*effect:\s*(\w+)\s*$',
        fm, re.M,
    ):
        if effect == "allow" and resource == "*" and action != "*":
            allowed.add(action)
    return sorted(allowed)


def _v1_tools_for(allowed: list[str]) -> list[str]:
    """把 V2 动作名反推回 V1 工具名，供 render_permissions 复用同一套渲染。"""
    inverse = {v: k for k, v in _ACTION.items() if k not in ("write", "edit")}
    out = []
    for action in allowed:
        out.append(inverse.get(action, action))
    if "edit" in allowed:
        out.append("write")
    return out


def convert(text: str) -> str:
    m = re.search(r"^---\s*\n(.*?)\n---", text, re.S)
    if not m:
        raise ValueError("缺少 frontmatter")
    fm = m.group(1)
    tools = parse_tools(fm)
    if not tools:
        if "permissions:" not in fm:
            raise ValueError("未找到 tools: 块")
        # 已迁移：从 allowlist 反推需要的 V1 工具名后重新渲染。
        tools = _v1_tools_for(parse_allowed_actions(fm))
    if _already_current(fm, tools):
        return text  # 幂等
    block = render_permissions(tools) + "\n"
    if "permissions:" in fm:
        # 规则演进后的就地重写：替换掉整个旧的 permissions 块。
        fm = re.sub(r"^permissions:\s*$.*?(?=^\S|\Z)",
                    lambda _m: block, fm, flags=re.M | re.S)
        return text[:m.start(1)] + fm + text[m.end(1):]
    # 用 lambda 替换：渲染出的文本含 E:\workspace 这类反斜杠，直接当 repl 会
    # 被 re 当转义序列处理（re.error: bad escape \w）。
    fm = re.sub(r"^tools:\s*$.*?(?=^\S|\Z)", lambda _m: block, fm, flags=re.M | re.S)
    return text[:m.start(1)] + fm + text[m.end(1):]


def main() -> int:
    changed = 0
    for path in sorted(AGENTS_DIR.glob("*.md")):
        original = path.read_text(encoding="utf-8")
        updated = convert(original)
        if updated != original:
            path.write_text(updated, encoding="utf-8")
            print(f"migrated: {path.name}")
            changed += 1
        else:
            print(f"skipped (already current): {path.name}")
    if changed:
        print(f"✅ {changed} 个 agent 更新")
    return 0


if __name__ == "__main__":
    sys.exit(main())
