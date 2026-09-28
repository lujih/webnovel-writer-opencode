# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Webnovel Writer for OpenCode — a long-form Chinese web novel AI writing system built on the OpenCode framework. Combats AI "forgetting" and "hallucination" in serialized fiction through layered RAG, story contracts, and structured quality review. v2.8 introduced inkOS-inspired Observer→Reflector fact extraction, SSOT event sourcing, and markdown truth-file projections; v2.9.x added atomic_write_json with WinError 5 backoff, open_loop foreshadowing projections, and rebuild non-event-field preservation (current release v2.9.2). Forked from lingfengQAQ/webnovel-writer and heavily refactored for OpenCode architecture.

## Commands

### Testing

```bash
# Full test suite (from repo root) — pytest.ini requires -p no:cov -o "addopts="
python -m pytest .opencode/scripts/data_modules/tests -q -p no:cov -o "addopts="

# CI 门禁用的命令（.github/workflows/test.yml 就是这么跑的）
# 只排除 test_publisher.py：它因陈旧 import（from publisher import REGISTRY，
# 产品已改为 _registry/get_adapter）与陈旧 lambda 签名（get_upload_log_dir 新增
# project_name 形参）而收集失败，属独立待修项。test_rag_adapter.py 不需要排除
# ——它 17/17 全过，真正的原因是 pytest.ini 的 -p no:asyncio 会禁掉
# pytest-asyncio，故命令行必须带 -o "addopts="。
python -m pytest .opencode/scripts/data_modules/tests -q -p no:cov -o "addopts=" \
  --ignore=.opencode/scripts/data_modules/tests/test_publisher.py

# Single test file
python -m pytest .opencode/scripts/data_modules/tests/test_config.py -q -p no:cov -o "addopts="

# Single test function
python -m pytest .opencode/scripts/data_modules/tests/test_config.py::test_load_env -q -p no:cov -o "addopts="
```

Tests live in `.opencode/scripts/data_modules/tests/`. `pytest.ini` enables `pytest-cov` by default — use `-p no:cov -o "addopts="` to disable. `conftest.py` patches `tempfile.mkdtemp` and sets `sqlite3` journal mode for test safety. **0 pre-existing failures as of 2026-09-17**（test fixtures 补齐 + verify_consistency drift 对齐后基线全绿；`ssot verify` 的 `progress.chapter_status` / `foreshadowing` 计数检查已对齐 rebuild P0 合并语义——只报缺口与少报，state 超集与顶层孤儿闭合不误报）。

### CLI

```bash
# Unified entry point for all commands
python .opencode/scripts/webnovel.py <command> [args]

# Common subcommands
python .opencode/scripts/webnovel.py preflight       # validate runtime environment
python .opencode/scripts/webnovel.py status          # project health report
python .opencode/scripts/webnovel.py story-system    # story contract management
python .opencode/scripts/webnovel.py review-pipeline # review pipeline management

# SSOT / Event Sourcing
python .opencode/scripts/webnovel.py ssot verify     # check state.json vs event log
python .opencode/scripts/webnovel.py ssot rebuild    # rebuild all projections from events
python .opencode/scripts/webnovel.py ssot events     # read event log

# Workflow / Override / Ops
python .opencode/scripts/webnovel.py workflow status              # chapter stage status
python .opencode/scripts/webnovel.py workflow checkpoint --chapter N --stage STAGE
python .opencode/scripts/webnovel.py override list                # active rule overrides
python .opencode/scripts/webnovel.py override context --chapter N # hints for writer
python .opencode/scripts/webnovel.py orchestrate write "1-5"      # batch write
python .opencode/scripts/webnovel.py delete-chapters "5-8" --dry-run
python .opencode/scripts/webnovel.py entity-clean                 # scan dirty entities
python .opencode/scripts/webnovel.py state render                 # markdown projections

# 独立工具脚本
python .opencode/scripts/data_modules/chapter_rename.py --project-root <PATH> --dry-run  # 章节文件名编号统一

# Others
python .opencode/scripts/webnovel.py export          # export novel
python .opencode/scripts/webnovel.py publish         # publish to platform
python .opencode/scripts/webnovel.py memory          # memory system management
```

