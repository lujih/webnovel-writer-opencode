# `.opencode/scripts` 后端脚本流程梳理

> 2026-06-10 生成。覆盖 `.opencode/scripts/` 下所有 Python 脚本的入口、调度和核心数据流。

## 一、目录总览

```
.opencode/scripts/
├── webnovel.py              ← 统一 CLI 入口（外部调用）
├── skill_runner.py          ← Skill 快速检查工具（结构检查/合同生成/批次验证）
├── project_locator.py       ← 项目根目录解析（5 级优先级）
├── init_project.py          ← 项目初始化（首次运行）
├── gen_manifest.py          ← manifest.json 生成（CI/CD）
├── runtime_compat.py        ← 运行时兼容层
├── security_utils.py        ← 安全检查（禁止直接写 state.json）
├── conftest.py              ← pytest 配置（符号链接）
│
├── data_modules/            ← 核心数据链模块（详见第四节）
├── tests/                   ← 测试
├── references/              ← 知识库引用
└── run_tests.ps1            ← PowerShell 测试入口
```

## 二、双入口架构

项目有 **两个入口点**，服务于不同场景：

### 2.1 `webnovel.py` — 统一 CLI 入口

```
python .opencode/scripts/webnovel.py <command> [args]
```

**角色**：所有用户/脚本/AI Agent 的统一入口。38 个顶层子命令 + 嵌套子命令。

**核心流程**：

```text
webnovel.py (system entry)
  │
  ├─ 1) 解析 --project-root
  │     _resolve_root() → project_locator.resolve_project_root()
  │     优先级: CLI > ENV > 脚本位置 > CWD 搜索 > 指针/注册表
  │
  ├─ 2) 转入 data_modules/webnovel.py
  │     argparse 分发 → 标记 PASSTHROUGH_TOOLS 的转发 / 内联命令
  │
  └─ 3) 执行
        PASSTHROUGH 命令: _run_data_module() → subprocess 调用独立脚本
        内联命令:        直接调用 cmd_* 函数
```

**PASSTHROUGH 命令**（独立脚本，通过 `subprocess` 运行）：
- `index`, `state`, `rag`, `style`, `entity`, `context`, `memory`, `migrate`
- `status`, `update-state`, `backup`, `archive`, `init`, `extract-context`
- `story-system`, `story-events`, `chapter-commit`, `memory-contract`, `project-memory`
- `review-pipeline`, `knowledge`, `placeholder-scan`, `master-outline-sync`
- `export`, `publish`, `orchestrate`, `delete-chapters`, `entity-clean`
- `ssot`, `workflow`, `override`, `checkers`

**内联命令**（直接在 webnovel.py 内处理）：
- `where` — 打印 project_root
- `chapter-path` — 查找章节文件路径
- `preflight` — 运行环境校验
- `use` — 绑定工作区 → 项目

### 2.2 `skill_runner.py` — Skill 内联工具

```
python .opencode/scripts/skill_runner.py <command> [args]
```

**角色**：OpenCode Skill 内部调用的轻量工具。避开 CLI 的 argparse 子进程开销，直接在 Python 内执行。

**命令**：
| 命令 | 功能 | 调用方 |
|------|------|--------|
| `story-system` | 生成章节合同 + 持久化 | webnovel-write SKILL.md |
| `check-structural` | 运行 5 项结构检查 | webnovel-write SKILL.md (prewrite gate) |
| `verify-chapter-files` | 验证章节文件完整性 | webnovel-write SKILL.md |
| `pause-batch` | 批次写入暂停检查 | webnovel-write SKILL.md |
| `mark-step-done` | 标记阶段完成 | webnovel-write SKILL.md |
| `clean-tmp` | 清理临时文件 | 通用维护 |
| `normalize-contracts` | 合同格式归一化 | 数据修复 |
| `compact-memory` | 记忆压合 | webnovel-write SKILL.md |
| `check-commit` | 检查 commit JSON 完整性 | webnovel-write SKILL.md (postcommit gate) |
| `check-index` | 检查 index.db 完整性 | webnovel-write SKILL.md (postcommit gate) |
| `check-batch-integrity` | 批次完整性检查 | batch 流程 |

## 三、核心数据流

### 3.1 写作主流程（write → commit → project）

