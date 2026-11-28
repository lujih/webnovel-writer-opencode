# 发行说明 · v4.1.0

> **v4.0.0 → v4.1.0**（十一轮以外部最佳实践为输入的持续重构）
> 主题：从「能写」到「改动可验证」。新增 **错误分类法**、**Canon Locks**、**变更审计**、**性能基线**，
> 并把它们全部接到 CLI 与面板上。
>
> ⚠️ **状态**：本文档对应的工作树改动**尚未提交**。数字来自本地完整套件（Windows / Python 3.12），
> CI 尚未对本批改动跑过。提交时请连带 `dist/`（前端构建产物），否则 `test.yml` 的
> `git diff --quiet -- dist` 会红。

---

## 升级须知（v4.0.0 → v4.1.0）

**无破坏性变更**。两处自动迁移，旧库无需手工处理：

| 变化 | 说明 |
|---|---|
| `review_metrics` 表 | 自动 ALTER 增加 `error_category_counts TEXT DEFAULT '{}'`，沿用 `_REVIEW_METRICS_NEW_COLUMNS` 的幂等迁移 |
| 新增文件 | `.story-system/canon_locks.json`（不存在即视为无锁定）、`.webnovel/projection_log.jsonl` 早已存在 |

**行为变化（新增，不影响既有路径）**：accepted 章节在 `build_commit` 阶段会做一次 canon lock 比对；
若你的书里**没有** `canon_locks.json`，这一步是空操作。

---

## 四项新能力

### 1. 错误分类法（ConStory-Bench）

审查 issue 从「一段描述」升级为**可统计的错误类型**：

- `error_type`：5 大类 / 19 子类型（`timeline_plot` / `characterization` / `worldbuilding` / `factual_detail` / `narrative_style`）
- `evidence_start` / `evidence_end`：证据在原文中的字符偏移；半截或反序的偏移会被**同时清空**，而不是降级成别的类型
- `ced_by_category`：分类口径的 CED
- 分类计数落库 `review_metrics.error_category_counts` → `IndexManager.get_error_category_totals(limit, since_chapter)` 聚合 → 面板「一致性错误分类分布」条形图

**为什么分类比总量有用**：总量会被「轻微文风问题多」稀释。世界规则类偏高该补合同，时间线类偏高该补时间线提取——这两件事只看总量会混成一个数。

### 2. Canon Locks（作者钉死的设定）

外部实践来自 NarraLume 的 locked facts：**AI 不能静默改 canon**。

```bash
python .opencode/scripts/webnovel.py canon lock \
  --project-root <PATH> --subject xiaoyan --field realm --value 斗师 \
  --rationale "第12章确立" --chapter 12
python .opencode/scripts/webnovel.py canon list --project-root <PATH>
python .opencode/scripts/webnovel.py canon unlock --project-root <PATH> --id xiaoyan.realm
```

- `build_commit` 在拼 payload 前对 `accepted_events` 做**确定性**比对：同 `(subject, field)` 新值 ≠ 锁定值 → **rejected**（技术层，与章纲节点缺失同级），明细写进 `payload["canon_conflicts"]`
- 只比字符串**完全相等**——「斗师」与「斗师巅峰」是否等价由作者决定，不由编辑距离决定
- 锁定文件损坏时**写入中止**（`CanonLocksCorrupted`）；读路径降级为空但 CLI 可感知
- 存 `.story-system/canon_locks.json` 而**不是** `state.json`——后者有 rebuild 非事件字段白名单问题，作者的硬约束不该依赖投影层的记忆

### 3. 变更审计（changes）

accepted 章节的 `state_deltas` 此前直接投影进 `state.json`，中间没有可读中间态。三条命令把它摊开：

```bash
webnovel.py changes list  --chapter 12 [--high-risk]   # 这章改了什么（全量）
webnovel.py changes diff  --from 10 --to 12            # 两章之间变了什么（三态）
webnovel.py changes audit --limit 30                   # 哪些章值得停下来看（摘要）
```

