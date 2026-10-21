# 发行说明 · v3.0.0

> **v2.9.2 → v3.0.0**（2026-06-22 → 2026-10-21，186 个提交：12 个 feat、91 个 fix、9 个 test、74 个 docs/refactor/simplify/ci/chore——其中 46 个为 CI 触发的 manifest 更新 [skip ci]）。
> 这是 v2.9.2 之后的第一个 MAJOR 版本：三项新能力 + 一处**破坏性行为变更**（门禁分层），外加两轮全量审查沉淀下来的 100 项修复。

---

## ⚠️ 升级须知（v2.9.x → v3.0.0 行为变更）

> **v3.0.0 的提交门禁行为与 v2.9.x 不同。升级前请确认你的工作流不依赖「LLM 报 critical 就拒章」。**

| 变化 | v2.9.x | v3.0.0 |
|---|---|---|
| LLM 审查报 critical/high | **拒章**（`has_blocking` → rejected） | **放行 + `review_flags` 观察标记**，由 `flag-triage` 人工裁决 |
| 拒章原因 | 语义 + 技术混合 | 仅三类技术故障：章纲节点缺失 / 消歧未决 / 审查输出不可解析 |
| `review_metrics` 表 | 9 列 | **自动 ALTER 加 5 列**（`chapter_words` / `ced_raw` / `ced_weighted` / `ced_blocking` / `review_parseable`），旧库无迁移风险 |
| 既有 rejected 章节 | — | 历史数据不受影响（历史 rejected 是历史状态） |

如果你依赖「审查不通过就不提交」：升级后请改跑 `python webnovel.py flag-triage list --chapter N` 人工裁决，再决定是否 `webnovel-heal`。

---

## 三项新能力

### 1. 一致性错误密度（CED）

审查指标从**过程指标**（清单完成度、审查均分——高分只代表"清单项打勾了"）升级为**质量指标**（一致性错误密度——回答"改进是否真的在减少错误"）。

- 三口径定义在 `review_schema.compute_ced()`（唯一定义处）：
  - `ced_raw` = issues / 单章字数 × 10000
  - `ced_weighted` = Σseverity 权重（critical 4.0 / high 3.0 / medium 2.0 / low 1.0）/ 单章字数 × 10000
  - `ced_blocking` = blocking issues / 单章字数 × 10000
- **中文口径**：`len()` 计字符即「字数」，与 ConStory-Bench（arXiv 2603.05890）的英文 word count 单位不同——不可横向比数值，只能比同口径趋势
- **`None` 与 `0.0` 是两件事**：分母为 0（章节文件缺失 / 空文件）时三口径为 NULL，不得除以 0 也不得伪造 0——「算不出」与「真的没发现问题」必须区分
- 审查输出不可解析时不产出 CED（`ReviewResult.unparseable` → `review_parseable=0`），否则一次性 JSON 解析失败会在趋势图上留虚假尖峰
- dashboard 审查分析页展示 CED 趋势图（三口径切换，被排除章可见）

### 2. 叙事预报（Narrative Forecast）

写第 N 章前生成 2-5 个**相互隔离**的候选未来分支，选支后只把选中分支追加进章纲（inkOS 同源能力的本土化）。

- 三个固定走向：`conflict_escalation`（冲突升级）/ `payoff_retrieval`（伏笔回收，无伏笔债时降级去掉）/ `calm_transition`（平静过渡）
- **确定性生成器**：基于未闭合伏笔债 + 最近 3 章摘要 + 既有章纲，不依赖 LLM、不依赖随机数——给定相同输入产出相同候选（可重放、可测试）
- 对齐度：字符 2-gram Jaccard（无外部分词依赖）——高 = 与既有章纲吻合，低 = 选它意味着要改章纲
- 候选隔离在 `.story-system/forecasts/chapter_NNN/`，每候选一个 JSON，互不引用；未选中的候选保留磁盘作为参考
- 选支**幂等**：重复选支 / 改选支 → 章纲 forecast 块原地更新，不累积
- 纯增量可跳过：`webnovel-write` skill 默认不执行，仅当用户显式要求时走
- **CLI**：`webnovel.py forecast generate|select|list`（第 39 个顶层命令）

### 3. 门禁分层（review_flags）

提交门禁从「LLM 语义阻断」收窄为「技术阻断 + 语义标记」。