```
┌─────────────────────────────────────────────────────────────┐
│  Skill: webnovel-write                                      │
│  Agent: chapter-writer-agent                                │
│  Steps: context → prewrite → write → precommit → commit    │
└─────────────────────────────────────────────────────────────┘
         │
         │ 1. context-agent 组装上下文
         ▼
    extract_chapter_context.py ──→ context_manager.py
         │                          │
         │                          ├─ build_context()
         │                          ├─ context_ranker.py (排序/裁剪)
         │                          └─ context_weights.py (权重计算)
         │
         │ 2. skill_runner.py check-structural  ← 五项结构检查
         ▼                         
    structural_checker.py ────────→ prewrite_validator.py
         │                          └─ artifact_validator.py (产物校验)
         │
         │ 3. chapter-writer-agent 生成正文
         ▼
         │
         │ 4. observer-agent 提取事实 (Step 5.1a)
         ▼
    observer-agent.md ────────────→ 自由文本 raw_facts
         │
         │ 5. observer_settler.py 解析 (Step 5.1b)
         ▼
    observer_settler.py ──────────→ extraction_result.json
         │                          ├─ _extract_character_changes()
         │                          ├─ _extract_entity_discoveries()
         │                          ├─ _extract_power_breakthroughs()
         │                          ├─ _extract_item_events()
         │                          ├─ _extract_promises()
         │                          ├─ _extract_world_rule_revealed()
         │                          └─ _extract_world_rule_broken()
         │
         │ 6. chapter_commit.py 提交 (Step 5.2)
         ▼
    chapter_commit.py ────────────→ chapter_commit_service.py

chapter_commit_service.py 核心流程:
  build_commit()
    ├─ 读取 review 报告
    ├─ 读取 extraction_result
    ├─ parse_review_output() 归一化 block_count
    └─ 写入 .story-system/commits/chapter_NNN.commit.json

  apply_projections()
    ├─ accepted → publish_event() (SSOT 事件) + 5 路投影
    └─ rejected → state writer (更新 chapter_rejected 状态)

         │
         │ 7. 5 路投影写入
         ▼

┌──────────────┬─────────────────┬──────────────────────────┐
│  Writer      │ 输出文件         │ 功能                     │
├──────────────┼─────────────────┼──────────────────────────┤
│ state        │ .webnovel/      │ 更新 state.json (entities/│
│              │ state.json      │ relationships/world_rules │
│              │                 │ /foreshadowing/progress)  │
│ index        │ .webnovel/      │ 更新 chapter_meta/        │
│              │ index.db        │ review_metrics/entities   │
│ summary      │ .webnovel/      │ 生成章节摘要 markdown     │
│              │ summaries/      │                           │
│ memory       │ .webnovel/      │ 更新记忆 scratchpad       │
│              │ memory_scratchpad│                          │
│ vector       │ .webnovel/      │ 向量嵌入更新 (需 aiohttp) │
│              │ index.db        │                           │
└──────────────┴─────────────────┴──────────────────────────┘

         │ 8. Markdown 投影渲染
         ▼
    state_projection_renderer.py
         ├─ _render_world_state()     ← state.json
         ├─ _render_foreshadowing_panel()  ← state.json
         ├─ _render_character_matrix()     ← state.json + entities
         ├─ _render_power_system()         ← state.json + entities
         └─ _render_chapter_index()        ← index.db
         │
         ▼
    story/ 目录
         ├─ 世界观状态.md
         ├─ 伏笔面板.md
         ├─ 角色关系.md
         ├─ 力量体系.md
         └─ 章节索引.md
```

### 3.2 审查流程

```
┌───────────────────────────────────────────────┐
│  Skill: webnovel-review                       │
│  Agent: reviewer × 6 (并行)                   │
└───────────────────────────────────────────────┘
         │
         │ 1. Code Checkers (确定性，阻断)
         ▼
    structural_checker.py
         ├─ 破折号统计
         ├─ "但" 使用统计
         ├─ "不是X是Y" 句式统计
         ├─ 句号密度统计
         └─ 系统【】格式验证
         │
         │ 2. LLM Reviewer (6 维度)
         ▼
    reviewer.md (agent prompt)
         ├─ 设定一致性 (setting)
         ├─ 时间线 (timeline)
         ├─ 叙事连贯 (continuity)
         ├─ 角色一致性 (character)
         ├─ 逻辑 (logic)
         └─ 项目规则 (rules)
         │
         │ 3. 解析输出
         ▼
    review_pipeline.py
         ├─ clean_reviewer_output()    ← 提取 JSON
         │   ├─ Markdown code block 提取
         │   ├─ 中文引号安全处理 (_sanitize_json_text)
         │   └─ 贪婪回退 (花括号匹配)
         ├─ parse_review_output()      ← schema 校验
         └─ review_schema.py           ← Pydantic 模型
         │
         ├─ blocking_count 自算（不信任 LLM 原始值）
         └─ 输出 → .story-system/reviews/
```