Full command list (**38** top-level commands, 51 `add_parser` call sites counting subcommands like `knowledge query-entity-state`, `ssot verify`, `workflow checkpoint`, `override add`): `where`, `chapter-path`, `preflight`, `use`, `index`, `state`, `rag`, `style`, `entity`, `context`, `memory`, `migrate`, `status`, `doctor`, `update-state`, `backup`, `archive`, `init`, `extract-context`, `story-system`, `story-events`, `chapter-commit`, `memory-contract`, `project-memory`, `review-pipeline`, `placeholder-scan`, `master-outline-sync`, `export`, `publish`, `knowledge`, `checkers`, `orchestrate`, `delete-chapters`, `entity-clean`, `ssot`, `workflow`, `override`, `dsh-sync`。

Most subcommands forward to `data_modules/<module>.py` via argparse dispatch. Writing tools (`--project-root` aware) use the `PASSTHROUGH_TOOLS` set; the entry point auto-resolves the book project root (directory containing `.webnovel/state.json`).

### Dashboard

```bash
# Backend (FastAPI on port 8765)
python -m .opencode.dashboard

# Frontend dev server (React + Vite, separate terminal)
cd .opencode/dashboard/frontend && npm run dev
```

## Architecture

### Six-Layer Data Flow

Code is organized as a pipeline — each layer feeds the next:

| Layer | What | Where |
|-------|------|-------|
| Knowledge | CSV tables + MD references + BM25 retrieval | `.opencode/references/` |
| Reasoning | Genre routing + anti-pattern ranking | `.opencode/genres/` |
| Contract | MASTER_SETTING + volume/chapter briefs + review contracts | `.story-system/` (per-project) |
| Context | JSON assembly of what the writer needs | `data_modules/context_manager.py` |
| Commit | Fact extraction + event sourcing + projection routing | `data_modules/chapter_commit_service.py`, `data_modules/event_log_store.py` |
| Projection | 5 writers: state, index, summary, memory, vector | `data_modules/` various `*_writer.py` |

### Key Subsystems

**Story Contract Engine** — MASTER_SETTING.json is the source of truth. Runtime contracts derive from it per chapter. Core files: `story_system_engine.py`, `story_contracts.py`.

**SSOT Event Sourcing** (v2.8) — Append-only event log (`.story-system/events/*.event.json`) as immutable truth. `publish_event()` is the single write path; `rebuild_state_json()` deterministically replays all 16 event types to rebuild projections. `verify_consistency()` detects drift between state.json and event log. File: `ssot_enforcer.py`. `state_manager.py` uses pending queue + filelock + snapshot rollback for atomic writes. `state_projection_writer.py` uses `filelock` to protect state.json read-modify-write. **v2.9.x additions**: `open_loop_created/closed` 事件路由到 `state` 投影，`StateProjectionWriter._apply_foreshadowing()` 聚合进 `plot_threads.foreshadowing`（dashboard 伏笔面板消费路径）；`rebuild_projections()` 保留非事件字段，避免 `ssot rebuild` 擦除数据（P0 修复）。保留机制分两类：`_NON_EVENT_FIELDS` 白名单（`project_info`/`chapter_meta` 等 **10** 个，整字段回填）与 `_SPECIAL_MERGE_FIELDS` 专用合并（`progress` 逐子字段——`current_chapter`/`last_updated` 仍以事件为准、`chapter_status` 逐章合并；`plot_threads` 子字段级——`foreshadowing` 由事件驱动；`relationships` 保持既有形状并把回放出的边并入）。**新增顶层字段若要扛住 rebuild 必须显式登记，否则会被静默擦除**；`_render_foreshadowing_panel()` 读 `plot_threads.foreshadowing`（嵌套优先），兼容顶层 `foreshadowing` legacy。