- **风险分级**：`realm` / `cultivation` / `status` / `alive` 等为 high（境界或生死写错会让后面几十章失据），`location*` / `relationship*` 为 medium
- **锁违背回溯**：`annotate_changes()` 给每条变更标 `locked_value`。lock 通常是**事后**才钉的，历史章节里可能已存在违背它的记录——`build_commit` 的写时拦截只管以后，这里回答「过去有没有违背」
- **归一化必须与投影一致**：`test_state_change_log.py` 用参数化交叉验证钉住 `collect_changes()` 与 `StateProjectionWriter._collect_state_deltas()` 对同一 payload 得出相同的 `(entity_id, field, new)` 三元组
- 面板 `/changes` 页：单章表格 + 相邻章 diff（`removed` 红框标注「可能是投影漏了一条」）
- **不做候选闸门**：真候选（先 accept 才投影）需改提交链，且会打断 `orchestrate write "1-50"` 的批量流水线，留待产品决策

### 4. 性能基线

前十一轮每轮都报「全绿」，但全绿只说明**正确**，不说明**不变慢**：

```bash
python .opencode/scripts/benchmarks/benchmark_core.py          # 表格
python .opencode/scripts/benchmarks/benchmark_core.py --json   # 机器可读
python .opencode/scripts/benchmarks/benchmark_core.py --quick  # 冒烟
```

本机基线（30 章 × 30 变更）：`collect_changes` 0.020 ms、`annotate_changes` 0.031 ms、
`audit_changes` 9.97 ms、`detect_conflicts`（200 事件×30 锁）0.361 ms、`compute_ced`（200 issues）0.031 ms、
`save_review_metrics` ×10 + 聚合 21.9 ms。完整表与判定阈值见 `.tmp/analysis/BASELINE.md`。

**不写耗时断言**——CI runner 之间差异巨大，写死阈值只会制造 flaky。`test_benchmarks_smoke.py` 只验证脚本能跑通、结构正确。

---

## 新增接口一览

| 类型 | 名称 | 说明 |
|---|---|---|
| CLI | `canon lock\|unlock\|list` | 第 41 个命令 |
| CLI | `run-log list` | 第 42 个命令（投影运行记录 + 可执行恢复命令） |
| CLI | `changes list\|diff\|audit` | 第 43 个命令 |
| API | `GET /api/changes/list?chapter=N` | 单章全量变更 |
| API | `GET /api/changes/audit?limit=N` | 最近 N 章关键项（高危 + 违背锁定） |
| API | `GET /api/changes/diff?from=A&to=B` | 两章差异（changed/added/removed） |
| 页面 | `/changes` | 变更审计（第 12 个页面） |
| 面板卡 | 审查分析页 | 「一致性错误分类分布」「需要人看一眼的变更」 |

---

## 验证

| 项 | 结果 |
|---|---|
| 后端完整套件 | **2739 passed / 4 skipped / 0 failed**（`pytest .opencode/scripts/data_modules/tests`） |
| 前端单元/组件测试 | **153 passed / 9 files**（`cd .opencode/dashboard/frontend && npm run test`） |
| 前端 lint | 0 errors（26 条既有 react-hooks warning） |
| 前端 build | 通过，`dist/` 已重建 |
| 文档一致性 | `sync-agents-md --check` 通过；`test_readme_accuracy` 通过（182 测试文件 / 2739 用例 / 79 模块 / 43 子命令） |

**本轮新增测试**：`test_review_error_taxonomy.py`（38）、`test_canon_locks.py`（25）、
`test_context_relationship.py`（11）、`test_review_error_category_persistence.py`（14）、
`test_projection_run_log.py`（11）、`test_state_change_log.py`（32）、
`test_dashboard_changes_audit.py`（14）、`test_benchmarks_smoke.py`（5）、前端 `ChangesPage.test.jsx`（11）。

---

## 未做与原因

| 差距 | 为什么没做 |
|---|---|
| G3 候选闸门（state delta 先 accept 再投影） | 需改提交链契约，且会打断批量写作流水线；缺使用场景数据（逐章把关 vs 批量生成后统一修订） |
| G8 token 熵预警 | 需要模型 logprob，OpenCode 侧拿不到 |
| G9 GRR（生成-修订-重评一致性） | 单用户无对比组，学术指标无法在本项目落地 |
| G10 异构记忆分层 | 最大工程，收益不确定 |

---

## 已知缺口（本轮顺手发现的）

- **本机前端验证链**：`node_modules` 可能只装了 dependencies，`npm run test` 报 `'vitest' is not recognized`。CI 用 `npm ci` 装全所以 CI 是绿的——这种「本地跑不了所以不跑」最容易长期潜伏。`npm install` 即可补齐。
- **`dist/` 是提交进 git 的**：`test.yml` 的 frontend job 最后一步校验 `git diff --quiet -- dist`，改前端源码必须一并提交构建产物。
