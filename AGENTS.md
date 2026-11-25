# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Webnovel Writer for OpenCode — a long-form Chinese web novel AI writing system built on the OpenCode framework. Combats AI "forgetting" and "hallucination" in serialized fiction through layered RAG, story contracts, and structured quality review. v2.8 introduced inkOS-inspired Observer→Reflector fact extraction, SSOT event sourcing, and markdown truth-file projections; v2.9.x added atomic_write_json with WinError 5 backoff, open_loop foreshadowing projections, and rebuild non-event-field preservation. **v3.0.0** added the CED consistency-error-density quality metric, Narrative Forecast (isolated candidate branches before writing), and review-gate layering (LLM semantic verdicts become `review_flags` instead of blocking commits). Forked from lingfengQAQ/webnovel-writer and heavily refactored for OpenCode architecture.

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

Tests live in `.opencode/scripts/data_modules/tests/`. `pytest.ini` 的 `addopts` **不含任何 `--cov`**，`-p no:cov` 只是防御性禁用插件（`pytest-cov` 装在 `requirements-dev.txt`、`.coveragerc` 存在，但默认不测量覆盖率）——本文件此前写「`pytest.ini` enables `pytest-cov` by default」是错的。真正**必须**带的是 `-o "addopts="`：它会清掉 `addopts` 里的 `-p no:asyncio`；而 `-p no:asyncio` 生效时 `pytest-asyncio` 插件被禁，`test_api_client.py` 与 `test_rag_adapter.py` 的 async 用例会整批失败。 `conftest.py` patches `tempfile.mkdtemp` **与 `tempfile.TemporaryDirectory`（包装类 `_SafeTemporaryDirectory`，同时把临时目录钉进仓库 `.tmp/pytest`）**并 sets `sqlite3` journal mode for test safety；它还会把 `WEBNOVEL_OPENCODE_HOME` 指向临时目录并在会话结束时还原工作区指针，**测试不会改写开发者的项目绑定**。⚠️ `_SafeTemporaryDirectory` 转发 `delete=` 关键字参数**必须带 `_TEMPDIR_ACCEPTS_DELETE` 守卫**——`delete` 是 Python 3.14 才有的参数，无条件转发会让 CI（test.yml 跑 **Python 3.11**）在**收集阶段**全灭（filelock 导入期探测是第一个触发者；2026-10-09 时 Test 自 10/01 起**已连红 12+ 次**，本机只有 3.14 所以一直全绿）。`test_conftest_py_compat.py` 钉住。**连环根因第②环**：`export_manager/parser.py` 与 `formats/docx.py` 顶层 `import mistune`/`docx` 未在 requirements 声明——本地装过、干净 CI 必崩；已补进 `.opencode/scripts/requirements.txt`（ebooklib/Pillow 一并，weasyprint/playwright 为可选+优雅降级、写在注释里），`test_dep_declarations.py` 静态比对 import 与 requirements（顶层必须必装、惰性必须留痕），防止复发。当前计数以 README 目录树为准（`test_readme_accuracy` 强制与实际一致）；历史里程碑：第4轮审查后 884 passed 零排除，publisher 子系统重新获得覆盖、`test_rag_adapter.py` 的 17 例纳入。

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

Full command list (**40** top-level commands, 54 `add_parser` call sites counting subcommands like `knowledge query-entity-state`, `ssot verify`, `workflow checkpoint`, `override add`): `where`, `chapter-path`, `preflight`, `use`, `index`, `state`, `rag`, `style`, `entity`, `context`, `memory`, `migrate`, `status`, `doctor`, `update-state`, `backup`, `archive`, `init`, `extract-context`, `story-system`, `story-events`, `chapter-commit`, `memory-contract`, `project-memory`, `review-pipeline`, `placeholder-scan`, `master-outline-sync`, `export`, `publish`, `knowledge`, `checkers`, `orchestrate`, `delete-chapters`, `entity-clean`, `ssot`, `workflow`, `override`, `flag-triage`, `forecast`, `sync-agents-md`。

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

