# 命令详解

## Skill 命令（在 OpenCode 中使用）

### `/webnovel-init`

初始化小说项目，生成目录结构、设定模板和状态文件。

产出：

- `.webnovel/state.json`（运行时状态）
- `设定集/`（世界观、力量体系、主角卡、金手指设计、反派设计等）
- `大纲/总纲.md`、`大纲/爽点规划.md`
- `.env.example`（RAG 配置模板）

### `/webnovel-plan [卷号]`

生成卷级规划与章节大纲。

```bash
/webnovel-plan 1
/webnovel-plan 2-3
```

### `/webnovel-write [章号]`

执行完整章节创作流程（`context-agent` 先 research 并生成写作任务书 → 按任务书起草正文 → 审查 → 润色 → 数据落盘）。

```bash
/webnovel-write 1
/webnovel-write 45
```

### `/webnovel-review [范围]`

对已有章节做多维质量审查。

```bash
/webnovel-review 1-5
/webnovel-review 45
```

### `/webnovel-query [关键词]`

查询角色、伏笔、节奏、状态等运行时信息。

```bash
/webnovel-query 萧炎
/webnovel-query 伏笔
```

### `/webnovel-learn [内容]`

从当前会话或用户输入中提取可复用写作模式，写入项目记忆。

```bash
/webnovel-learn "本章的危机钩设计很有效，悬念拉满"
```

产出：`.webnovel/project_memory.json`

### `/webnovel-dashboard`

启动可视化面板，查看项目状态、编辑文风约束、执行批量操作。

```bash
/webnovel-dashboard
```

说明：

- 9 个页面：总览、上下文健康、角色图鉴（含时间线）、审查分析、节奏雷达、伏笔追踪、文档浏览、文风约束（6 Tab）、系统状态（含批量操作）
- 支持亮色/暗色主题切换
- 文风约束编辑：自定义提示词、全局文风、禁止模式、写作技法、章级合同、审查维度
- 批量操作：批量写入、删除（dry-run 预览）
- 前端构建产物已随插件发布，无需本地 `npm build`

### `/webnovel-delete <章号>`

安全删除章节（dry-run 预览 → 确认执行 → 清理投影）。

```bash
/webnovel-delete 12
/webnovel-delete 12 --force
```

### `/webnovel-rewrite <章号>`

重写指定章节，保持章节结构、保留已确认事实。

```bash
/webnovel-rewrite 5
/webnovel-rewrite 5-8
```

### `/webnovel-heal`

批量自动修复章节中的已知问题（伏笔断裂、状态不一致等）。

```bash
/webnovel-heal
```

## 统一 CLI（命令行使用）

所有 CLI 命令的入口都是 `webnovel.py`，格式：

```bash
python -X utf8 "<OPENCODE_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" <子命令> [参数]
```

## Story System 主链

推荐按以下顺序执行：

1. 生成合同

```bash
python -X utf8 "<OPENCODE_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" story-system "玄幻退婚流" --chapter 12 --persist --emit-runtime-contracts --format both
```

2. 提交章节

```bash
python -X utf8 "<OPENCODE_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" chapter-commit \
  --chapter 12 \
  --review-result ".webnovel/tmp/review_results.json" \
  --fulfillment-result ".webnovel/tmp/fulfillment_result.json" \
  --disambiguation-result ".webnovel/tmp/disambiguation_result.json" \
  --extraction-result ".webnovel/tmp/extraction_result.json"
```

3. 检查主链健康

```bash
python -X utf8 "<OPENCODE_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" preflight --format json
```

其中 `.story-system/` 是主链真源，`.webnovel/*` 是投影/read-model。

### 常用工具子命令

| 子命令 | 说明 |
|--------|------|
| `where` | 打印当前解析出的项目根目录 |
| `preflight` | 校验 CLI 环境、脚本路径、项目根和可选依赖（aiohttp）。输出 OK/WARN/ERROR 汇总 |
| `use <路径>` | 绑定当前工作区使用的书项目 |
| `chapter-path <章号>` | 查找指定章节的正文文件路径 |

### 数据模块子命令

| 子命令 | 说明 |
|--------|------|
| `index` | 索引管理（`process-chapter`、`stats` 等） |
| `state` | 状态管理（`render` 渲染 markdown 投影文件） |
| `rag` | RAG 向量索引（`index-chapter`、`stats` 等） |
| `entity` | 实体链接 |
| `context` | 上下文管理 |
| `style` | 风格采样 |
| `migrate` | state.json → SQLite 迁移 |
| `knowledge` | CSV 知识库管理 |
| `checkers` | 审查器配置管理 |
| `delete-chapters` | 安全删除章节（dry-run 预览 → 确认执行 → 清理投影） |
| `orchestrate` | 批量编排（write/heal/nightly 模式） |
| `entity-clean` | 扫描脏实体（`--mark-invalid` 标记无效） |

### 独立工具脚本

