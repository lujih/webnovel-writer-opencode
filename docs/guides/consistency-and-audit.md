# 一致性与审计：怎么用、失败时看哪里

> 这份文档回答一个比「有哪些功能」更实际的问题：**出了问题，我该看哪一条、敲哪一条命令。**
> 架构细节见 `docs/architecture/`，能力清单见 README §4.3。

---

## 一、五层防线（从硬到软）

| 层 | 机制 | 什么时候生效 | 你看到什么 |
|---|---|---|---|
| **硬约束** | Canon Locks | 章节提交时 | 拒章 + `payload.canon_conflicts` |
| **技术门禁** | `build_commit` 的 rejected 表达式 | 章节提交时 | 拒章 + `meta.rejection_reasons` |
| **质量度量** | CED 三口径 + 错误分类 | 每次审查后 | `review_metrics` 表 + 面板图表 |
| **观察标记** | `review_flags` | 章节提交时（语义问题） | commit 里的标记，待人工仲裁 |
| **事后审计** | `changes` / `run-log` | 任何时候 | CLI 列表 + 面板 `/changes` |

关键区别：**前两层会拦住你，后三层只告诉你**。被拦时看前两层；想复查历史看后三层。

---

## 二、排障决策树

### 2.1 章节被拒了（`status: rejected`）

先看 `payload` 里到底哪一条触发：

```bash
python .opencode/scripts/webnovel.py changes list --chapter 12   # 这章改了什么
```

| `rejection_reasons` 里的关键字 | 含义 | 处置 |
|---|---|---|
| `missed_raw` / `missed_cbn` | 章纲节点缺失（裸串 / 核心情节点） | 补写该节点，或改章纲 |
| `disambiguation pending` | 实体消歧未决 | 跑 `entity-clean` 看脏实体，人工确认指代 |
| `review_parseable=0` | 审查输出不可解析 | 重跑审查（多半是模型输出格式坏了） |
| `canon_conflicts` 非空 | 与作者钉死的设定冲突 | 见 2.2 |

**注意**：LLM 报的 critical / high **不再拒章**（v3.0 门禁分层），它们降级为 `review_flags`。所以「审查说有大问题但章节通过了」是**设计如此**，不是漏判——去 `flag-triage` 处理。

### 2.2 与 canon lock 冲突

```bash
python .opencode/scripts/webnovel.py canon list --project-root <PATH>
```

冲突意味着：这章把某个 `(实体, 字段)` 改成了与锁定值不同的值。

- **AI 写错了** → 改章节内容
- **作者改主意了** → `canon lock` 覆盖（同字段再跑一次即覆盖，不报错），或 `canon unlock --id <id>`

只比字符串**完全相等**。「斗师」与「斗师巅峰」是否等价由你决定，不由编辑距离决定。

### 2.3 投影失败了（某章提交成功但 state/index 没更新）

```bash
python .opencode/scripts/webnovel.py run-log list --limit 10
```

输出形如：

```
❌ 第012章 failed 失败=index  (2026-11-27T…)
   ↳ 下一步：python …/webnovel.py projections retry --chapter 12（只重跑该章投影；仍是失败再考虑 `ssot rebuild`）
```

按建议重跑：

```bash
python .opencode/scripts/webnovel.py projections retry --chapter 12
```

它是**幂等**的：该章已有事件时不会重复写入不可变事件日志，writer 本身是覆盖式投影。

仍失败 → 再往上：

```bash
python .opencode/scripts/webnovel.py ssot verify    # 事件日志 vs state.json 漂移
python .opencode/scripts/webnovel.py ssot rebuild   # 从事件日志重建全部投影
```

`ssot rebuild` 会保留非事件字段（`project_info` / `chapter_meta` 等 10 个白名单 + `progress` / `plot_threads` / `relationships` 的专用合并），但仍是**最重**的一步——能用 `projections retry` 就别用它。

### 2.4 怀疑设定在章节间漂了

