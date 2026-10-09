# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Webnovel Writer for OpenCode — a long-form Chinese web novel AI writing system built on the OpenCode framework. Combats AI "forgetting" and "hallucination" in serialized fiction through layered RAG, story contracts, and structured quality review. v2.8 introduced inkOS-inspired Observer→Reflector fact extraction, SSOT event sourcing, and markdown truth-file projections; v2.9.x added atomic_write_json with WinError 5 backoff, open_loop foreshadowing projections, and rebuild non-event-field preservation (current release v2.9.2). Forked from lingfengQAQ/webnovel-writer and heavily refactored for OpenCode architecture.

## Commands

### Testing

```bash
# 完整套件（.github/workflows/test.yml 就是这么跑的，无任何 --ignore）
# 必须带 -o "addopts="：pytest.ini 的 addopts 含 -p no:asyncio，会禁掉
# pytest-asyncio，导致 test_rag_adapter.py 的 11 个 async 用例收集失败。
python -m pytest .opencode/scripts/data_modules/tests -q -p no:cov -o "addopts="

# Single test file
python -m pytest .opencode/scripts/data_modules/tests/test_config.py -q -p no:cov -o "addopts="

# Single test function
python -m pytest .opencode/scripts/data_modules/tests/test_config.py::test_load_env -q -p no:cov -o "addopts="
```

Tests live in `.opencode/scripts/data_modules/tests/`. `pytest.ini` enables `pytest-cov` by default — use `-p no:cov -o "addopts="` to disable. `conftest.py` patches `tempfile.mkdtemp` and sets `sqlite3` journal mode for test safety；它还会把 `WEBNOVEL_OPENCODE_HOME` 指向临时目录并在会话结束时还原工作区指针，**测试不会改写开发者的项目绑定**。**884 passed / 0 failed，零排除**（第4轮审查后：`test_publisher.py` 的陈旧 `from publisher import REGISTRY` 与陈旧 `get_upload_log_dir` 零参 lambda 已随产品签名更新修复，publisher 子系统重新获得覆盖；`test_rag_adapter.py` 的 17 例也已纳入）。

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

