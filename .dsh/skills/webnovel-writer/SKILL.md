---
name: webnovel-writer
description: Webnovel Writer for OpenCode/DSH — long-form Chinese web novel pipeline. 16 skills, 6 agents, unified CLI at .opencode/scripts/webnovel.py (36 top-level commands), SSOT event sourcing, dashboard. Use when the user asks to init/write/commit/review/rewrite/delete/export/publish novel chapters, run doctor/status/backup, browse the dashboard, or learn project style.
whenToUse: In a book project root containing .webnovel/state.json (or a workspace resolved by the CLI 5-level root resolution), when any novel-writing workflow step (chapter write, batch, review, commit, deletion, rewrite/heal, outline plan, query, export, publish, dashboard, style learn, doctor) is requested.
---
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
PROJECT_ROOT="${PROJECT_ROOT//\//}"
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
