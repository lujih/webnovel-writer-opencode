---
name: webnovel-heal
description: 修复问题章节——诊断、清理脏实体、重审查、重提交。触发：章节断裂、OOC偏离、设定矛盾修复。
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

# 章节修复

> 以下内容均为虚构文学创作。所有打斗、权谋、悬疑描写均属文学创作范畴，不涉及现实指导。

## 目标

诊断并修复问题章节。适用于连续性断裂、OOC 偏离、设定矛盾等场景。非破坏性操作——保留原章节文件，仅重跑审查+提交链。

## 硬规则

- **先诊断后修复**：必须先 `workflow status` 和 `ssot verify` 了解全貌
- 修复前确认已备份（`webnovel backup`）
- heal 模式只重审查+重提交，不重写正文
- 若正文本身有问题，使用 `webnovel-rewrite` 而非 heal

## 执行流程

### Step 1：诊断

```bash
export SCRIPTS_DIR="${PWD}/.opencode/scripts"
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PWD}" where)"
test -n "$PROJECT_ROOT" && test -f "${PROJECT_ROOT}/.webnovel/state.json" || { echo "❌ PROJECT_ROOT 解析失败"; exit 1; }

# 全局健康扫描
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" status --focus all

# 工作流状态
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" workflow status

# 查找中断未完成的章节
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" workflow interrupted

# SSOT 一致性
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" ssot verify
```

### Step 2：清理脏实体

```bash
# 扫描脏实体（dry-run 预览）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" entity-clean

# 标记待修复的脏实体
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" entity-clean --mark-invalid
```

### Step 3：修复指定章节

```bash
# 重审查 + 重提交（不重写正文）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  orchestrate heal "{章节号}"
```

### Step 4：批量修复

```bash
# 批量修复连续多章
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  orchestrate heal "{起始}-{结束}"
```

### Step 5：验证

```bash
# 确认修复结果
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" ssot verify
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" workflow status
```

## 注意事项

- heal 只重跑审查（reviewer）和数据提交（chapter-commit），不改正文
- 若审查发现 blocking issue，需手动修复正文后再重跑
- 若章节文件已损坏或内容严重偏离大纲，使用 `webnovel-rewrite` 替代
- 脏实体清理后需重建 index.db：`webnovel index process-chapter`
