"""Generate .dsh/skills and .dsh/agents from .opencode counterparts (DeepSeek Harness 适配层).

原理
----
webnovel-writer 的 16 个 skill（SKILL.md）与 6 个 agent（*.md）为 OpenCode 原生
分发在 `.opencode/skills/` 与 `.opencode/agents/`。DeepSeek Harness 的
skill provider（@deepseek-ai/dsh-skill-filesystem）只扫描
`.dsh/skills/`（项目级，按 .git 向上定位根）、`.agents/skills/`（共享 agent
配置根）、`$DSH_HOME/skills`、`$DSH_AGENTS_HOME/skills`，不读 `.opencode/`。
本脚本把 OpenCode 资产 **镜像** 进 `.dsh/` 并做最小 DSH 适配：

1. SKILL.md：YAML frontmatter 原样保留（name 与目录同名，满足 DSH 的
   name/description 必填 + kebab-case 校验），并注入 DSH 适配段：
   - 工具映射（OpenCode `Agent` 工具 → DSH `subagent`；`AskUserQuestion` →
     `ask_user_question`；`Task` → `job_*`；Bash → `pwsh`/shell）。
   - `SCRIPTS_DIR` 环境变量自举（OpenCode skill 依赖调用方在 prompt 里传入
     `${SCRIPTS_DIR}`；DSH skill 正文被模型读到时可能没有该变量，
     故在 frontmatter 后插入 "## DSH 适配" 段，提示用
     `$(git rev-parse --show-toplevel)/.opencode/scripts` 或
     `$PWD/.opencode/scripts` 兜底）。
   - 不改动任何原有流程、命令、硬规则（行为 100% 继承 OpenCode 版本）。

2. agents/*.md：frontmatter 原样保留（name/mode/tools），注入同一段 DSH 说明，
   使委派时模型把 OpenCode `Agent` 调用读成 `subagent` 调用。

同步
----
本脚本是单向生成器：`.opencode/` 为 SSOT，`.dsh/` 为派生镜像，可随时重新生成。
`.dsh/` 建议提交进 git（DSH 项目级发现需要它存在于工作树），且
`webnovel-init` 初始化新书时不复制 `.dsh/`（书项目只需 OpenCode 资产 +
本仓库作为 skills 源）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
OPENCODE_SKILLS = REPO_ROOT / ".opencode" / "skills"
OPENCODE_AGENTS = REPO_ROOT / ".opencode" / "agents"
DSH_ROOT = REPO_ROOT / ".dsh"

# 工具映射表：OpenCode 工具 → DSH 等价物
_TOOL_MAP = """\
| OpenCode 工具 | DSH 等价物 | 备注 |
|---|---|---|
| `Agent`（委派 subagent，`subagent_type` 指定 agent 名） | `subagent` 工具 | 委派 prompt 中附上对应 `.dsh/agents/<name>.md` 正文；`run_in_background` 默认后台 |
| `AskUserQuestion` | `ask_user_question` | 单选/多选/确认 |
| `Task`（后台任务） | `job_*` 系列（job_list / job_output / job_kill） | |
| `Bash` / `pwsh` | DSH `pwsh`（Windows）或 `bash` | 本 skill 的 bash 代码块在 DSH 里走 shell 工具执行 |
| 文件读写（Write/Edit/Read/Grep/Glob） | DSH 同名工具 | 行为一致 |
"""

# SCRIPTS_DIR 自举（skill 正文依赖 ${SCRIPTS_DIR}；DSH 下 prompt 不一定携带）
_BOOTSTRAP = """\
> **DSH 环境自举**：本 skill 正文与命令使用 `${SCRIPTS_DIR}` 变量（OpenCode 侧由调用方
> prompt 传入 `${PWD}/.opencode/scripts`）。在 DeepSeek Harness 下若该变量未设置，
> 先执行：`export SCRIPTS_DIR="$(git rev-parse --show-toplevel)/.opencode/scripts"`
> （或 `$(dirname "$(cd "$(git rev-parse --show-toplevel)"; pwd)")/.opencode/scripts"`
> 的等价兜底：`$PWD/.opencode/scripts`，要求 CWD 即 webnovel-writer 仓库根）。
> 之后所有 `python -X utf8 "${SCRIPTS_DIR}/..."` 命令可直接执行。