**Atomic JSON Writes / WinError 5 退避** (v2.9.x) — `security_utils.atomic_write_json()` 用 `tempfile.mkstemp` + `os.replace` 原子写盘，`_replace_with_retry()` 对 `PermissionError` 指数退避（20ms→500ms ×10，约 2.6s 窗口），穷尽后抛 `AtomicWriteError`。Windows 下目标文件被 VSCode file watcher/杀软/同步盘瞬时打开（未开 `FILE_SHARE_DELETE`）报 WinError 5 时自动重试自愈；`WEBNOVEL_TEST_RELAX_ATOMIC_REPLACE=1` 降级为非原子覆写兜底（测试沙箱专用）。所有 JSON 投影（state.json/memory_scratchpad.json/summaries）统一受益。

**Context Assembly — global 段注入 + 文风记忆** (v2.9.x) — `context_manager._assemble_json_payload()` 恢复注入 `global` 段（世界观骨架/力量体系/风格契约，此前 `!= "global"` 特例误丢弃），按 `TEMPLATE_WEIGHTS` 权重分配；`author_style_patterns` section 消费 `/webnovel-learn` 写入的 `project_memory.json` patterns——按 importance 排序取前 10 条、description 截断 200 字、从 memory 段剥离避免重复注入；`style_contract_ref` 截断 2000 字控制 token 预算。

**Override Contract Engine** (v2.8) — Versioned world rule evolution (e.g., "金丹期不可飞行 → 获得混沌珠后可飞行"). `add_override()` creates new version and supersedes previous. `build_context_hints()` generates AI-injectable context. File: `override_contract_engine.py`.

**Observer→Reflector Pipeline** (v2.8) — Two-stage fact extraction inspired by inkOS. Observer (`observer-agent.md`) extracts free-text facts with no schema constraint (coverage-first). Settler (`observer_settler.py`) parses markdown sections via regex, resolves entity references, validates via Pydantic `StoryEvent`, and outputs `extraction_result.json`. Wired into `webnovel-write` SKILL.md Step 5.1a/5.1b. **Important**: `observer_settler.py` uses try/except ImportError fallback for `__main__` execution — the SKILL.md calls it as `python observer_settler.py` directly.

**Commit Chain** — `chapter_commit_service.py`: `build_commit()` (blocking_count 从 issues 列表自算，通过 `parse_review_output` 归一化，不信任 LLM 原始值) → `apply_projections()` (accepted 章节发布事件到 SSOT + 运行 5 路 projection；rejected 章节只走 state writer 更新 chapter_rejected 状态). `event_projection_router.py` determines which writers to invoke. `event_log_store.py` mirrors events to per-chapter JSON + SQLite `story_events` table.

**Memory System** — Three tiers: working (short-term), **episodic** (mid-term), semantic (long-term). 预算比例见 `memory/budget.py`（`working_ratio` / `episodic_ratio` / `semantic_ratio`，按 write/review/query 三种任务分别配比）。Modules in `data_modules/memory/`: orchestrator, compactor, store, writer, schema, bootstrap, budget.

**Chase Debt / 追读债** — `index_debt_mixin.py`（mixin 进 `index_manager.py`）维护 `chase_debt` 数值表（金额/利息/到期章节，`create_debt`），以及 `create_override_contract` 的版本化合同。与伏笔（`open_loop_created/closed`）是**两套独立机制**。上下文侧的硬约束是 `context_manager._load_debt_override_hints` 的 `if shown >= 3: break` 上限——注意它读的是 `override_contracts` 而非 `chase_debt`。**文档此前描述的「DebtTracker 类 / 活跃债 > 2 触发 15% token 预算」在代码中并不存在**（`DebtTracker` 这个名字只出现在本文件里），已删除该表述。

**Review Pipeline** — Two layers: Code Checkers (deterministic, run before LLM, block critical issues) → LLM reviewer checking **6 dimensions**（`reviewer.md`: setting / timeline / continuity / character / logic / rules）。**ai_flavor、pacing、毒点不由 reviewer 检查**，由 polish 阶段处理（`reviewer.md:39,43,95` 三处明确排除）——此前本文件写的「13 维」不存在。结构化检查清单强制逐项输出 pass/问题结论. Reviewer output processed via `.opencode/scripts/review_pipeline.py`, schema in `data_modules/review_schema.py`. **审查载荷形状异常（`issues` 非数组、顶层非对象）一律 fail-closed**——合成 critical 阻断项转人工，因为「无法判断有没有问题」不等于「没有问题」，静默丢弃会让被否章节以满分通过进 SSOT。JSON 解析含中文引号安全处理（`_sanitize_json_text`）. 写-修循环上限**只存在于 prompt 文本**（`webnovel-write/SKILL.md` 写 2 轮，与 `webnovel-write-batch/SKILL.md` 一致；本文件此前写 3 轮，与 skill 矛盾），代码中无计数器或轮次参数。

