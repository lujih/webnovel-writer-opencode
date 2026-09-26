---
name: webnovel-learn
description: 从当前会话提取成功模式并写入 project_memory.json
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

# /webnovel-learn

## Project Root Guard（必须先确认）

- 必须在项目根目录执行（需存在 `.webnovel/state.json`）
- 使用统一入口解析项目根，避免写错目录：

```bash
export WORKSPACE_ROOT="${PWD}"
export SCRIPTS_DIR="${PWD}/.opencode/scripts"
export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"
test -n "$PROJECT_ROOT" && test -f "${PROJECT_ROOT}/.webnovel/state.json" || { echo "❌ PROJECT_ROOT 解析失败"; exit 1; }
```

## 目标
- 提取可复用的写作模式（钩子/节奏/对话/微兑现等）
- 追加到 `.webnovel/project_memory.json`

## 执行流程
1. 读取 `"$PROJECT_ROOT/.webnovel/state.json"`，获取当前章节号（progress.current_chapter）
2. 解析用户输入，归类 pattern_type（hook/pacing/dialogue/payoff/emotion/format/other）
3. 必须调用脚本写入，不得手写或拼接 JSON：

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" project-memory add-pattern \
  --pattern-type "{pattern_type}" \
  --description "{用户输入或提炼后的完整描述}" \
  --category "{分类，可空}" \
  --importance "{high|medium|low}"
```

脚本会自动读取/初始化 `.webnovel/project_memory.json`，并用 JSON 序列化写回，自动转义英文双引号、换行等字符。

## 约束
- 不删除旧记录，仅追加
- 追加前扫描已有 patterns，`pattern_type` + `description` 完全相同则跳过
- 禁止使用 `Write` 或手工编辑 `.webnovel/project_memory.json`

## 成功标准

- `project_memory.json` 存在且格式合法
- 新 pattern 已追加到 `patterns` 数组
- 输出包含 `status: success` 和完整 `learned` 对象

## 失败恢复

| 故障 | 恢复方式 |
|------|---------|
| `project_memory.json` 不存在 | 自动初始化 `{"patterns": []}` 后继续 |
| JSON 解析失败 | 不写入脏数据，告知用户文件损坏并建议手动修复 |
| `state.json` 缺失导致无法获取章节号 | 使用 `source_chapter: null`，不阻断 |
| 用户输入无法归类 | 使用 `pattern_type: "other"`，不阻断 |