### 3.3 SSOT 事件溯源

```
┌──────────────────────────────────────────────┐
│  事件写入 (唯一写路径)                        │
└──────────────────────────────────────────────┘
    chapter_commit_service.py
         │
         └─ publish_event()
              │
              ssot_enforcer.py
              ├─ append_event()       ← 追加 .story-system/events/*.event.json
              ├─ event_log_store.py   ← 同步 SQLite + JSON
              └─ 5 路 projection writer
                   │
                   ▼
              ssot_enforcer.py:rebuild_state_json()
                   ← 确定性重放全部事件重建 state.json

    ssot verify  →  compare state.json vs event log
    ssot rebuild →  rebuild all projections from events
    ssot events  →  read event log
```

### 3.4 Story Contract 合同系统

```
┌──────────────────────────────────────────────┐
│  合同层次: MASTER_SETTING → Volume → Chapter │
└──────────────────────────────────────────────┘
    story_system.py ──────────────→ story_system_engine.py
         │                              │
         │                              ├─ 加载 MASTER_SETTING
         │                              ├─ 解析 volume_brief
         │                              └─ 生成 chapter_directive
         │
         ▼
    story_contracts.py
         ├─ persist_runtime_contracts()
         │   └─ .story-system/chapters/chapter_NNN.json
         ├─ story_contract_schema.py
         └─ runtime_contract_builder.py

    override_contract_engine.py
         └─ 版本化规则覆盖 (如 "金丹期不可飞行 → 获得混沌珠后可飞行")
```

## 四、data_modules/ 核心模块

按数据链层次组织：

### 4.1 配置与环境

| 模块 | 功能 |
|------|------|
| `config.py` | DataModules 配置、project_root 管理 |
| `project_phase.py` | 项目生命周期检测（10 个阶段） |
| `cli_args.py` / `cli_output.py` | CLI 参数解析 / 输出格式化 |
| `schemas.py` | 通用 Pydantic 模型 |
| `observability.py` | 日志与监控 |

### 4.2 数据层

| 模块 | 功能 | 关键类/函数 |
|------|------|------------|
| `state_manager.py` | state.json 原子读写 (filelock + snapshot) | `StateManager` |
| `sql_state_manager.py` | SQLite 状态存储 | `SqlStateManager` |
| `index_manager.py` | index.db 索引管理 | `IndexManager` |
| `event_log_store.py` | 事件日志 SQLite + JSON 双写 | `EventLogStore` |
| `ssot_enforcer.py` | SSOT 重建/校验/发布 | `publish_event()`, `rebuild_state_json()`, `verify_consistency()` |
| `chapter_commit_service.py` | commit 构建 + 投影调度 | `build_commit()`, `apply_projections()` |
| `chapter_commit_schema.py` | commit JSON 结构定义 | `CommitPayload` |
| `chapter_delete_service.py` | 章节安全删除 | |
| `projection_log.py` | 投影运行日志 (JSONL) | `append_projection_run()` |
| `commit_artifacts.py` | extraction 产物统一访问 | `extraction_result_from_commit()` |

### 4.3 投影 Writer（5 路）

