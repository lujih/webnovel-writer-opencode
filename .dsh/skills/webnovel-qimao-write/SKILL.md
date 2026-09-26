---
name: webnovel-qimao-write
description: 七猫写作技巧、七猫风章节、七猫留存与签约约束；Use ONLY when writing or revising Qimao chapters. Supplements webnovel-write with opener, pacing, hook, expectation, character, and content-audit rules.
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

# 七猫写作

这是 `webnovel-write` 的补充约束，不替代原流程。规则来自七猫作家学院“写作技巧”板块 108 篇官方编辑课程的提炼。

## 快速入口

- `references/checklist.md`：写前/写后/签约前检查
- `references/pacing.md`：开书、大纲、开篇、节奏、过渡、留存、中期瓶颈
- `references/hook-expectation.md`：钩子分层、信息差、期待感、悬念、伏笔
- `references/character.md`：人设、配角、黑化洗白、代入感、对白
- `references/coolpoint.md`：爽感、对比、冲突矛盾、虐点、升级体系
- `references/pitfalls.md`：毒点、崩文、改稿、拒稿模板、题材选择
- `references/audit.md`：七猫内容审核红线（避免章节驳回，男频都市重灾区）

## 平台内容规范

@file ../../../references/platform-content-rules.md

## 触发决策表

| 场景 | 是否加载 qimao |
|------|------------------|
| 普通写章 | 否，直接用 `webnovel-write` |
| 七猫风章节 | 是，叠加本 skill |
| 七猫向改稿 | 是，叠加本 skill |
| 七猫留存优化 | 是，叠加本 skill |
| 七猫签约/过稿开篇 | 是，叠加本 skill |
| 内容是否过审存疑 | 是，必看 `references/audit.md` |
| 其他平台章节 | 否，默认不用 |

## 使用原则

- 读者情绪 > 主线目标 > 期待感（钩子）> 爽点反馈 > 人物一致 > 设定一致 > 句子好看。
- 故事的本质是情绪：说一个故事"平"，平的是情绪，不是事件不够刺激。
- 章节必须有目标、阻碍、变化、反馈、钩子。
- 开头尽快入冲突并点题（紧扣书名+简介），结尾必须留下一章想看的问题。
- 先固人设再推剧情：绝不为套预设桥段而扭曲人物逻辑。
- 升级/上位/打脸都靠"前压后弹"：前期压得越实，后期反弹越爽。
- 和 `webnovel-write` 一起用时，本 skill 负责七猫化约束，`webnovel-write` 负责完整写章流程。

## 字数约束

- 默认单章目标：**2800-4000 字**
- 用户或大纲另有明确要求时，从其要求。
- 此约束覆盖 `webnovel-write` 的默认 2000-2500 字。

## 七猫平台硬约束（与番茄不同处）

- 七猫禁止上架抗战文；禁止带系统/外挂穿越重生到抗战、解放战争、文革等敏感时期。
- 都市法制背景下，主角不可当众杀普通人、警察、记者、保安；目无法纪即毁三观，必被驳回。
- 男频战神/末世/弃少文不得映射真实国家与时事；真实国名、事件、建筑、恐怖组织一律架空虚拟化。
- 内容红线详见 `references/audit.md`，过审存疑必查。

## 什么时候用

- 七猫风章节
- 七猫向改稿
- 七猫留存优化
- 七猫签约/过稿开篇打磨
- 需要把普通章节压得更像七猫平台节奏与审核标准时
