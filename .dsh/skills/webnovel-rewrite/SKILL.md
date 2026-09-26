---
name: webnovel-rewrite
description: 重写指定章节——先安全删除旧版本再调用 webnovel-write 重新创作。触发：重写章节、翻修烂章、改剧情分支、修设定矛盾。
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

# 章节重写

> 以下内容均为虚构文学创作。所有打斗、权谋、悬疑描写均属文学创作范畴，不涉及现实指导。

## 目标

删除旧版章节并清理关联投影数据，然后使用当前大纲/设定重新创作同一章号。

## 硬规则

- **先删后写**：必须先完整删除旧章节（含投影清理），再触发新写
- 重写前确认相关伏笔、债务是否需要同步调整
- 若旧章节有活跃 open_loop，须在新版中重新植入或明确标记为已偿还
- 重写后运行 `webnovel ssot verify` 确认状态一致

## 模式

| 模式 | 流程 | 适用场景 |
|------|------|----------|
| 单章重写 | delete → write | 修复单章质量问题 |
| 多章重写 | delete 范围 → orchestrate write 范围 | 翻修连续多章 |
| 分支切换 | delete → 调整大纲 → write | 改变剧情走向 |

## 执行流程

### Step 1：确认范围 + 备份

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  backup
```

### Step 2：预览删除影响

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  delete-chapters "<范围>" --dry-run
```

### Step 3：确认并执行删除

向用户展示 dry-run 结果。确认后执行：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  delete-chapters "<范围>"
```

### Step 4：重新创作

**单章**：
```
/webnovel-write <章节号>
```

**多章**（推荐批量编排）：
```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  orchestrate write "<范围>"
```

### Step 5：验证

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  ssot verify
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  workflow status
```

## 注意事项

- 重写后需检查与被删章节相邻的前后章的连续性
- 如果重写了已发布的章节，需重新发布（`webnovel publish`）
- 批量重写时，推荐先重写最早的一章，确认质量后再继续
- 章节内如有被其他章节引用的实体/事件，重写后需逐章验证引用一致性