| 模块 | 输出 | 关键类 |
|------|------|--------|
| `state_projection_writer.py` | state.json | `StateProjectionWriter` |
| `index_projection_writer.py` | index.db | `IndexProjectionWriter` |
| `summary_projection_writer.py` | summaries/*.md | `SummaryProjectionWriter` |
| `memory_projection_writer.py` | memory_scratchpad.json | `MemoryProjectionWriter` |
| `vector_projection_writer.py` | index.db (向量) | `VectorProjectionWriter` |
| `event_projection_router.py` | 路由 → 决定调用哪些 writer | |
| `state_projection_renderer.py` | story/*.md (人类可读) | `render_all_projections()` |

### 4.4 上下文与知识

| 模块 | 功能 |
|------|------|
| `context_manager.py` | 组装写作上下文 (含 section 裁剪) |
| `context_ranker.py` | 上下文排序与优先级 |
| `context_weights.py` | 权重计算 |
| `rag_adapter.py` | BM25 检索 + 结果融合 |
| `query_router.py` | 多源查询路由 |
| `knowledge_query.py` | 知识库查询 |
| `reference_search.py` | 参考文献搜索 |

### 4.5 AI API 客户端

| 模块 | 功能 |
|------|------|
| `api_client.py` | Modal/OpenAI API 客户端 (aiohttp) |

### 4.6 Entity / 实体

| 模块 | 功能 |
|------|------|
| `entity_linker.py` | 实体链接 + 消歧 |
| `entity_cleanup.py` | 脏实体扫描与标记 |
| `index_entity_mixin.py` | index.db 实体操作 |

### 4.7 写作工作流

| 模块 | 功能 |
|------|------|
| `observer_settler.py` | Observer→Settler: raw_facts → extraction_result |
| `structural_checker.py` | 5 项代码级结构检查 |
| `prewrite_validator.py` | 写前校验 |
| `artifact_validator.py` | 产物完整性校验 |
| `chapter_outline_loader.py` | 章纲加载 (field mapping) |
| `story_contracts.py` | 合同持久化与渲染 |
| `story_contract_schema.py` | 合同 Pydantic 模型 |
| `story_system_engine.py` | 合同引擎 (MASTER_SETTING → chapter) |
| `runtime_contract_builder.py` | 运行时合同构建 |
| `override_contract_engine.py` | 规则覆盖版本化 |
| `override_ledger_service.py` | 覆盖分类账管理 |
| `project_memory.py` | 项目级记忆管理 |
| `writing_guidance_builder.py` | 写作指导构建 |
| `genre_profile_builder.py` | 题材配置 |
| `genre_aliases.py` | 题材别名映射 |
| `urgency_utils.py` | 紧迫度计算 |

### 4.8 审查

| 模块 | 功能 |
|------|------|
| `review_pipeline.py` | 审查输出解析 + 中文引号安全 |
| `review_schema.py` | 审查结果 Pydantic 模型 |
| `amend_proposal_schema.py` | 修改建议 Schema |

### 4.9 记忆

| 模块 | 功能 |
|------|------|
| `memory_contract.py` | 记忆合同定义 |
| `memory_contract_adapter.py` | 记忆合同适配器 |
| `memory/` (子包) | orchestrator, compactor, store, writer, schema, bootstrap, budget |

### 4.10 运维

| 模块 | 功能 | CLI 触发 |
|------|------|---------|
| `orchestrate.py` | 批量编排 (write/heal/nightly) | `orchestrate` |
| `placeholder_scanner.py` | 占位符扫描 | `placeholder-scan` |
| `doctor.py` | 项目健康诊断 | `doctor` |
| `status_reporter.py` | 项目状态报告 | `status` |
| `story_runtime_health.py` | 故事运行时健康检查 | preflight 内调用 |
| `story_runtime_sources.py` | 故事运行时数据源 | |
| `workflow_checkpoint.py` | 章节阶段检查点 | `workflow checkpoint` |

## 五、调用关系总图

```text
webnovel-write SKILL.md
  │
  ├─ context-agent.md ─────→ extract_chapter_context.py
  │                              └─ context_manager.py
  │
  ├─ skill_runner.py ──────→ structural_checker.py (prewrite)
  │                         └─ story_system_engine.py (contract)
  │
  ├─ chapter-writer-agent.md ──→ 生成正文
  │
  ├─ observer-agent.md ────→ raw_facts (自由文本)
  │     └─ observer_settler.py → extraction_result.json
  │
  ├─ reviewer.md × 6 (并行) ──→ review JSON
  │     └─ review_pipeline.py → parse → .story-system/reviews/
  │
  └─ chapter_commit.py ────→ chapter_commit_service.py
        ├─ build_commit()
        ├─ publish_event() → SSOT enforcer
        └─ apply_projections() → 5-way writers + projection_log
              └─ state_projection_renderer.py → story/*.md
```

## 六、关键数据路径总结

| 路径 | 输入 | 处理 | 输出 |
|------|------|------|------|
| **写作** | SKILL.md → agent prompt | context → prewrite → write → precommit | 章节正文 .md |
| **提取** | 章节正文 .md | observer-agent → settler 正则解析 | extraction_result.json |
| **审查** | 章节正文 + state + 大纲 | 5 项 code check → 6 维 LLM review | review JSON + issues |
| **提交** | extraction + review | build_commit → apply_projections | commit JSON + 5 投影 + story/*.md |
| **SSOT** | events/*.event.json | rebuild_state_json 确定性重放 | state.json |
| **合同** | MASTER_SETTING + volume_brief | story_system_engine → persist | chapter_NNN.json |
| **上下文** | state.json + index.db + 大纲 | context_manager.build_context() | .webnovel/runtime/chapter-NNN.context.json |
| **记忆** | extraction result | orchestrator → compactor → 三层存储 | memory_scratchpad.json + index.db |
