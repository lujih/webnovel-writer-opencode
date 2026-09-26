---
name: webnovel-query
description: 查询项目设定、角色、力量体系、势力、伏笔等信息。支持紧急度分析与金手指状态查询。
compatibility: opencode
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

# Information Query Skill

## Use when

用户询问关于故事设定、角色、力量体系、势力、伏笔、金手指、节奏等项目内信息时触发。

## 项目根保护

```bash
export WORKSPACE_ROOT="${PWD}"
export SCRIPTS_DIR="${PWD}/.opencode/scripts"
export SKILL_ROOT="${PWD}/.opencode/skills/webnovel-query"
export PROJECT_ROOT="$(python "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"
test -n "$PROJECT_ROOT" && test -f "${PROJECT_ROOT}/.webnovel/state.json" || { echo "❌ PROJECT_ROOT 解析失败"; exit 1; }
```

- `PROJECT_ROOT` 必须包含 `.webnovel/state.json`
- **禁止**在 `.opencode/` 下读取或写入项目文件

## 查询类型识别

| 关键词 | 查询类型 | 数据源 |
|--------|---------|--------|
| 角色/主角/配角 | 标准查询 | 主角卡.md, 角色库/ |
| 境界/筑基/金丹 | 标准查询 | 力量体系.md |
| 宗门/势力/地点 | 标准查询 | 世界观.md |
| 伏笔/紧急伏笔 | 伏笔分析 | state.json + foreshadowing.md |
| 金手指/系统 | 金手指状态 | state.json |
| 节奏/Strand | 节奏分析 | state.json + strand-weave-pattern.md |
| 标签/实体格式 | 格式查询 | tag-specification.md |
| 某角色在第N章时/历史状态/时间点状态 | 时序查询 | knowledge query-entity-state / query-relationships |
| 规则变更/设定演进/Override | Override 查询 | webnovel override context |

## 引用加载策略

先识别查询类型，再按需加载。路径说明：`references/` 指 skill 私有 `skills/webnovel-query/references/`；`../../references/` 指共享 references。

| 查询类型 | Reference | 实际路径 |
|---------|-----------|---------|
| 所有查询 | 数据流规范 | `${SKILL_ROOT}/references/system-data-flow.md` |
| 伏笔分析 | 伏笔分析 | `${SKILL_ROOT}/references/advanced/foreshadowing.md` |
| 节奏分析 | Strand 模式 | `${SKILL_ROOT}/../../references/shared/strand-weave-pattern.md` |
| 格式查询 | 标签规范 | `${SKILL_ROOT}/references/tag-specification.md` |

不得同时加载两个以上 L2 文件，除非用户请求明确跨多类型。

## 查询流程

1. **识别查询类型**：根据用户关键词匹配上表
2. **加载参考**：只加载该类型需要的 reference
3. **加载主链上下文**：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" memory-contract load-context --chapter {chapter_num}
```

4. **按优先级查询数据源**（写前真源 → 写后真源 → 投影层）：
   1. `.story-system/MASTER_SETTING.json` - 全书主设定（题材、调性、核心禁忌）
   2. `.story-system/volumes/*.json` - 卷级合同（本卷目标、节奏策略）
   3. `.story-system/chapters/*.json` - 章级合同（本章焦点、动态上下文）
   4. latest accepted `.story-system/commits/chapter_XXX.commit.json` - 写后事实（已发布章节的定稿状态）
   5. `memory-contract load-context` - 记忆编排结果（长期记忆、伏笔、时间线）
   6. `.webnovel/state.json` / `index.db` - 投影层（仅 fallback/read-model，类比网文后台的"角色卡"、"章节列表"）
   
   **优先级说明**：
   - 写前真源（1-3）：作者开写前必须遵守的"大纲、设定、禁区"
   - 写后真源（4）：已发布章节的"定稿状态"，不可篡改
   - 投影层（5-6）：从写后真源自动生成的"查询视图"，方便快速检索
5. **确认上下文充足**：查询类型已识别 + 主链合同 / latest commit 已加载
6. **执行查询**：按类型检索对应数据源。若为时序查询，使用以下命令：

```bash
# 查询某实体在指定章节时的状态
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" knowledge query-entity-state --entity "{entity_id}" --at-chapter {N}

# 查询某实体在指定章节时的所有关系
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" knowledge query-relationships --entity "{entity_id}" --at-chapter {N}

# 查询当前生效的世界规则变更（Override Contracts）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" override context --chapter {N}
```

7. **格式化输出**：按下方模板输出

## 输出格式

```markdown
# 查询结果：{关键词}

## 概要
- **匹配类型**: {type}
- **数据源**: state.json + 设定集 + 大纲
- **匹配数量**: X 条

## 详细信息
{结构化数据，含文件路径和行号}

## 数据一致性检查
{state.json 与静态文件的差异，若无差异则省略}
```

## 边界与失败恢复

- 只读操作，不修改任何项目文件
- 若数据源缺失，明确告知用户缺少什么文件
- 若查询无匹配，返回空结果并建议检查范围
- 若 `.story-system/` 合同与 accepted commit 缺失，必须显式说明当前查询已降级到 legacy fallback