**Chase Debt / 追读债** — `index_debt_mixin.py`（mixin 进 `index_manager.py`）维护 `chase_debt` 数值表（金额/利息/到期章节，`create_debt`），以及 `create_override_contract` 的版本化合同。与伏笔（`open_loop_created/closed`）**存储上是两套机制**（`chase_debt` 表 vs `plot_threads.foreshadowing`），但**事件上是耦合的**：`chapter_commit_service._sync_foreshadowing()`（`chapter_commit_service.py:119`）在 `open_loop_created` 时 `create_simple_debt(debt_type="foreshadowing")` 建债、在 `open_loop_closed`/`promise_paid_off` 时 `resolve_debt_by_subject()` 销债，两侧 note 都走 `foreshadowing_utils.coerce_loop_content` 归一（否则建债与销债的键会分叉、伏笔债永不闭合）。上下文侧的硬约束是 `context_manager._load_debt_override_hints` 的 `if shown >= 3: break` 上限——注意它读的是 `override_contracts` 而非 `chase_debt`。**文档此前描述的「DebtTracker 类 / 活跃债 > 2 触发 15% token 预算」在代码中并不存在**——`grep -rn "class DebtTracker"` 零命中。真实入口是 `index_debt_mixin.py` 的 `create_debt`/`create_simple_debt`/`pay_debt`/`accrue_interest`（**利息按章节数复利，与调用频率无关**——`accrue_interest(ch)` 用 `debt_events` 里已计息的最大 chapter 算出需补几章，一次性 `current * ((1+rate)**n - 1)`。别改回「每次调用收一期」：那样写会让余额取决于作者多久跑一次。**v3.0 起已接入提交链路**——accepted 章节在投影循环与 `_sync_foreshadowing` 之后自动调用 `accrue_interest(chapter)`，幂等由 `debt_events` 的 `MAX(chapter)` 保证，失败只记 warning 不阻断提交）；上下文侧只通过 `context_manager.build_analysis_signal()` 的 `signal["debt_summary"] = self.index_manager.get_debt_summary()`（`context_manager.py:412`）注入一段摘要，**没有 2 条阈值、也没有 15% token 预算**。

**Review Pipeline** — Two layers: Code Checkers (deterministic, run before LLM, block critical issues) → LLM reviewer checking **6 dimensions**（`reviewer.md`: setting / timeline / continuity / character / logic / rules）。**ai_flavor、pacing、毒点不由 reviewer 检查**，由 polish 阶段处理（`reviewer.md:84,88,140` 三处明确排除）——此前本文件写的「13 维」不存在。结构化检查清单强制逐项输出 pass/问题结论. Reviewer output processed via `.opencode/scripts/review_pipeline.py`, schema in `data_modules/review_schema.py`. **审查载荷形状异常（`issues` 非数组、顶层非对象）一律 fail-closed**——合成 critical 阻断项转人工，因为「无法判断有没有问题」不等于「没有问题」，静默丢弃会让被否章节以满分通过进 SSOT。JSON 解析含中文引号安全处理（`_sanitize_json_text`）. **CED（一致性错误密度）** — 审查指标的**质量口径**，区别于 `overall_score`/清单完成度那类过程指标。三口径定义在 `review_schema.compute_ced()`（**唯一定义处**）：`ced_raw` = issues/单章字数×10000；`ced_weighted` = Σseverity 权重(critical 4.0/high 3.0/medium 2.0/low 1.0)/字数×10000；`ced_blocking` = blocking issues/字数×10000。**中文口径**：`_count_chapter_words()` 用 `len()` 计字符即「字数」，与 ConStory-Bench（arXiv 2603.05890）的英文 word count 单位不同，不可横向比数值、只能比同口径趋势。**`None` 与 `0.0` 是两件事**——分母为 0（章节文件缺失/空文件）时三口径为 NULL，不得除以 0 也不得伪造 0；「算不出」与「真的没发现问题」必须区分，否则趋势图会误导。**审查输出不可解析时不产出 CED**（`ReviewResult.unparseable` → `review_parseable=0`）：`_shape_issue()` 会合成一条 critical issue，照常算会把纯解析失败当成一致性问题，在趋势图上留一个无法解释的尖峰。落库 `review_metrics`（5 个新列 + 幂等 ALTER 迁移，`IndexManager._migrate_review_metrics_columns`），dashboard 经 `/api/review/analytics` 的 `ced_trends`/`ced_summary` 展示。**`unparseable` 必须随 `review_result` 流转**：`ReviewResult.to_dict()` 会带上 `review_parseable`（0/1），因为 `build_review_artifacts()` 的 `review_result` 字段就是它的输出、而 chapter-commit 把它交给 `parse_review_output()` 决定 accepted/rejected——此前 to_dict 漏了这个键，导致 fail-closed 结论在提交侧整段丢失（E2E 抓到）。技术/语义判定统一走 `review_schema.is_malformed_issue()`，`chapter_commit_service` 与 `artifact_validator` 共用一份逻辑。
**门禁分层（review_flags，v3.0）** — 提交门禁从「LLM 语义阻断」收窄为「技术阻断 + 语义标记」。`chapter_commit_service.build_commit` 的 `rejected` 表达式只保留三类**技术故障**（fail-closed 仍拒章）：`missed_raw`（章纲节点缺失为裸串）/ `missed_cbn`（核心情节点缺失）/ `disambiguation pending`（实体消歧未决）/ `unparseable`（审查输出不可解析，`ReviewResult.unparseable`，「无法判断有没有问题」≠「没有问题」）。LLM 报的 blocking 级**语义问题**不再拒章，降级为 commit 的 `review_flags`（观察标记，`triage: null` 即待仲裁）。配套：① `artifact_validator._policy_issues` 把单一 `blocking_review` error 拆成 technical error（`category=review_output_malformed` 或描述命中固定前缀，仍阻断）+ semantic warning（其余，不阻断）；② `context_manager._load_pending_review_flags` 写第 N 章前注入第 N-1 章未仲裁 flag（上限 3）到 `reader_signal["pending_review_flags"]` 警戒段；③ `flag_triage.py` CLI（`webnovel.py flag-triage list|confirm|dismiss --chapter N [--index I]`）+ dashboard `/api/review/flags` GET + `/api/review/flags/triage` PUT 作为人工裁决入口——confirm/dismiss 只写回 `review_flags[i].triage`，**不改 commit status**（门禁分层的底线：语义问题不拒章，仲裁只留痕）。confirm 不自动触发 heal（避免一次点击就改作品），真矛盾由作者显式跑 `webnovel-heal`。