```
技术层（fail-closed，仍拒章）：
  missed_raw / missed_cbn   章纲节点缺失（可机判，零误判空间）
  disambiguation pending    实体消歧未决（无法确定指代，静默写错比不写更糟）
  unparseable               审查输出不可解析（「无法判断有没有问题」≠「没有问题」）

语义层（fail-open + 留痕）：
  LLM critical/high → review_flags（观察标记）
                        ├─ 写下一章前自动注入 context 警戒段（上限 3 条）
                        ├─ dashboard「待仲裁审查标记」区块（confirm / dismiss）
                        └─ 确认后走 webnovel-heal（人工显式入口，不自动触发）
```

- 动机：LLM 的 critical 不可靠——误判会杀死好章节；且旧实现把「纯 JSON 解析失败」也包成 critical 一并阻断（技术故障伪装成语义阻断）
- `confirm` / `dismiss` 只写回 `review_flags[i].triage`，**不改 commit status**——门禁分层的底线是「语义问题不拒章，仲裁只留痕」
- **CLI**：`webnovel.py flag-triage list|confirm|dismiss`（第 40 个顶层命令）
- **dashboard**：`/api/review/flags` GET + `/api/review/flags/triage` PUT

---

## 两轮全量审查沉淀（100 个 fix 的脉络）

v2.9.2 之后做了 L0-L4 自下而上 + A/B/C/D 正交两轴的第二轮深度审查，沉淀下来的修复集中在几条主线：

### 审查管线 fail-closed 闭环

- 审查载荷形状异常（`issues` 非数组、顶层非对象）**一律 fail-closed**——合成 critical 阻断项转人工，「无法判断有没有问题」≠「没有问题」，静默丢弃会让被否章节以满分通过进 SSOT
- 审查输出不是 JSON 时不再直接中断（畸形 JSON 走 fail-closed、非 JSON 走降级）
- `blocking_count` 写成字符串不再阻断整章提交（两条校验路径结论对齐）
- 模型报「3 个阻断但没列明细」时不再放行（审查阻断判据补全）

### 同名歧义消歧链路

- data-agent 的置信度分级拿不到触发条件 → 同名歧义上报到提取阶段（`02ab891`）
- settler 算出的同名歧义**只写不读** → 歧义信号终于接到处置链路（`7807cfc`）
- 角色状态变化全丢（settler 只给 description、下游要 field，1 条事件提取出 0 条 delta）→ 已修
- 别名同类型歧义不再静默吞掉（新增 `lookup_alias_detailed`）

### 事件溯源健壮性

- rebuild 不再静默丢弃未知事件类型（SSOT 灾难恢复不缺数据）
- 删章节先写事件再改 state（旧顺序下 rebuild 会把删除的章节复活）
- override 事件优先于文件（旧顺序下 rebuild 后世界规则覆盖会消失）
- `ssot verify` 对含未知事件的项目不再说「一致」（检查项自己漏了）
- `verify_consistency` 跨形状比较计数（健康项目不再恒定误报 drift）
- 伏笔债建销键不一致、空键销全部债、孤立时间线静默删除、WORKFLOW 进度点恒灰

### 提交链路安全

- **写入门禁接入提交链路**——此前 400 行门禁是死代码，一次都没被调用（`708d8cf`）
- state.json 损坏时 fail-closed，不再静默清空整本小说的状态
- 写路径失败时报成功 → 现在如实上报（`_run_data_module` 不再一律返回 0）
- 回滚不再脱离 HEAD / 不再半途而废；git 失败如实上报；降级备份覆盖正文
- 项目根解析以 CWD 为先——人站在哪本书就操作哪本书（避免写错书）
- 空项目根被解析成当前目录 → 环境变量留空不再「写错书」

### 数值 / 统计正确性

- 空清单得满分、无 required 项白送 30% → 评分公式不再把「没有依据」当成「全部达成」
- 趋势报表 required 完成率对新书恒为 100%、脏数据能算出 250% → 已修
- 紧急伏笔筛选遇脏 urgency（'80' 与 None）直接崩掉整章写作 → 已修
- `hybrid_search` 把显式传的 0 当成「没传」（静默换成配置默认值）→ 已修
- RAG backtrack 的 top_k 被绕过（有 parent 时返回 2 倍，token 预算全失准）→ 已修
- 文风样本请求 3 条只给 2 条，且 20 个样本永远只用前 5 个 → 已修