```bash
# 最近 30 章里「改错代价大」的变更 + 违背 lock 的记录
python .opencode/scripts/webnovel.py changes audit --limit 30

# 两章之间的差异（removed 组 = 字段静默消失，通常是投影漏了一条）
python .opencode/scripts/webnovel.py changes diff --from 10 --to 12
```

风险分级按本项目领域结构：`realm` / `cultivation` / `status` / `alive` 是 **high**（境界或生死写错会让后面几十章失据），`location*` / `relationship*` 是 **medium**，其余 low。

面板 `/changes` 有同样的三个视图，且 `removed` 用红框标出。

### 2.5 审查报了一堆问题，想判断「误判率」

```bash
python .opencode/scripts/webnovel.py flag-triage list --chapter 12
python .opencode/scripts/webnovel.py flag-triage confirm --chapter 12 --index 0
python .opencode/scripts/webnovel.py flag-triage dismiss --chapter 12 --index 1
```

`confirm` / `dismiss` **只写回 `review_flags[i].triage`，不改章节状态**。真矛盾由你显式跑 `webnovel-heal`。
面板「审查分析」页会显示**误判率**（dismissed / triaged），这是判断「审查是否值得信」的唯一数据来源。

---

## 三、度量口径：CED 与错误分类

### 3.1 CED 三口径

定义在 `data_modules/review_schema.compute_ced()`（唯一定义处）：

| 口径 | 公式 |
|---|---|
| `ced_raw` | issues 数 / 单章字数 × 10000 |
| `ced_weighted` | Σ severity 权重（critical 4.0 / high 3.0 / medium 2.0 / low 1.0）/ 字数 × 10000 |
| `ced_blocking` | blocking issues / 字数 × 10000 |

**两个容易误读的点**：

1. **中文口径不同单位**。`_count_chapter_words()` 用 `len()` 计字符，而 ConStory-Bench 用英文 word count——**不可横向比数值**，只能比同口径趋势。
2. **`None` ≠ `0.0`**。分母为 0（章节文件缺失/空文件）时三口径是 NULL；审查输出不可解析时（`review_parseable=0`）也不产出 CED。看到曲线断开，先查这两类原因，别当成「这章没问题」。

### 3.2 错误分类

审查 issue 带 `error_type`（5 大类 / 19 子类型）与 `evidence_start/end`（原文字符偏移）。
分类计数落库 `review_metrics.error_category_counts`，面板「一致性错误分类分布」展示。

**为什么分类比总量有用**：总量会被轻微文风问题稀释。世界规则类偏高 → 补合同；时间线类偏高 → 补时间线提取。只看总量会把这两件事混成一个数。

---

## 四、能力边界（**不**做什么）

| 边界 | 原因 |
|---|---|
| canon lock 只比字符串全等 | 「斗师」与「斗师巅峰」是否等价应由作者判断，编辑距离会替你决定 |
| `changes` **不拦截**任何提交 | 它只回答「改了什么」。真候选（先 accept 再投影）需改提交链，且会打断 `orchestrate write "1-50"` 的批量流水线 |
| `projections retry` 跳过 precommit gate | gate 检查「能否入库」，retry 处理的是**已在库里**的产物。用 gate 拦 retry 会让「gate 因故变严」的历史章节永远修不好 |
| `flag-triage` 的 confirm 不自动 heal | 一次点击就改作品太危险；真矛盾由作者显式跑 `webnovel-heal` |
| 面板不可迁进 OpenCode | 插件没有 webview 宿主，ECharts 无等价物 |

---

## 五、性能基线（判断「改慢了没有」）

```bash
python .opencode/scripts/benchmarks/benchmark_core.py          # 6 项关键路径
python .opencode/scripts/benchmarks/benchmark_core.py --quick  # 冒烟
```

判定阈值（同机、同数据）：中位数涨幅 **< 20%** 视为噪声；**20%–2×** 检查是否引入多余 IO；**> 2×** 视为退化。

完整数字与三条读解见 `.tmp/analysis/BASELINE.md`。**不要**把耗时写进 pytest 断言——共享 runner 上的绝对耗时没有意义。