**Markdown Projection Renderer** (v2.8) — Renders 5 human-readable markdown files from `state.json` + `index.db` into `story/` directory. Triggered after `chapter-commit` and `ssot rebuild`. File: `state_projection_renderer.py`. 兼容 `relationships` 字段的 dict 和 list 两种格式，`entities_v3` 值类型防御.

**Runtime Artifacts** (v2.8) — `context_manager.build_context()` persists `.webnovel/runtime/chapter-NNN.context.json` (full context pack) and `.trace.json` (section inclusion/exclusion decisions) for post-hoc debugging.

**Dashboard** — FastAPI backend (GET 查询 + 文风约束编辑 PUT/POST/DELETE + 批量操作) + React 19 frontend with ECharts visualization. Backend: `.opencode/dashboard/app.py`. Frontend: `.opencode/dashboard/frontend/`. 9 个页面：总览、上下文健康、角色图鉴（含时间线）、审查分析、节奏雷达、伏笔追踪、文档浏览、文风约束（6 Tab）、系统状态（含批量操作）。支持亮色/暗色主题切换。文风约束编辑器（`/style`）支持 6 层约束的可视化编辑：自定义提示词、全局文风、禁止模式、写作技法、章级合同、审查维度。批量操作使用 `asyncio.create_subprocess_exec` 避免阻塞。关键 Section 列表可通过 `.webnovel/dashboard_config.json` 自定义。All SQL queries use parameterized `?` placeholders. CORS restricted to localhost. 项目根目录解析支持 5 级优先级（CLI > 环境变量 > 脚本位置搜索 > CWD 向上搜索 > 指针文件/注册表）。

### OpenCode Integration

16 skills and 6 agents defined in `.opencode/skills/` and `.opencode/agents/`. **DSH 适配**：
DeepSeek Harness（deepseek-ai/deepseek-harness）的 skill provider 不读 `.opencode/`；
`.dsh/skills/` + `.dsh/agents/` 是派生镜像（17 个 SKILL.md 含桥接层 webnovel-writer
+ 6 个 agent），由 `python .opencode/scripts/webnovel.py dsh-sync` 单向生成（幂等，
`--check` 做 CI 门禁）；工具映射（Agent→subagent / AskUserQuestion→ask_user_question /
Task→job_*）与差异点（write-guard 钩子在 DSH 退化为流程纪律）详见
`docs/guides/dsh-adaptation.md`。SSOT/原子写/伏笔契约等数据层不变式与 harness 无关。

Skills: `webnovel-write`, `webnovel-write-batch`, `webnovel-delete`, `webnovel-rewrite`, `webnovel-heal`, `webnovel-review`, `webnovel-init`, `webnovel-plan`, `webnovel-query`, `webnovel-export`, `webnovel-publish`, `webnovel-dashboard`, `webnovel-learn`, `webnovel-doctor`, `webnovel-fanqie-write`, `webnovel-qimao-write`.

Agents: `context-agent`, `observer-agent` (free-text extraction, coverage-first), `chapter-writer-agent`, `data-agent` (fulfillment + disambiguation only in default flow; fallback extraction in `--fast` mode), `reviewer` (instantiated 6× parallel), `deconstruction-agent`.

### Key Convention: Unified CLI

All Python functionality routes through a single entry point: `.opencode/scripts/webnovel.py` → `data_modules/webnovel.py`. Subcommands are dispatched via argparse — most forward to `data_modules/<module>.py` via `_run_data_module()`. New subcommands should be added to the argparse subparser chain in `webnovel.py`.

### Import Convention

Modules in `data_modules/` use absolute imports for top-level scripts (`from runtime_compat import ...`) and relative imports for intra-package references (`from .config import ...`). When a module needs to support both `__main__` execution and package import, use the try/except ImportError pattern (see `observer_settler.py`). `scripts/` must be on `sys.path` — `_ensure_scripts_dir_on_path()` handles this for the dashboard; the test harness and CLI entry point handle it for other contexts.