| 脚本 | 说明 |
|------|------|
| `data_modules/chapter_rename.py` | 章节文件名编号统一（`第060章` → `第0060章`）。`--dry-run` 预览，`--recursive` 递归子目录 |

### 运维子命令

| 子命令 | 说明 |
|--------|------|
| `status` | 健康报告（`--focus all` / `--focus urgency`） |
| `update-state` | 手动更新状态 |
| `backup` | 备份管理 |
| `archive` | 归档管理 |
| `extract-context` | 提取章节上下文（`--chapter N --format json`） |
| `placeholder-scan` | 扫描大纲/设定中的未补齐占位 |
| `master-outline-sync` | 将规划产物同步回写总纲 |
| `export` | 正文导出（MD/TXT/EPUB/HTML/DOCX/PDF） |
| `publish` | 番茄小说平台发布 |
| `clean-tmp --keep <filename>` | 清理临时文件时保留指定文件 |

### 长期记忆子命令

| 子命令 | 说明 |
|--------|------|
| `memory stats` | 查看总量、分类统计 |
| `memory query` | 按 category/subject/status 过滤查询 |
| `memory dump` | 导出完整 scratchpad 内容 |
| `memory conflicts` | 查看同主键 active 冲突项 |
| `memory bootstrap` | 从 index.db 与 summaries 回填初始长期记忆 |
| `memory update` | 对指定章节结果执行手动映射写入 |
| `project-memory` | 项目记忆管理（读写 `.webnovel/project_memory.json`） |

示例：

```bash
python -X utf8 "<OPENCODE_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" memory stats
python -X utf8 "<OPENCODE_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" memory query --category character_state --subject xiaoyan
```

### Story System 子命令

| 子命令 | 说明 |
|--------|------|
| `story-system "<题材>" --persist` | 写入合同种子（`MASTER_SETTING.json` 等） |
| `story-system "<题材>" --emit-runtime-contracts --chapter N` | 生成运行时合同 + 写前校验 |
| `chapter-commit --chapter N` | 提交章节 commit（可附带 review/fulfillment/disambiguation/extraction 结果） |
| `story-events --chapter N` | 查询指定章节事件 |
| `story-events --health` | 事件链健康检查 |
| `memory-contract` | 记忆合同管理 |
| `review-pipeline --chapter N --review-results <file>` | 审查流水线 |

示例：

```bash
python -X utf8 "<OPENCODE_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" story-system "玄幻退婚流" --persist
python -X utf8 "<OPENCODE_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" chapter-commit --chapter 12 --review-result .webnovel/tmp/review.json
python -X utf8 "<OPENCODE_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" story-events --health
```

产物：

- `story-system --persist` → `.story-system/MASTER_SETTING.json`
- `--emit-runtime-contracts` → `volumes/*.json` 与 `reviews/*.review.json`
- `chapter-commit` → `commits/*.commit.json`
- `story-events` → 读取 `events/*.events.json` 或 `index.db.story_events`

### 架构管理子命令

| 子命令 | 说明 |
|--------|------|
| `ssot verify` | 校验 state.json 与事件日志的一致性 |
| `ssot rebuild` | 从事件日志重建所有投影。**保留非事件字段**（`project_info`/`chapter_meta`/`review_checkpoints`/`world_settings`/`strand_tracker`/`entities`/`entity_state`/`schema_version`/`disambiguation_warnings`/`disambiguation_pending`，以及 `protagonist_state` 的 dict 子键补全）——这些来自 init 初始化与审查流水线，不被事件日志覆盖。事件字段（如 `progress.current_chapter`、`progress.chapter_status`（字符串值 + `progress.chapter_status_seq` 事件序）、`entities_v3`、`plot_threads.foreshadowing`）以重建结果为准 |
| `ssot events` | 查看完整事件历史 |
| `workflow checkpoint --chapter N --stage STAGE` | 记录章节工作流检查点 |
| `workflow status` | 查看所有章节阶段状态 |
| `workflow interrupted` | 查找中断未完成的章节 |
| `override add` | 记录世界规则变更 |
| `override list` | 查看生效的覆盖规则 |
| `override context` | 生成当前章节的覆盖提示 |

示例：

```bash
python -X utf8 "<OPENCODE_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" ssot verify
python -X utf8 "<OPENCODE_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" ssot rebuild
python -X utf8 "<OPENCODE_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" workflow status
python -X utf8 "<OPENCODE_ROOT>/scripts/webnovel.py" --project-root "<PROJECT_ROOT>" override list
```

### observer_settler.py CLI

`observer_settler.py` 是事实提取脚本，位于 `data_modules/` 目录。CLI 参数：

| 参数 | 说明 |
|------|------|
| `--raw-facts` | 输出原始事实数据 |
| `--project-root <路径>` | 指定项目根目录 |
| `--chapter <章号>` | 指定处理章节 |
| `--output <路径>` | 输出文件路径 |
