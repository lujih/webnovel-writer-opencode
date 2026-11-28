# `changes` 工具链：五分钟上手

> 六条命令，回答六个不同的问题。示例输出来自真书 `凡尘之舞`（58 章）。
> 全部**只读**——没有任何一条会改你的书。

---

## 0. 它们各自回答什么

| 命令 | 问题 | 什么时候跑 |
|---|---|---|
| `preview` | 这份稿子**将要**改什么？ | 提交**之前** |
| `list` | 这一章改了什么？ | 想核对单章 |
| `diff` | 两章之间变了什么？ | 怀疑中间某步出问题 |
| `audit` | 最近哪些章值得停下来看？ | 定期巡检 |
| `trace` | 这个字段历史上怎么变的？ | 怀疑某字段漂了 |
| `candidates` | 哪些字段值得钉死？ | 想用 canon lock 但不知锁什么 |

---

## 1. `preview` —— 提交前预演

```bash
python .opencode/scripts/webnovel.py changes preview \
  --project-root <书目录> --file .story-system/commits/chapter_020.commit.json
```

```
预演：这份 payload 将产生 9 项状态变更（高危 2、中危 1）。

高危变更（改错代价大）：
  tianyan_zhang_supervisor.status: 受伤存活 → 死亡（能量刃切断颈右侧…）
  tianyan_guard.status: 折返灵材铺途中 → 已从东南方向返回…
```

**怎么读**：「高危」= `realm` / `status` / `alive` 这类字段（按本书领域结构分级，不是通用规则）。
如果你锁过 canon lock 而这份 payload 撞了，这里会直接写「会因违背 canon lock 被拒章」。

**注意**：它**不阻断**。看完仍可提交——它只是把「事后才发现」变成「事前就知道」。

---

## 2. `list` —— 单章全量

```bash
python .opencode/scripts/webnovel.py changes list --project-root <书目录> --chapter 20
```

```
第 20 章的状态变更（9 项）：
  chen_sheng.chip.neural_load: 92 → 105  [state_deltas]
  chen_sheng.chip.remaining_uses: 28 → 25  [state_deltas]
  tianyan_zhang_supervisor.status: 受伤存活 → 死亡（能量刃切断颈右侧…）  [state_deltas] ⚠️
```

加 `--high-risk` 只看 high/medium。标记说明：`⚠️` 高危、`·` 中危、`🔒` 违背锁定。

---

## 3. `diff` —— 两章差异

```bash
python .opencode/scripts/webnovel.py changes diff --project-root <书目录> --from 19 --to 20
```

输出三组：**变化** / **新增** / **消失**。

**`消失` 组最重要**：某字段在前一章有、后一章没有，通常是**投影漏了一条**，而不是你真的删了它。纯表格视图完全看不出这种静默丢失。

---

## 4. `audit` —— 巡检

```bash
python .opencode/scripts/webnovel.py changes audit --project-root <书目录> --limit 30
```

```
需要人看一眼的变更（34 项）：
  第019章 tianyan_zhang_supervisor.status: 正常（健康） → 受伤（…）  [⚠️高危]
  第020章 tianyan_guard.status: 折返灵材铺途中 → 已从东南方向返回…  [⚠️高危/↻重复 2 次（第 20,24 章）]
```

只列两类：**高危字段**与**违背 canon lock** 的。低危（发色、情绪）不进报告——否则关键项会被淹没。

**`↻重复 N 次`** 表示同一条变更在多章出现，通常意味着**投影重复应用了同一份 delta**。

---

## 5. `trace` —— 单字段时间线

```bash
python .opencode/scripts/webnovel.py changes trace \
  --project-root <书目录> --entity chen_sheng --field chip.remaining_uses
```

```
chen_sheng.chip.remaining_uses 的变更时间线（13 次，按章升序）：
  第016章 30 → 29
  第017章 29 → 29  ≈无变化
  ...
  第024章 28 → 25  ↺回退
  另有 5 条「无变化的空变更」（old == new）——投影把未变的字段也记成了 delta
```

两种标记要分清：

- **`↺回退`**：新值等于更早出现过的值。状态机式字段（剩余次数、境界、存活）回退**几乎总是 bug**；情绪/位置类回退是剧情正常波动。
- **`≈无变化`**：`old == new` 的空 delta——投影噪声，不是回退。

---

## 6. `candidates` —— 哪些字段值得钉死

```bash
python .opencode/scripts/webnovel.py changes candidates --project-root <书目录>
```

```
值得考虑锁定的字段（4 个）：
  sun_bai.status（第31–63章，改 5 次）
    最新值：已死亡（遗体被发现于溪沟，确认死于能量刃）
  chen_sheng.realm（第8–50章，改 3 次，≈空变更×2）
    最新值：首杀筑基期修士（孙柏）——…
```

**这里有个必须由人做的区分**：

| 成因 | 该怎么做 |
|---|---|
| 「角色已死却还在改状态」 | **锁**（`sun_bai.status`） |
| 剧情推进（境界突破、任务阶段） | **绝不锁**——锁了立刻阻断写作 |

工具只给统计（变更次数 / 回退次数 / 空变更次数），**不替你区分**。看到候选先跑 `trace` 看时间线，再决定。

锁的命令：

```bash
python .opencode/scripts/webnovel.py canon lock \
  --subject sun_bai --field status --value '已死亡（遗体被发现于溪沟，确认死于能量刃）' \
  --rationale '第63章确认死亡' --chapter 63
```

⚠️ 锁的值必须与 projection 里存的字符串**逐字一致**（含标点括号）——`canon lock` 只比全等。

---

## 7. 面板上的同一套东西

`/changes` 页有三个视图：单章表格、相邻章 diff（`removed` 红框）、风险过滤。数据与 CLI 完全一致——同一份 commit 文件。

---

## 常见误读

| 误读 | 事实 |
|---|---|
| 「审查没报问题 = 没问题」 | 审查会漏报；`audit` 看的是**提交记录**，不会漏 |
| 「`candidates` 列出来的都该锁」 | 频繁变更有两种成因：数据脏（该修）与剧情推进（别锁） |
| 「`preview` 说会拒章 = 已经拒了」 | 它只是预演。你没提交，什么都没发生 |
| 「`diff` 的消失组是真的删了字段」 | 多半是投影漏了一条，去查 `run-log` |