## Commit Convention & Versioning

All commits **MUST** follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>: <简短描述>

Co-Authored-By: AI Assistant <noreply@anthropic.com>
```

### Types

| Type | 用途 | 版本影响 |
|------|------|---------|
| `feat:` | 新功能 | **bump MINOR** (v2.8 → v2.9) |
| `fix:` | Bug 修复 | **bump PATCH** (v2.8.0 → v2.8.1) |
| `feat!:_/fix!:_/BREAKING CHANGE:` | 破坏性变更 | **bump MAJOR** (v2 → v3) |
| `docs:` | 文档 | 不触发版本变更 |
| `refactor:` | 重构 | **bump PATCH** |
| `perf:` | 性能优化 | **bump PATCH** |
| `ci:` | CI/CD | 不触发版本变更 |
| `chore:` | 杂项 | 不触发版本变更 |
| `simplify:` | 代码审查清理 | 不触发版本变更 |
| `test:` | 测试 | 不触发版本变更 |

### 发版（当前实际行为，勿依赖下面这段）

`manifest.yml` 在 push 到 master 时**只做两件事**：读 `git describe --tags` 拿到最近
tag，生成 `manifest.json`（`version` 字段形如 `v2.9.2-20260926.1521`，即
**tag + UTC 时间戳**，不是 semver），并把 manifest 提交回 master。`release` job
仅在 `github.ref_type == 'tag'` 时运行，也就是**必须人工先推 tag**。

⚠️ 本文件此前描述的「CI 根据 commit type 计算下一个 semver 版本 / 创建 git tag /
`feat!:` → v3.0.0 / `docs:` 不 bump」**并未实现**：`manifest.yml` 里没有任何
semver 运算，也没有 `git tag` 调用；它确实解析了 commit type，但只写进
`manifest.json` 的 changelog 数组，**从不影响版本号**。上表的「版本影响」列描述的是
**约定**，不是自动化行为——`feat!:` 目前会发成 `v2.9.2-<时间戳>`，`docs:` 反而也会
产生新的 `version` 字符串。补齐这部分（type → semver → 自动打 tag）是待办。

**版本号仍以 git tag 为准**，不是 manifest.json 字段。发布 = 人工打 tag。

### 示例

```bash
git commit -m "feat: add HTML export format"     # → v2.9.0
git commit -m "fix: resolve JSON corruption"     # → v2.8.1
git commit -m "docs: update install guide"        # → 无版本变更
git commit -m "feat!: drop Python 3.9 support"    # → v3.0.0
```

### 注意

- 不要在提交里手动改 `manifest.json` 版本号——CI 自动处理
- 多个提交一起 push → CI 取最高优先级的 bump
- `docs:`/`ci:`/`chore:`/`simplify:`/`test:` 不触发版本变更，可放心多用

## Guidelines

These behavioral guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

For multi-step tasks, state a brief plan with verification per step. Strong success criteria enable independent iteration.

### 5. OpenCode 文档优先

修改 OpenCode 相关目录前，必须先阅读对应文档：

| 目录 | 文档 |
|------|------|
| `.opencode/agents/` | https://opencode.ai/docs/zh-cn/agents/ |
| `.opencode/skills/` | https://opencode.ai/docs/zh-cn/skills/ |
| `.opencode/plugins/` | https://opencode.ai/docs/zh-cn/plugins/ |
| 其他 OpenCode 相关 | https://opencode.ai/docs/zh-cn/ |

原因：OpenCode 的 agent/skill/plugin 有特定的 frontmatter 格式、生命周期 hook、权限模型。不了解规范就修改会导致功能异常或兼容性问题。

## 外置

**实际写小说的目录。**

E:\workspace\webnovel2
E:\workspace\webnovel

**外部参考项目的目录。**

E:\workspace\webnovel-writer\外部参考\inkos
E:\workspace\webnovel-writer\外部参考\webnovel-writer 原项目

**opencode官方文档。**

https://opencode.ai/docs/zh-cn/