### SSOT 投影一致性

- `ssot rebuild` 保留非事件顶层字段（P0 防擦除）
- `rebuild` 写扁平的 `entities_v3` + 事件重放按数值序 → 统一为嵌套形状
- `progress` 子字段 / `plot_threads.active_threads` 不再被 `ssot rebuild` 擦除
- `relationships` 类型翻转被纠正

### 安装器 / 供应链

- npm 离线包必须携带 dashboard 前端产物（此前发布的包根本没有前端）
- 安装器校验 sha256（此前只比 content-length，恶意镜像可注入 AGENTS.md）
- dashboard 校验 Host 头（堵住 DNS rebinding 的读取路径）
- manifest 按 git 索引取文件集并归一化行尾（发布包不再混进本地产物）
- npm 发布改用 Trusted Publishing（OIDC），不再依赖长期令牌
- OpenCode V2 适配——移除 `opencode.json` 的 V2 `permissions` 键（它会让全部 V1 用户无法启动）
- npm 离线包必须携带 AGENTS.md / CLAUDE.md（主安装路径在 OpenCode V2 下原本没有项目说明）

### dashboard 加固

- 运维诊断页（第 11 个页面）+ 追读债面板补齐
- 三个 P0：反模式写入自锁、雷达图空白、换端口后写操作全挂 → 已修
- CSRF 守卫覆盖全部写端点（此前只有 `/api/actions/*` 有回归保护）
- 写端点断言副作用（堵住「500 也算通过」的测试盲区）

### 其它

- `find_chapter_file` 漏掉 1-2 位编号（`第7章.md` 定位不到），且 3 位优先于 4 位（与「4 位是项目标准」矛盾）→ 已修（CED 字数的分母可靠性地基）
- 七猫发布匹配不到分类就静默归到第一个（科幻会被发到玄幻分类下）→ 已修
- 题材分节用子串匹配（写「都市」拿到「都市异能」的写作规则）→ 已修
- 15 个规范题材里 13 个拿不到英文 profile key（方法论策略卡静默不生效）→ 已修
- 权限迁移脚本会把 read 提权成 edit、并静默丢掉自定义 resource 规则 → 已修
- `chapter-rename` 扫错目录（对着投影目录做章节编号统一，永远找不到正文）→ 已修
- `entity-clean` 重复运行让 invalid_facts 无限膨胀、且会推翻已完成的复核 → 已修

---

## 数据 / 性能

- `review_metrics` 表自动 ALTER 加 5 个新列（幂等迁移函数 `IndexManager._migrate_review_metrics_columns`，旧库无迁移风险）
- 全套测试 **2344 passed / 3 skipped**，新增测试文件 5 个（`test_ced_integration.py` / `test_dashboard_ced.py` / `test_chapter_file_locating.py` / `test_narrative_forecast.py` / `test_flag_triage.py`），测试文件总数 164 / 用例 2349
- 新增顶层 CLI 命令 2 个（`forecast` / `flag-triage`），现共 40 个
- `manifest` 当前 586 个文件条目

## 文档

- `README.md`：2.2 项目描述追加「v3.0 起」三项能力；4.3 关键子系统表加 3 行
- `CLAUDE.md` / `AGENTS.md`：追加「门禁分层（review_flags，v3.0）」段；CLI 命令清单 40 个（含 `forecast` / `flag-triage`）
- `docs/guides/commands.md`：运维子命令表加 `forecast` 与 `flag-triage`
- `.npm-package/README.md`：不动（安装器说明，不描述被装系统能力）

## 已知限制

- `accrue-interest` 仍为手工 CLI，未自动接入 `chapter-commit`（行为变更影响所有既有项目的债务值，需单独评估）
- `observer-agent` 仍为 free-text 提取（无 schema 约束，coverage-first）
- `data-agent` 的 prompt 不读 `ambiguous_mentions`（代码路径 `ambiguous_to_pending()` + fail-closed pending 已兜底）
- 三个 skill 的 `allowed-tools` 在 OpenCode V2 不被解释（V2 只认 `name` / `description` / `slash` / `metadata`；对两代宿主都是惰性键，保留是为兼容 Claude Code / Agent Skills 生态）