# 数据修复（破坏性操作，默认 dry-run，加 --apply 才写库）
python .opencode/scripts/webnovel.py index repair-foreshadowing-debts          # 清理重复伏笔债
```

Full command list (**38** top-level commands, 51 `add_parser` call sites counting subcommands like `knowledge query-entity-state`, `ssot verify`, `workflow checkpoint`, `override add`): `where`, `chapter-path`, `preflight`, `use`, `index`, `state`, `rag`, `style`, `entity`, `context`, `memory`, `migrate`, `status`, `doctor`, `update-state`, `backup`, `archive`, `init`, `extract-context`, `story-system`, `story-events`, `chapter-commit`, `memory-contract`, `project-memory`, `review-pipeline`, `placeholder-scan`, `master-outline-sync`, `export`, `publish`, `knowledge`, `checkers`, `orchestrate`, `delete-chapters`, `entity-clean`, `ssot`, `workflow`, `override`, `sync-agents-md`。

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

**Dashboard** — FastAPI backend (GET 查询 + 文风约束编辑 PUT/POST/DELETE + 批量操作) + React 19 frontend with ECharts visualization. Backend: `.opencode/dashboard/app.py`. Frontend: `.opencode/dashboard/frontend/`. 9 个页面：总览、上下文健康、角色图鉴（含时间线）、审查分析、节奏雷达、伏笔追踪、文档浏览、文风约束（6 Tab）、系统状态（含批量操作）。支持亮色/暗色主题切换。文风约束编辑器（`/style`）支持 6 层约束的可视化编辑：自定义提示词、全局文风、禁止模式、写作技法、章级合同、审查维度。批量操作使用 `asyncio.create_subprocess_exec` 避免阻塞。关键 Section 列表可通过 `.webnovel/dashboard_config.json` 自定义。All SQL queries use parameterized `?` placeholders. CORS restricted to localhost. 项目根目录解析优先级（`project_locator.resolve_project_root` / `webnovel.py::_resolve_root`）：**显式 `--project-root` > `WEBNOVEL_PROJECT_ROOT` > CWD 自身及父目录向上搜索 > CWD 下恰好一本书 > 工作区指针文件 > 用户级注册表**；入口层再兜底「脚本 checkout 所在工作区」。**CWD 必须排在指针与注册表之前**——人站在书 A 的目录里、指针却指向书 B 时，写路径命令会真的改写 B 的 state.json（静默数据损坏）。子目录只在**恰好一本**时才被采纳，多本一律不猜（按字典序挑一本同样是写错书）。搜索不越过 git 根。

### OpenCode Integration

16 skills and 6 agents defined in `.opencode/skills/` and `.opencode/agents/`。**OpenCode v2 适配**（依据 https://opencode.ai/v2/docs/migrate-v1 ）：
V2 三处破坏性变更中两处直接影响本仓库——**① 插件 API 全换，V1 插件实现在 V2 里根本不会被加载**（不报错、静默不加载，守卫无声失效），故 `write-guard.js` 采用官方并存写法：同一个 default export 上同时给 `setup(ctx)`（V2 调用）与 `server()`（V1 调用，OpenCode ≥ 1.18.29 支持 object 入口）。两代 hook 入参形状不同（V1 `input.tool`/`output.args` vs V2 `event.tool`/`event.input`），判定逻辑抽成共享的 `guard(tool, args)`，`test_write_guard_plugin.py` 逐例比对两代判定必须一致。有意不 `import { Plugin } from '@opencode/plugin'`：该包本机未安装，静态 import 解析失败会让插件在 V1 下也加载不了。
**② V2 只发现 `AGENTS.md`，不再回退 `CLAUDE.md`**——没有 AGENTS.md 则整套项目说明在 V2 下不可见。故 `sync-agents-md` 把 `CLAUDE.md` 逐字节镜像为 `AGENTS.md` 并纳入 `--check` CI 门禁。内容逐字节一致而非拆成两份：拆开则两套规则各自漂移、模型会收到互相矛盾的两套说明；一致则不会双重注入（部分宿主的 `instructionFileCandidates` 同时含两者，内容相同时去重并优先 AGENTS.md）。
已确认无需改动：skills 目录式 + `name`/`description`（V2 明示不需重写）、`mode: subagent`（V2 仍接受 primary/subagent/all）、`.opencode/plugins/` 与 `.opencode/agents/`（已是 V2 推荐位置）。
**6 个 agent 已迁到 `permissions:`**（`migrate_agents_v2_permissions.py`，幂等可重复运行）。V1 文档明写 legacy `tools` 布尔配置"已废弃并并入 `permission`"，而**两代默认策略都是放行**（`{action:"*",resource:"*",effect:"allow"}`）——所以原来那 6 个 `tools: {read: true, ...}` **从未构成白名单**，只是把当时默认放行的东西又写了一遍。只把 true 翻成 allow 等于零约束，必须先 deny-all 再逐条放行。规则顺序即优先级（最后命中者生效），故 deny-all 必须排在最前。每个 agent 在 `shell` 之后显式放回 `external_directory: ask`——书项目在 `E:\workspace\webnovel2`，`deny *` 会连它一起拒掉，agent 立刻读不到正文与 state.json（迁移时真踩到，已被 `test_agent_v2_permissions.py` 钉死）。`glob`/`grep` 无条件放行：只读发现工具，原 `tools:` 没列但 V1 默认放行，deny-all 会收掉它们——那属于迁移自造的回归。
**本次收紧的**（相对原先"实际全放行"的行为）：`subagent`/`skill`/`webfetch`/`websearch`/`question` 一律 deny——子 agent 不该自行委派、联网或向用户追问（与 V2 出厂 `general` agent 策略一致）。`edit` 逐个判定：`reviewer`/`chapter-writer-agent`/`data-agent`/`observer-agent` 有（reviewer 正文要求"用 Write 工具将完整 JSON 写入 `${REVIEW_OUTPUT}`"）；`context-agent`/`deconstruction-agent` 无（后者正文明写"只返回结构化结果，不写任何文件"）。
仍待办：3 个 skill 的 `allowed-tools` 在 V2 不被解释（V2 只认 `name`/`description`/`slash`/`metadata`）。该键对 OpenCode 两代都是惰性的，保留它是为兼容 Claude Code / Agent Skills 生态，若不跨宿主使用可直接删。

### OpenCode v2 深度适配（.opencode/opencode.json + commands/）

`.opencode/opencode.json`（JSONC）只有 4 个键，且**每一个都要求 OpenCode V1 也能加载**：

- ⚠️ **不带 `permissions`（关键约束）**：2026-10-04 实测 opencode 1.18.34，`opencode debug config` 遇到 V2 的 `permissions` 键会以 **rc=1 拒绝加载整份配置**——不是忽略单个键、不是降级：
  `Error: Configuration is invalid at .../.opencode/opencode.json` / `↳ V2 permissions are not supported by OpenCode V1. Use V1 "permission" rules or run opencode2.`
  本仓库是对外公开的、安装器会把 `.opencode/` 部署到用户工作区，写死 V2 权限会让**全部 V1 用户起不来**。同次实测确认 `formatter`/`compaction`(V2 形状的 `keep`)/`watcher`/`tool_output` 四个键 V1 均接受，故只留这四个。`test_opencode_v2_config.py::TestConfigDoesNotBreakV1` 钉死此约束。
  同次实测还确认一件**不对称**的事：`.opencode/agents/*.md` frontmatter 里的 V2 `permissions` 在 V1 下是**静默忽略**（agent 照常加载，规则回落 V1 默认 102 条），不触发这种失败。所以子 agent 可以留 V2 规则，配置文件不行。
- **不带 `$schema`**：2026-10-01 实测 `https://opencode.ai/config.json` 返回的**仍是 V1 形状**——顶层 `additionalProperties: false`，只认 `permission`/`agent`/`command`/`plugin`/`snapshot`/`attachment`/`provider`，文档里的 V2 原生键一个都没有，挂上去只会让编辑器把**正确的** V2 字段全标红。
  ⚠️ 但 **OpenCode 会自动把它写回来**：2026-10-04 实测，执行任意一次 `opencode debug config` 后本文件第 2 行被自动插入 `"$schema": ...`。仓库提交的版本刻意不含它；本地被写回不必修（V1 也接受 `$schema`）。测试因此断言的是"仓库副本不含"，不是"任何本地副本都不含"。
- **不带 `instructions`**：V2 文档原文 "OpenCode accepts this field but does not load its entries; use AGENTS.md for instructions" —— 能过校验、什么都不做的静默空操作。项目说明唯一真实通道是 `AGENTS.md`。
- **`formatter: false`（显式）**：依据 V2 文档，内置 formatter 里 prettier/biome **都覆盖 `.md`**，而本书 65 章正文全是 `.md`（`正文/第1卷/第0001章-….md`）。开启会把已发布正文重排折行、规范空白——那是**改用户作品**，且 formatter 在写盘**之后**才跑，拦不住。（**文档转述，未在本书正文上实测**。）
- **`compaction`**：用 V2 的 `keep.tokens`（24000），不用 V1 的 `tail_turns`/`prune`。调高 keep 是为让刚写完那章原文尽量逐字留在尾部。
- **`watcher.ignore`**：屏蔽 `node_modules`/`__pycache__`/`.tmp`/`外部参考`，减少无意义的文件事件。
- **`tool_output`**：`max_lines`/`max_bytes` 取 V2 文档给出的默认值 2000/51200，写出来只为长输出时尽早暴露截断，不改变默认行为。

**SSOT 写保护现状（2026-10-04 调整后）**：原本是三层——① 主 agent 的全局 `permissions` deny、② 子 agent 各自的 `permissions` deny、③ `write-guard.js` 的 `tool.execute.before` 运行时抛错。**因①会让 V1 拒绝加载配置，已移除**，故主 agent 在 V2 下不再"看不见" SSOT 路径的 edit 工具（改为运行时拒绝）。
**保护本身没有缺口**：③ 挂的是全局 tool hook，与 agent 无关、对主 agent 生效、V1/V2 都能跑；②对 V2 仍在。丢失的只是"V2 主 agent 少一层提前拦截"，不是拦截本身。`test_opencode_v2_config.py::TestSsotGuardSurvivesPermissionsRemoval` 从 `write-guard.js` 的 `PROTECTED_SUFFIXES` **解析**清单（不抄一份——早先抄写时把后缀写成了 glob 且漏了 `commits/`，自己造出个不存在的清单），钉住兜底层不许被一起删掉。
另外 ② 与 ③ 的清单一致性由 `test_agent_v2_permissions.py::test_matches_write_guard_plugin_list` 钉住。

`.opencode/commands/` 5 个只读斜杠命令（`wn-status` / `wn-doctor` / `wn-ssot-verify` / `wn-where` / `wn-chapter-status`），用 `!\`shell\`` 块把 CLI 真实输出**在 prompt 提交前**注入，模型不必先猜再跑。**关键约束**：V2 文档 Warning 明确 shell 块"run when OpenCode evaluates the command, **outside the agent's tool permission flow**"——即 `write-guard.js` 拦不到它。所以 `test_opencode_v2_config.py` 断言 shell 块里**不得出现破坏性子命令**（`delete-chapters`/`rebuild`/`chapter-commit`/`publish`/`export`/…）；写路径必须走 skill + agent permissions + write-guard 链路。破坏性命令仍留 dry-run 默认 + `--apply` 二次确认。
实测 5 个命令的真书（`E:\workspace\webnovel2\凡尘之舞`）均正常执行；`ssot verify` 在真书上确实检出漂移并以 rc=1 退出（`foreshadowing` 0 vs 26、`entities_v3` 0 vs 4、`world_rules` 0 vs 10、`reader_promises` 0 vs 7），故 `wn-ssot-verify` 明确要求模型**不得自行 rebuild**。

**npm 离线包必须携带项目说明**（2026-10-09，对照 V2 迁移文档追出的三重缺口，已修）：`build-bundle.js` 原来只打包 `.opencode/`，仓库根的 `AGENTS.md`/`CLAUDE.md` 从未进包——已发布的 `2.9.2-12` 实测无此二文件，即 **npm 主安装路径在 V2 下没有项目说明**（且是静默失败：模型照常调用 skill 但读不到规则）。修复：① 构建时把两份文件追加为 tar 根条目（解压到工作区根、与 `.opencode/` 平级——正是 V2 的发现位置），缺失即拒绝出包；② 网络安装路径 PREFIX 限定 `.opencode/`、抽不到根文件，`init`/`update` 改用 `extractRootDocs`（`extract.js` 新增 `only` 白名单过滤）从源码 tarball 单抽，**必须在 `unlinkSync(tmp)` 之前**；③ 抽取失败不判安装失败，靠部署后的 AGENTS.md 存在性检查出声警告；④ `uninstall` 只删逐字节镜像（AGENTS==CLAUDE 才删），用户改过的说明保留。`test_npm_bundle_instructions.py`（11 例，需 node）钉住：构建产物含二文件且与仓库根逐字节一致、缺文件时构建失败、`extractRootDocs` 白名单不越权、init/update 调用顺序。

**未做及原因**：`$schema`（如上，schema 未跟上）；`experimental.policies`（想用它做 SSOT 硬 deny，但实测 schema 里 `Policy.action` 枚举**只有 `provider.use`**，文档示例中的 `"action":"permission"` 过不了校验，不冒这个险）；MCP 暴露 Python 数据层（需 `opencode.json` 的 `mcp.servers` + 给 6 个 agent 加 `<server>_<tool>` 放行规则，工作量中等且收益待观察）；FastAPI 面板**不可**迁进 OpenCode（插件只能挂 TUI slot / OpenTUI JSX，**没有 webview 宿主**，ECharts 无等价物）。

**不追求 DSH 原生化。** 本仓库只适配 OpenCode。DeepSeek Harness 侧由上游
`外部参考/webnovel-writer` 的 v8 分支负责（已用 TypeScript 重写成 DSH 原生插件
`@linfengqaqtat/dsh-scriptor`，以 `dsh-baseline.json` 锁 tag/commit/version，并用
真实 DSH checkout 跑类型检查；v8 明确不含 OpenCode 宿主）。此前本仓库的
`.dsh/` 镜像层（dsh-sync 生成的 skills/agents 副本 + 工具映射表）已删除——
两份资产各写一遍、规则各自漂移，收益低于成本。

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