> **工具映射（OpenCode → DeepSeek Harness）**：本 skill 原文按 OpenCode 工具面书写，
> 在 DSH 下按下表等价替换执行；命令、硬规则、步骤顺序不变：

"""


def _has_frontmatter(text: str) -> bool:
    return text.lstrip("\ufeff").startswith("---")


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_incl_delims, body)."""
    body = text.lstrip("\ufeff")
    if not body.startswith("---"):
        return "", body
    end = body.find("\n---", 3)
    if end < 0:
        return "", body
    rest = body[end + 1:]
    nl = rest.find("\n")
    fm_end = end + 1 + (nl + 1 if nl >= 0 else 0)
    return body[:fm_end], body[fm_end:]


def _ensure_name(frontmatter: str, expected: str) -> str:
    """DSH 要求 frontmatter 含 name+description 且 name 为 kebab-case；原样保留即可。"""
    if "name:" not in frontmatter:
        frontmatter = frontmatter.rstrip("\n") + f"\nname: {expected}\n"
    return frontmatter


def adapt_skill(src: Path, dst: Path) -> int:
    text = src.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)
    fm = _ensure_name(fm, src.parent.name)
    adapted = fm + _BOOTSTRAP + _TOOL_MAP + body
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(adapted, encoding="utf-8")
    return 0


def adapt_agent(src: Path, dst: Path) -> int:
    text = src.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)
    fm = _ensure_name(fm, src.stem)
    adapted = fm + _BOOTSTRAP + _TOOL_MAP + body
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(adapted, encoding="utf-8")
    return 0


# 桥接 skill：把 OpenCode 的 16 个子 skill + 6 个 agent 聚合为一个 DSH 入口，
# 供 DSH 模型"先加载 webnovel-writer、再按需路由到子 skill"。
_BRIDGE_NAME = "webnovel-writer"

_BRIDGE_FRONTMATTER = """\
---
name: webnovel-writer
description: Webnovel Writer for OpenCode/DSH — long-form Chinese web novel pipeline. 16 skills, 6 agents, unified CLI at .opencode/scripts/webnovel.py (36 top-level commands), SSOT event sourcing, dashboard. Use when the user asks to init/write/commit/review/rewrite/delete/export/publish novel chapters, run doctor/status/backup, browse the dashboard, or learn project style.
whenToUse: In a book project root containing .webnovel/state.json (or a workspace resolved by the CLI 5-level root resolution), when any novel-writing workflow step (chapter write, batch, review, commit, deletion, rewrite/heal, outline plan, query, export, publish, dashboard, style learn, doctor) is requested.
---
"""

