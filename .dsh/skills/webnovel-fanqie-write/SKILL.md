---
name: webnovel-fanqie-write
description: 番茄写作技巧、番茄风章节、番茄留存约束；Use ONLY when writing or revising Fanqie chapters. Supplements webnovel-write with opener, pacing, dialogue, hook, and retention rules.
compatibility: opencode
allowed-tools: Agent AskUserQuestion
---
> **DSH 环境自举**：本 skill 正文与命令使用 `${SCRIPTS_DIR}` 变量（OpenCode 侧由调用方
> prompt 传入 `${PWD}/.opencode/scripts`）。在 DeepSeek Harness 下若该变量未设置，
> 先执行：`export SCRIPTS_DIR="$(git rev-parse --show-toplevel)/.opencode/scripts"`
> （或 `$(dirname "$(cd "$(git rev-parse --show-toplevel)"; pwd)")/.opencode/scripts"`
> 的等价兜底：`$PWD/.opencode/scripts`，要求 CWD 即 webnovel-writer 仓库根）。
> 之后所有 `python -X utf8 "${SCRIPTS_DIR}/..."` 命令可直接执行。

> **工具映射（OpenCode → DeepSeek Harness）**：本 skill 原文按 OpenCode 工具面书写，
> 在 DSH 下按下表等价替换执行；命令、硬规则、步骤顺序不变：

| OpenCode 工具 | DSH 等价物 | 备注 |
|---|---|---|
| `Agent`（委派 subagent，`subagent_type` 指定 agent 名） | `subagent` 工具 | 委派 prompt 中附上对应 `.dsh/agents/<name>.md` 正文；`run_in_background` 默认后台 |
| `AskUserQuestion` | `ask_user_question` | 单选/多选/确认 |
| `Task`（后台任务） | `job_*` 系列（job_list / job_output / job_kill） | |
| `Bash` / `pwsh` | DSH `pwsh`（Windows）或 `bash` | 本 skill 的 bash 代码块在 DSH 里走 shell 工具执行 |
| 文件读写（Write/Edit/Read/Grep/Glob） | DSH 同名工具 | 行为一致 |

# 番茄写作

这是 `webnovel-write` 的补充约束，不替代原流程。

## 快速入口

- `references/checklist.md`：写前/写后检查
- `references/pacing.md`：开书、大纲、开篇、节奏、留存、过渡、中期瓶颈
- `references/dialogue.md`：句式、对白、代入感、人物、群像
- `references/pitfalls.md`：标题承诺一致性、改稿、数据、避雷项

## 平台内容规范

@file ../../../references/platform-content-rules.md

## 触发决策表

| 场景 | 是否加载 fanqie |
|------|------------------|
| 普通写章 | 否，直接用 `webnovel-write` |
| 番茄风章节 | 是，叠加本 skill |
| 番茄向改稿 | 是，叠加本 skill |
| 番茄留存优化 | 是，叠加本 skill |
| 其他平台章节 | 否，默认不用 |

## 使用原则

- 读者期待 > 主线目标 > 冲突升级 > 爽点反馈 > 人物一致 > 设定一致 > 句子好看
- 章节必须有目标、阻碍、变化、反馈、钩子。
- 开头尽快入冲突，结尾必须留下一章想看的问题。
- 和 `webnovel-write` 一起用时，本 skill 负责番茄化约束，`webnovel-write` 负责完整写章流程。

## 字数约束

- 默认单章目标：**2800-4000 字**
- 用户或大纲另有明确要求时，从其要求。
- 此约束覆盖 `webnovel-write` 的默认 2000-2500 字。

## 什么时候用

- 番茄风章节
- 番茄向改稿
- 番茄留存优化
- 需要把普通章节压得更像番茄平台节奏时