写-修循环上限**只存在于 prompt 文本**（`webnovel-write/SKILL.md` 写 2 轮，与 `webnovel-write-batch/SKILL.md` 一致；本文件此前写 3 轮，与 skill 矛盾），代码中无计数器或轮次参数。

**Narrative Forecast（叙事预报）** — 写第 N 章前生成 2-5 个**相互隔离**的候选未来分支（冲突升级/伏笔回收/平静过渡），评估与既有章纲的对齐度（字符 2-gram Jaccard，无外部分词依赖），供写作者选支后**只把选中分支**追加进章纲。未选中候选保留在 `.story-system/forecasts/chapter_NNN/` 磁盘上作为参考，不被下游消费。纯增量可跳过（`webnovel.py forecast generate|select|list`，skill 默认不执行）。`data_modules/narrative_forecast.py`。

**Markdown Projection Renderer** (v2.8) — Renders 5 human-readable markdown files from `state.json` + `index.db` into `story/` directory. Triggered after `chapter-commit` and `ssot rebuild`. File: `state_projection_renderer.py`. 兼容 `relationships` 字段的 dict 和 list 两种格式，`entities_v3` 值类型防御.

**Runtime Artifacts** (v2.8) — `context_manager.build_context()` persists `.webnovel/runtime/chapter-NNN.context.json` (full context pack) and `.trace.json` (section inclusion/exclusion decisions) for post-hoc debugging.