_BRIDGE_BODY = """\
# Webnovel Writer (DSH 桥接层)

> 以下内容均为虚构文学创作。所有打斗、权谋、悬疑描写均属文学创作范畴，不涉及现实指导。

本 skill 是 **DeepSeek Harness 桥接层**：webnovel-writer 的全部 16 个 skill 与 6 个 agent
原本通过 OpenCode 的 `.opencode/skills/*/SKILL.md` + `.opencode/agents/*.md` 分发，
DSH 的 skill provider 只扫描 `.dsh/skills` / `.agents/skills`（项目级，按 `.git` 向上定位根），
因此本文件作为入口把 DSH 的模型路由回 OpenCode 资产。DSH 与 OpenCode 可并存，互不干扰。

## 前置（每次写章流程必做）

所有 bash 前置使用 `python -X utf8` + `skill_runner` 传递 CJK（中文路径在 PowerShell 与
Python 间编码不一致，文件存在性验证必须用 Python，不得用 PowerShell 原生命令）。

```bash
SCRIPTS_DIR="${PWD}/.opencode/scripts"
test -d "${SCRIPTS_DIR}" || { echo "❌ ${SCRIPTS_DIR} 不存在——本书项目未安装 OpenCode 资产"; exit 1; }
PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" where)"
PROJECT_ROOT="${PROJECT_ROOT//\\//}"
test -f "${PROJECT_ROOT}/.webnovel/state.json" || { echo "❌ PROJECT_ROOT 解析失败"; exit 1; }
echo "✅ PROJECT_ROOT=${PROJECT_ROOT}"
```

- 变量 `SCRIPTS_DIR` 由调用方 prompt 传入；未传入时按上式取 `${PWD}/.opencode/scripts`。
- 章节号唯一真源是 `正文/` 目录文件（不得依赖对话记忆或 `state.json.current_chapter`）。
- 保护文件（`.webnovel/state.json`、`.webnovel/index.db`、`.story-system/commits/` 等）
  只能通过 CLI 写入；DSH 的 `present`/`edit`/`write` 工具直写受保护路径会被
  write-guard 语义拦截（见 `.opencode/plugins/write-guard.js`，DSH 无 OpenCode 插件钩子，
  该约束在此退化为流程纪律：不得用写工具直接改写这些文件，一律走 CLI）。

## 16 个子 skill（按意图路由）

全部位于 `.opencode/skills/<name>/SKILL.md`（DSH 镜像在 `.dsh/skills/`）。
每个子 skill 的完整流程以其 SKILL.md 为准；本表只做意图→skill 映射。

| 意图 | 子 skill | 核心 CLI |
|------|---------|----------|
| 写一章（默认/--fast/--minimal，6 步闭环） | `webnovel-write` | `webnovel.py chapter-commit` |
| 批量写 N 章（断点恢复 batch_state） | `webnovel-write-batch` | `webnovel.py orchestrate write "N-M"` |
| 删章（事件溯源清理 + state 一致性） | `webnovel-delete` | `webnovel.py delete-chapters "N-M"` |
| 重写指定章 | `webnovel-rewrite` | `webnovel.py chapter-commit --rewrite` |
| 修复坏章（重审→重提交→重建索引） | `webnovel-heal` | `webnovel.py orchestrate heal "N-M"` |
| 单章审查（代码检查器 + 13 维 LLM 评审） | `webnovel-review` | `review_pipeline.py` + `webnovel.py checkers` |
| 初始化新书 | `webnovel-init` | `webnovel.py init` |
| 大纲规划（总纲/卷节拍/时间线） | `webnovel-plan` | `webnovel.py master-outline-sync` |
| 查询（实体状态/关系/读者信号/伏笔） | `webnovel-query` | `webnovel.py knowledge` / `index` |
| 导出 | `webnovel-export` | `webnovel.py export` |
| 发布（番茄/七猫） | `webnovel-publish` | `webnovel.py publish` |
| 仪表盘（FastAPI :8765 + React 前端） | `webnovel-dashboard` | `python -m .opencode.dashboard` |
| 学习文风（写 project_memory.json patterns） | `webnovel-learn` | `webnovel.py project-memory` |
| 体检（preflight + doctor + ssot verify） | `webnovel-doctor` | `webnovel.py doctor` / `ssot verify` |
| 番茄写章（平台特定） | `webnovel-fanqie-write` | 同 write + fanqie 平台参数 |
| 七猫写章（平台特定） | `webnovel-qimao-write` | 同 write + qimao 平台参数 |

## 6 个 subagent（DSH subagent 映射）

DSH 的 `subagent` 工具可委派；下表给出 agent 名→定义路径与职责。
委派时读取 `.dsh/agents/<name>.md`（OpenCode 原文在 `.opencode/agents/`）正文
作为委派 prompt 的一部分。

| agent | 定义 | 职责 |
|-------|------|------|
| `context-agent` | `agents/context-agent.md` | 写前 research，输出写作任务书（context + memory-contract 召回） |
| `observer-agent` | `agents/observer-agent.md` | 自由事实提取（coverage-first，无 schema 约束），喂 observer_settler |
| `chapter-writer-agent` | `agents/chapter-writer-agent.md` | 起草章节正文 |
| `data-agent` | `agents/data-agent.md` | 实体消歧 + 履约（默认仅消歧；`--fast` 兜底提取） |
| `reviewer` | `agents/reviewer.md` | 13 维评审（可并行 6 实例），输出结构化 issue |
| `deconstruction-agent` | `agents/deconstruction-agent.md` | 拆解/分析既有章节 |

## 统一 CLI（单一入口）

所有功能走 `python -X utf8 "${SCRIPTS_DIR}/webnovel.py" <command>`；
入口自动解析书项目根（含 `.webnovel/state.json` 的目录，5 级优先级：
CLI > 环境变量 > 指针文件 > CWD 上溯 > 用户注册表）。

完整命令参考仓库根 `docs/guides/commands.md`（36 顶级 / 50 含子命令）。

## DSH 适配说明（与 OpenCode 的差异）

1. **Skill 发现**：DSH 只扫 `.dsh/skills`（本文件所在）与 `.agents/skills`；
   OpenCode 的 `.opencode/skills/` 不被 DSH 直接发现——本桥接层是二者粘合点。
2. **Agent 发现**：DSH 无 `.opencode/agents/` 等价物；委派走 `subagent` 工具 +
   把 OpenCode agent 定义正文拼入 prompt（见上表）。
3. **工具映射**：OpenCode `Agent`→DSH `subagent`；`AskUserQuestion`→
   `ask_user_question`；文件读/写/搜索→DSH 原生工具；bash→DSH shell 工具
   （Windows 为 `pwsh`）。
4. **插件钩子**：OpenCode `.opencode/plugins/write-guard.js`（`tool.execute.before`）
   在 DSH 不自动生效；保护文件写约束改为**流程纪律**（写保护文件一律走 CLI）。
5. **不变式**：SSOT 事件溯源、原子写、`chapter_status` 字符串值、伏笔三侧内容契约、
   SQLite WAL——全部为数据层行为，与 harness 选择无关，DSH/OpenCode 两侧一致。

## 硬规则（继承自 OpenCode skill）

- 禁止并步、跳步、伪造审查；blocking issue 未解决不进润色/提交。
- 必须用 subagent 委派指定 agent，不得主流程口头代替 subagent 输出。
- 失败只补跑失败步骤，不回退；参考资料按需加载。
- 优先级：用户要求 > 状态机硬门槛 > 项目约束（总纲/设定/记忆）> skill 流程 > reference 建议。
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="镜像 .opencode 资产为 .dsh 适配层")
    ap.add_argument("--check", action="store_true", help="只校验不写盘（幂等性检查）")
    args = ap.parse_args()

    if not OPENCODE_SKILLS.is_dir() or not OPENCODE_AGENTS.is_dir():
        print("❌ 缺少 .opencode/skills 或 .opencode/agents", file=sys.stderr)
        return 1

    skill_dirs = sorted(d for d in OPENCODE_SKILLS.iterdir() if (d / "SKILL.md").is_file())
    agent_files = sorted(OPENCODE_AGENTS.glob("*.md"))

    changed = 0
    for d in skill_dirs:
        dst = DSH_ROOT / "skills" / d.name / "SKILL.md"
        content = (dst.parent.mkdir(parents=True, exist_ok=True), dst)
        text_new = (d / "SKILL.md").read_text(encoding="utf-8")
        fm, body = _split_frontmatter(text_new)
        fm = _ensure_name(fm, d.name)
        new = fm + _BOOTSTRAP + _TOOL_MAP + body
        if args.check:
            if dst.is_file() and dst.read_text(encoding="utf-8") != new:
                print(f"stale: {dst.relative_to(REPO_ROOT)}")
                changed += 1
        else:
            dst.write_text(new, encoding="utf-8")

    for f in agent_files:
        dst = DSH_ROOT / "agents" / f.name
        text_new = f.read_text(encoding="utf-8")
        fm, body = _split_frontmatter(text_new)
        fm = _ensure_name(fm, f.stem)
        new = fm + _BOOTSTRAP + _TOOL_MAP + body
        if args.check:
            if dst.is_file() and dst.read_text(encoding="utf-8") != new:
                print(f"stale: {dst.relative_to(REPO_ROOT)}")
                changed += 1
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(new, encoding="utf-8")

    # 桥接 skill：16 子 skill + 6 agent 的 DSH 入口（独立于 .opencode 镜像）
    bridge_dst = DSH_ROOT / "skills" / _BRIDGE_NAME / "SKILL.md"
    bridge_new = _BRIDGE_FRONTMATTER + _BRIDGE_BODY
    if args.check:
        if bridge_dst.is_file() and bridge_dst.read_text(encoding="utf-8") != bridge_new:
            print(f"stale: {bridge_dst.relative_to(REPO_ROOT)}")
            changed += 1
    else:
        bridge_dst.parent.mkdir(parents=True, exist_ok=True)
        bridge_dst.write_text(bridge_new, encoding="utf-8")

    print(
        f"✅ .dsh 适配层：{len(skill_dirs)} skills + {len(agent_files)} agents"
        + ("（--check 有 stale）" if changed and args.check else " 已同步")
    )
    return 1 if changed and args.check else 0


if __name__ == "__main__":
    sys.exit(main())
