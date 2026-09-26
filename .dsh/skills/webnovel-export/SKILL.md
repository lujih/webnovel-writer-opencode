---
name: webnovel-export
description: 将网文正文导出为 Markdown/TXT/EPUB/HTML/DOCX/PDF 格式。立即使用此 skill 当用户说：导出、导出小说、导出章节、生成电子书、导出 TXT、导出 Markdown、导出 EPUB、导出 HTML、导出 Word、导出 PDF、下载小说、输出正文。
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

# 正文导出

## 目标

将网文正文导出为多种格式，便于发布或存档。导出是只读操作，不修改项目文件。

## 支持格式

| 格式 | 扩展名 | 依赖 | 说明 |
|------|--------|------|------|
| Markdown | `.md` | 无 | 原文拼接，适合存档 |
| TXT | `.txt` | 无 | 纯文本，去 Markdown 标记 |
| EPUB | `.epub` | ebooklib | 电子书，带目录 + CSS 排版 |
| HTML | `.html` | 无 | 单文件网页，内嵌 CSS + 目录 |
| DOCX | `.docx` | python-docx | Word 文档，中文排版（首行缩进） |
| PDF | `.pdf` | weasyprint（可选） | 印刷品质，自动分页 |

## 环境设置

```bash
export SCRIPTS_DIR="${PWD}/.opencode/scripts"
test -d "${SCRIPTS_DIR}" || { echo "错误: 未找到 ${SCRIPTS_DIR}，请确保当前目录是 webnovel-writer 仓库根目录"; exit 1; }

export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PWD}" where)"
test -n "$PROJECT_ROOT" && test -f "${PROJECT_ROOT}/.webnovel/state.json" || { echo "错误: PROJECT_ROOT 解析失败，请用 --project-root 显式指定"; exit 1; }
echo "项目路径: ${PROJECT_ROOT}"

test -d "${PROJECT_ROOT}/正文" || { echo "错误: ${PROJECT_ROOT}/正文/ 不存在，无章节可导出"; exit 1; }
```

## 执行流程

### Step 0：确认参数

若用户指令未明确包含以下参数，**必须先询问用户**：

| 参数 | 何时询问 | 选项 |
|------|---------|------|
| 格式 | 用户未指定导出格式 | Markdown / TXT / EPUB / HTML / DOCX / PDF |
| 范围 | 用户未指定章节范围 | 全部 / 指定范围（1-50）/ 指定卷 |
| 书名 | 未指定且需元数据（EPUB/PDF/HTML） | 默认项目目录名 |
| 作者 | EPUB/DOCX/PDF 未指定 | — |
| CSS | 用户提到"排版""样式" | 默认内置中文排版 CSS |

**明确指令示例**（直接执行）：
- "导出为 EPUB" → 格式明确
- "导出第 1-50 章为 DOCX" → 格式和范围明确
- "导出全部为 PDF" → 格式和范围明确

**模糊指令示例**（必须询问）：
- "导出小说" → 格式不明
- "导出" → 所有参数不明
- "下载小说" → 同上

推荐一次性告知所有格式让用户选择，减少来回。

### Step 1：列出章节

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" export list
```

确认章节数量和范围。

### Step 2：导出

```bash
# Markdown
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" export export --format md

# TXT
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" export export --format txt

# EPUB（需 ebooklib）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" export export --format epub --author "作者名"

# HTML（单文件，含目录和内嵌 CSS）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" export export --format html

# DOCX（中文排版：首行缩进、宋体、1.8 倍行距）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" export export --format docx --author "作者名"

# PDF（需 weasyprint，未安装时提示安装）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" export export --format pdf

# 指定范围
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" export export --format docx --range 1-50

# 按卷导出
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" export export --format epub --volume 1

# 自定义样式
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" export export --format html --style "${PROJECT_ROOT}/export-style.css"

# 自定义输出路径
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" export export --format txt --output "${PROJECT_ROOT}/导出/第一卷.txt"
```

### Step 3：验证

```bash
ls -la "${PROJECT_ROOT}/导出/" && echo "导出完成"
```

## 参数说明

| 参数 | 说明 | 适用格式 | 默认值 |
|------|------|---------|--------|
| `--format` | md / txt / epub / html / docx / pdf | 全部 | md |
| `--range` | 1-50 / 1,3,5 / all | 全部 | all |
| `--volume` | 按卷导出（覆盖 --range） | 全部 | — |
| `--output` | 输出文件路径 | 全部 | `导出/{书名}.{ext}` |
| `--title` | 书名（元数据） | 全部 | 项目目录名 |
| `--author` | 作者名 | EPUB/DOCX/PDF | — |
| `--cover` | 封面图路径 | EPUB | `图片/封面/` 下最新 |
| `--cover-size` | 封面裁剪尺寸 | EPUB | 1200x1600 |
| `--style` | 自定义 CSS 路径 | EPUB/HTML/PDF | 内置中文排版 CSS |

## CSS 自定义

项目根目录放置 `export-style.css` 自动生效。默认排版：

- 首行缩进 2em
- 1.8 倍行距
- 章节标题居中
- 场景分隔符 `* * *`
- 思源宋体 / 宋体

## 充分性闸门

- [ ] 正文/ 目录存在且有章节文件
- [ ] 导出命令返回码为 0
- [ ] 导出/ 下有非空输出文件

## 常见问题

| 错误 | 原因 | 解决 |
|------|------|------|
| 未找到章节 | 正文/ 目录不存在 | 先用 /webnovel-write 写章节 |
| EPUB 导出失败 | ebooklib 未安装 | `pip install ebooklib` |
| DOCX 导出失败 | python-docx 未安装 | `pip install python-docx` |
| PDF 导出失败 | weasyprint 未安装 | `pip install weasyprint` |
| 封面未裁剪 | Pillow 未安装 | `pip install Pillow`，不影响导出 |
| HTML 排版异常 | CSS 编码问题 | 确保 `export-style.css` 为 UTF-8 |