**Dashboard** — FastAPI backend (GET 查询 + 文风约束编辑 PUT/POST/DELETE + 批量操作) + React 19 frontend with ECharts visualization. Backend: `.opencode/dashboard/app.py`. Frontend: `.opencode/dashboard/frontend/`. 11 个页面：总览、上下文健康、角色图鉴（含时间线）、审查分析、节奏雷达、伏笔追踪、追读债、运维诊断、文档浏览、文风约束（6 Tab）、系统状态（含批量操作）。支持亮色/暗色主题切换。文风约束编辑器（`/style`）支持 6 层约束的可视化编辑：自定义提示词、全局文风、禁止模式、写作技法、章级合同、审查维度。批量操作使用 `asyncio.create_subprocess_exec` 避免阻塞。关键 Section 列表可通过 `.webnovel/dashboard_config.json` 自定义。All SQL queries use parameterized `?` placeholders. CORS restricted to localhost. 项目根目录解析优先级（`project_locator.resolve_project_root` / `webnovel.py::_resolve_root`）：**显式 `--project-root` > `WEBNOVEL_PROJECT_ROOT` > CWD 自身及父目录向上搜索 > CWD 下恰好一本书 > 工作区指针文件 > 用户级注册表**；入口层再兜底「脚本 checkout 所在工作区」。**CWD 必须排在指针与注册表之前**——人站在书 A 的目录里、指针却指向书 B 时，写路径命令会真的改写 B 的 state.json（静默数据损坏）。子目录只在**恰好一本**时才被采纳，多本一律不猜（按字典序挑一本同样是写错书）。搜索不越过 git 根。

### OpenCode Integration

16 skills and 6 agents defined in `.opencode/skills/` and `.opencode/agents/`。**本项目自本次改动起以 OpenCode 2 为唯一目标**（V1 兼容已放弃，理由见下方配置章节）。规范依据：[/docs/migrate-v1/](https://opencode.ai/docs/migrate-v1/) 、[/docs/agents/](https://opencode.ai/docs/agents/) 、[/docs/skills/](https://opencode.ai/docs/skills/) 、[/docs/commands/](https://opencode.ai/docs/commands/) 、[/docs/permissions/](https://opencode.ai/docs/permissions/) 、[/docs/build/plugins/migrate-v1](https://opencode.ai/docs/build/plugins/migrate-v1) 。
**① 插件 API 全换**：V1 插件实现在 V2 里根本不会被加载（不报错、静默不加载，守卫无声失效）。`write-guard.js` 用官方「one package, both generations」写法：同一个 default export 上并存 `setup(ctx)`（V2）与 `server()`（V1 ≥1.18.29）。V2 侧注册 `ctx.tool.hook('execute.before', event => guard(event.tool, event.input))`——回调收**一个可变 event**；V1 侧仍是 `'tool.execute.before'(input, output)`，两代入参形状不同，判定抽成共享 `guard(tool, args)`，`test_write_guard_plugin.py` 逐例比对两代结论必须一致。有意不 `import { Plugin } from '@opencode/plugin'`：`Plugin.define` 只是定义辅助，手写的 `{ id, setup, server }` 与其返回值同形状，少一个依赖就少一个加载失败点（`test_opencode_v2_native.py` 钉住 id + setup + server 三要素）。
**② `AGENTS.md` 与 `CLAUDE.md`**：V2 **保留** `CLAUDE.md` fallback（目录里没有 AGENTS.md 就用 CLAUDE.md；`~/.claude/CLAUDE.md` 顶替缺失的全局 AGENTS.md），且**按目录各自择一**——根目录有 AGENTS.md 不再遮蔽子目录的 CLAUDE.md。所以 `sync-agents-md` 的镜像不是"V2 只认 AGENTS.md"的补丁（旧说法已作废），理由变为三条更稳的：① 让 V2 在任何目录都命中 AGENTS.md（显式存在优先于 fallback）；② 与 Claude Code 共用同一份说明；③ 逐字节一致，避免两份规则漂移。`--check` 已纳入 CI。
已确认无需改动：skills 目录式 + `name`/`description`（V2 明示不需重写）、`mode: subagent`（V2 仍接受 primary/subagent/all）、`.opencode/plugins/` 与 `.opencode/agents/`（已是 V2 推荐位置）。
**6 个 agent 已迁到 `permissions:`**（`migrate_agents_v2_permissions.py`，幂等可重复运行）。V1 文档明写 legacy `tools` 布尔配置"已废弃并并入 `permission`"，而**两代默认策略都是放行**（`{action:"*",resource:"*",effect:"allow"}`）——所以原来那 6 个 `tools: {read: true, ...}` **从未构成白名单**，只是把当时默认放行的东西又写了一遍。只把 true 翻成 allow 等于零约束，必须先 deny-all 再逐条放行。规则顺序即优先级（最后命中者生效），故 deny-all 必须排在最前。每个 agent 在 `shell` 之后显式放回 `external_directory: ask`——书项目在 `E:\workspace\webnovel2`，`deny *` 会连它一起拒掉，agent 立刻读不到正文与 state.json（迁移时真踩到，已被 `test_agent_v2_permissions.py` 钉死）。`glob`/`grep` 无条件放行：只读发现工具，原 `tools:` 没列但 V1 默认放行，deny-all 会收掉它们——那属于迁移自造的回归。
**本次收紧的**（相对原先"实际全放行"的行为）：`subagent`/`skill`/`webfetch`/`websearch`/`question` 一律 deny——子 agent 不该自行委派、联网或向用户追问（与 V2 出厂 `general` agent 策略一致）。`edit` 逐个判定：`reviewer`/`chapter-writer-agent`/`data-agent`/`observer-agent` 有（reviewer 正文要求"用 Write 工具将完整 JSON 写入 `${REVIEW_OUTPUT}`"）；`context-agent`/`deconstruction-agent` 无（后者正文明写"只返回结构化结果，不写任何文件"）。
**skill frontmatter 已按 V2 规范收敛**：V2 只解释 `name` / `description` / `metadata.opencode/autoinvoke` / `disable-model-invocation`。此前 3 个 skill 带的 `allowed-tools: Agent AskUserQuestion` 已**删除**——它在 V2 里不被解释，留着等于"看起来限制了工具其实没生效"，正是本项目反复栽的「声明了但没生效」。`compatibility` 是官方明确接受的可移植字段，保留。`test_opencode_v2_native.py` 钉住：16 个 skill 都有 `description`（没有它模型根本看不到该 skill）、无 `allowed-tools`、目录名与 `name` 一致（ID 由路径决定，`name` 只是展示名）。

### OpenCode v2 深度适配（.opencode/opencode.json + commands/）

`.opencode/opencode.json`（JSONC）是 **OpenCode 2 原生配置**，键集合与 https://opencode.ai/config.json 对齐：

- **`$schema` 必须写**：`"$schema": "https://opencode.ai/config.json"`。官方 config 页把它放进所有示例，编辑器校验与自动补全靠它。
  ⚠️ 旧断言已作废：2026-10 曾以「该地址返回的仍是 V1 形状 schema」为由刻意不写——现在 schema 已是 V2 形状，`test_opencode_v2_config.py::TestSchemaDeclaration` 反转钉住「必须声明」。
- **顶层 `permissions`（SSOT 写保护第一层）**：7 条 `edit` deny 覆盖 `.webnovel/state.json`、`index.db`、`vectors.db`、`memory_scratchpad.json`、`.story-system/events/`、`commits/`、`master_setting.json`，外加 `external_directory: ask`。
  规则语义：**最后匹配者生效**，全局规则先于 agent 规则且 agent 规则是**追加**不是替换；默认基线是 `{*:*:allow}` + `.env` 读 ask，所以我们的 deny 位于基线之后、必须生效。`TestNativeV2Shape` 逐条校验三元组形状、V2 action 名、7 条路径覆盖、`external_directory` 为 ask。
- **`external_directory: ask` 是必须显式写的**：V2 **默认允许**访问项目外（V1 是 ask）。本项目书项目常在仓库之外的 `E:\workspace\webnovel2`，不写就等于静默放行任意外部路径。
- **V1 不再支持（破坏性变更）**：V1 的配置 schema 是 `additionalProperties: false`，遇到顶层 `permissions` 会 rc=1 **拒绝整份配置**（2026-10 实测 1.18.34）。本项目现为 V2-only，需要 V1 请用 v3.1.x 及更早版本。README/INSTALL 已标注 requires OpenCode 2。
- **`formatter: false`（显式）**：V2 内置 prettier/biome **都覆盖 `.md`**，而本项目产物绝大多数是 `.md`。formatter 在写盘**之后**才跑，等于重排用户作品且拦不住。
- **`compaction`**：V2 形状 `keep.tokens`(24000) + `buffer`(20000)，取代 V1 的 `tail_turns`/`prune`。调高 keep 是为让刚写完那章原文尽量逐字留在尾部。
- **`watcher.ignore`**：屏蔽 `node_modules`/`__pycache__`/`.tmp`/`外部参考`，减少无意义的文件事件。
- **`tool_output`**：`max_lines`/`max_bytes` 取 V2 文档默认值 2000/51200，写出来只为长输出时尽早暴露截断。
- **不使用 `instructions`**：V2 **确实会加载**该键（/docs/config#instructions）。此前写的「V2 accepts this field but does not load its entries」是**错的**；本项目的理由改为「规则集中在单一 AGENTS.md，不需要额外文件清单」，而不是「该键无效」。

**SSOT 写保护现状（V2 原生，三层都在）**：① 顶层 `permissions` 的 7 条 `edit` deny——主 agent 也受约束，模型在权限判定阶段就看不到那些 edit；② 6 个 agent 各自的 `permissions`——子 agent 用**自己的**规则集，不是父集的子集（V2 明确如此）；③ `write-guard.js` 的 `ctx.tool.hook('execute.before')` 运行时抛错。
三层互补而非重复：①② 依赖 OpenCode 的权限流程，③ 是运行时兜底（绕过权限配置直接调 hook 也拦得住）。清单一致性由 `test_agent_v2_permissions.py::test_matches_write_guard_plugin_list` **双向解析** write-guard 的 `PROTECTED_SUFFIXES` 钉住（此前是子集断言——plugin 多一条仍然绿）；`test_opencode_v2_config.py::TestNativeV2Shape` 另钉住 config 侧 7 条路径全覆盖。

`.opencode/commands/` 5 个只读斜杠命令（`wn-status` / `wn-doctor` / `wn-ssot-verify` / `wn-where` / `wn-chapter-status`），用 `!\`shell\`` 块把 CLI 真实输出**在 prompt 提交前**注入，模型不必先猜再跑。**关键约束**：V2 文档 Warning 明确 shell 块"run when OpenCode evaluates the command, **outside the agent's tool permission flow**"——即 `write-guard.js` 拦不到它。所以 `test_opencode_v2_config.py` 断言 shell 块里**不得出现破坏性子命令**（`delete-chapters`/`rebuild`/`chapter-commit`/`publish`/`export`/…）；写路径必须走 skill + agent permissions + write-guard 链路。破坏性命令仍留 dry-run 默认 + `--apply` 二次确认。
实测 5 个命令的真书（`E:\workspace\webnovel2\凡尘之舞`）均正常执行；`ssot verify` 在真书上确实检出漂移并以 rc=1 退出（`foreshadowing` 0 vs 26、`entities_v3` 0 vs 4、`world_rules` 0 vs 10、`reader_promises` 0 vs 7），故 `wn-ssot-verify` 明确要求模型**不得自行 rebuild**。

**npm 离线包必须携带项目说明**（2026-10-09，对照 V2 迁移文档追出的三重缺口，已修）：`build-bundle.js` 原来只打包 `.opencode/`，仓库根的 `AGENTS.md`/`CLAUDE.md` 从未进包——已发布的 `2.9.2-12` 实测无此二文件，即 **npm 主安装路径在 V2 下没有项目说明**（且是静默失败：模型照常调用 skill 但读不到规则）。修复：① 构建时把两份文件追加为 tar 根条目（解压到工作区根、与 `.opencode/` 平级——正是 V2 的发现位置），缺失即拒绝出包；② 网络安装路径 PREFIX 限定 `.opencode/`、抽不到根文件，`init`/`update` 改用 `extractRootDocs`（`extract.js` 新增 `only` 白名单过滤）从源码 tarball 单抽，**必须在 `unlinkSync(tmp)` 之前**；③ 抽取失败不判安装失败，靠部署后的 AGENTS.md 存在性检查出声警告；④ `uninstall` 只删逐字节镜像（AGENTS==CLAUDE 才删），用户改过的说明保留。`test_npm_bundle_instructions.py`（11 例，需 node）钉住：构建产物含二文件且与仓库根逐字节一致、缺文件时构建失败、`extractRootDocs` 白名单不越权、init/update 调用顺序。

**OpenCode V2 实机验证（2026-10-10，opencode 2.0.26 全量实测）**：安装方式 `npm i -g --allow-scripts=@opencode/cli`（官方包，bin 同时提供 `opencode`/`opencode2`；⚠️ npm 上的 `opencode2` 包是**第三方分叉** `game-libgdx-unity/opencode2`，勿装；⚠️ 不带 `--allow-scripts` 时 postinstall 被 npm 门禁拦掉、选不了原生二进制）。回滚 V1：`npm uninstall -g @opencode/cli && npm install -g opencode-ai@1.18.34`。验证结论：
- 配置：当时是 4 键、V2 加载 rc=0。**现已扩到 7 键**（新增 `$schema` 与顶层 `permissions`），改变了「V1 也能加载」这个前提——本项目随之转为 V2-only，`test_opencode_v2_config.py` 的旧断言（4 键白名单、断言不含 `$schema`/`permissions`）已全部反转为 V2 原生断言。这次没有重跑真机 `opencode debug config`（本机无 opencode 二进制），配置形状按官方文档校对。
- 6 个 agent 全被发现，**但 frontmatter 的 `permissions` 全部错位到 `request.body.permissions`**（顶层只剩出厂默认 5 条，SSOT deny 零生效）。根因是 frontmatter 里的 **`name:` 行**——隔离实验矩阵：无 name→规则正确追加到顶层（默认在前、文件规则在后、最后匹配者生效）；有 name→必然错位；而 name 的值本身还被忽略（id/展示名取文件名）。已从 6 个 agent 删除 name（V1 侧 id 本就取文件名，两代行为不变）；`test_agent_v2_permissions.py::TestNoFrontmatterName` 钉死，`test_prompt_integrity.py::test_agent_frontmatter_complete` 的"name 必须有"**反转为"禁止有"**。修复后 `opencode debug agents` 确认：4 个带 edit 的 agent = 18 条（5 默认+13 文件规则）、2 个只读 agent = 11 条（5+6），deny-all 与 6 条 SSOT deny 均在顶层生效字段。
- **AGENTS.md 发现语义（npm 布局的成立依据）**：工作区根的 AGENTS.md 在三种 CWD 下均加载——工作区根本身、书子目录（书是独立 git 仓库）、书子目录（非 git），模型均可复述 marker。文档说 "outside home 时走到 project root 为止"，实测该 project root = **配置根**（含 `.opencode`/`package.json` 的目录），**书目录自身的 git 根不截断**向上发现。
- 16 个 skills 模型逐个可见；**write-guard 端到端拦截**：V2 主 agent 默认 allow-all 下，模型 Edit `.webnovel/state.json` 被 hook 拒绝（中文提示原样返回、文件未变）——兜底层独立成立。
- commands：V2 文档确认 `.opencode/commands/` 发现规则同 V1，frontmatter **只需 `description`、没有 name 字段**（id=文件名）——`wn-*` 形态本就符合；plugin-scanner 要求 name 是它自设约定，不是 OpenCode 规范。
- 操作坑：V2 `debug` 仅剩 agents/config/paths；共享服务 stop 后**首跑可能返回 `[]` 或只含内置 agent**（预热竞态，重试即可）；`--standalone` 对 debug 子命令无效。**未实测**：TUI 内 `/wn-*` 实调（机制同 V1，真书已验过）。

**未做及原因**：`$schema`（**已完成**——见配置章节；旧的「schema 还未跟上 V2」判断已作废）；顶层 `policies`（这是正式键，可硬 deny `tool.use`，但本项目已用 `permissions` 的 edit deny 达成同等效果，而 policies 的语义是「在权限判定之后硬拒」——属于冗余的第二道锁，暂不加，需要企业级强制时再加）；MCP 暴露 Python 数据层（需 `mcp.servers` + 给 6 个 agent 加 `<server>_<tool>` 放行规则，工作量中等且收益待观察）；FastAPI 面板**不可**迁进 OpenCode（插件没有 webview 宿主，ECharts 无等价物）。

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

### 发版（v3.0.1 起自动打 tag）

三个 workflow 分工，**不再需要人工推 tag**：

| workflow | 触发 | 做什么 |
|---|---|---|
| `semver.yml` | push master（改了非 manifest.json 的文件） | 按 Conventional Commits 推算下一个版本 → **打 annotated tag 并推送** |
| `manifest.yml` → `update-manifest` | push master（`.opencode/**`） | 用 `git describe` 的 tag + UTC 时间戳生成 `manifest.json`（`version` 形如 `v3.1.0-20261101.0930`），提交回 master |
| `manifest.yml` → `release` | push tag | 建 GitHub Release |
| `npm-publish.yml` | push master（`.opencode/**` / `.npm-package/**`）或 push tag | tag 路径发正式版（`npm version $TAG` + `publish`）；非 tag 路径发 `${BASE:-0.0.0}-N` prerelease |

**bump 规则**（实现在 `data_modules/semver_bump.py`，纯函数 + 49 例单测；
逻辑不埋在 YAML 里，因为 YAML 无法单测、写错只有推送后才知道）：

- 标题 `type!:` / `type(scope)!:`，或正文含 `BREAKING CHANGE:` → **MAJOR**
- 标题 `feat:` / `feat(scope):` → **MINOR**
- 标题 `fix:` / `refactor:` / `perf:`（可带 scope）→ **PATCH**
- 只有 `docs:` `ci:` `chore:` `test:` `simplify:` `style:` `build:` `revert:` → **不 bump、不打 tag**
- 取所有 commit 里的最高档；空列表或全被忽略 → 不发版

**被忽略的 commit**：含 `[skip ci]`（CI 自己产出的 manifest/release 提交）、
含 `[no release]`（显式逃生口）、`Merge ...` 合并提交、以及无法识别的 type。

⚠️ **真正的破坏性变更必须写进 commit**：自动化只读 message。v3.0.0 的「门禁分层」
是破坏性行为变更，但当时的 commit 写的是 `feat:`——按规则只会算成 MINOR，
MAJOR 是人手动定的。以后这类改动请写成 `feat!:` 或正文加 `BREAKING CHANGE:`。

**幂等**：目标 tag 已存在则跳过（重复运行不会重复打 tag）。

⚠️ **自动打 tag 的已知限制（实测踩过）**：GitHub 的防递归规则规定
**用 `GITHUB_TOKEN` 推送 tag 不会触发其它 workflow**。v3.1.0 首次自动打 tag 时
就是如此：tag 建出来了、但 `manifest.yml` 的 `release` job 与 `npm-publish.yml`
的 tag 路径**零个 run**，npm 仍是旧版本、GitHub Release 也没建。

因此 workflow 的当前行为是：**只有配置了 `SEMVER_PUSH_TOKEN`（带 `contents:write`
的 PAT）才会建 tag**；没配就**整步跳过**并在 step summary 里写明「推算出的是哪个
版本、为什么没建」。

为什么不「先建 tag、发不了版再说」：悬空 tag 会让**下一次推送的发布直接失败**
——`npm-publish.yml` 的非 tag 路径要求「npm 最新版与 git tag 同 base」，不同
base 时它 `exit 1`。实测 v3.1.1 就产生过这种悬空 tag（tag 有、npm 停在 3.1.0），
必须手工删掉才对回来。宁可这次不动，也不制造不一致。

启用自动化只需一步：仓库 Settings → Secrets and variables → Actions →
添加 `SEMVER_PUSH_TOKEN`（PAT，`contents:write`）。建 token 的逐步操作、
验证方法与常见故障见 [`docs/operations/release-token.md`](docs/operations/release-token.md)。在此之前不影响其它任何流程
（`manifest.yml` 仍按旧 tag 生成 manifest、`npm-publish` 仍发 `${BASE}-N` prerelease）。

**只推 tag、从不推 master**：master 由 `manifest.yml` 维护，两个 workflow 同时写
同一分支会产生非快进冲突；`manifest.yml` 已加 `concurrency` 串行化自身的
`update-manifest` 与 `release`（自动打 tag 后两者会在同一次发布里前后脚运行）。

**版本号仍以 git tag 为准**，不是 `manifest.json` 字段。

### 示例

```bash
git commit -m "feat: add HTML export format"     # → MINOR，如 v3.1.0
git commit -m "fix: resolve JSON corruption"     # → PATCH，如 v3.0.1
git commit -m "docs: update install guide"        # → 不发版
git commit -m "feat!: drop Python 3.9 support"    # → MAJOR，如 v4.0.0
```

### 注意

- **不要**在提交里手动改 `manifest.json` 版本号：`update-manifest` job 会覆写它
- 「多个提交一起 push → 取最高优先级的 bump」**成立**（`highest_bump` 取最大值）
- 上表的「版本影响」现在是**真实自动化行为**，不再是约定
- 想跳过自动发版但保留提交：commit message 里带 `[no release]`

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