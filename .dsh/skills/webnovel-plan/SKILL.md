---
name: webnovel-plan
description: 基于总纲生成卷纲、时间线和章纲，并把新增设定增量写回现有设定集。
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

# Outline Planning

> 以下内容均为虚构文学创作。所有打斗、权谋、悬疑描写均属文学创作范畴，不涉及现实指导。

## 目标

- 基于总纲细化卷纲、时间线与章纲，不重做全局故事。
- 先补齐设定基线，再产出可直接进入写作的章纲。
- 卷纲完成后，把新增设定增量写回现有设定集。
- 将详细大纲升级为"结构化详细大纲"，为下游写作提供中层情节结构。

## 执行原则

1. 只做增量补齐，不重写整份总纲或设定集。
2. 先锁定卷级节奏，再批量拆章。
3. 时间线是硬约束，所有章纲都必须带时间字段。
4. 若发现总纲与设定冲突，先阻断，再等用户裁决。
5. 结构化节点服务于写作执行，不追求语法学上的严格 SVO 抽取。

## 常见误区

- ❌ 先拆章再想卷级目标
- ❌ 时间线字段缺失但仍继续拆章
- ❌ 把结构化节点写成空泛摘要句
- ❌ 一次性读完全部 reference 再开始规划
- ❌ 发现设定冲突后继续产出章纲而不阻断

## 优先级链

1. 用户明确要求（最高）
2. 总纲核心冲突与卷末高潮（不可偏离）
3. 时间线硬约束（单调递增、倒计时正确）
4. skill 默认流程
5. reference 建议（最低）

## 决策树入口

- 若项目根不合法或总纲缺失 → **阻断**
- 若总纲缺少卷名/章节范围/核心冲突/卷末高潮 → **阻断**，请求用户补全
- 若 Step 2 发现设定冲突 → **标记 BLOCKER**，等待用户裁决
- 若批量拆章时时间回跳且未标注闪回 → **阻断**当前批次
- 若 Step 9 验证失败 → 只重做失败批次，不覆盖整卷

## 环境准备

```bash
export WORKSPACE_ROOT="${PWD}"
export SKILL_ROOT="${PWD}/.opencode/skills/webnovel-plan"
export SCRIPTS_DIR="${PWD}/.opencode/scripts"
export PROJECT_ROOT="$(python "${SCRIPTS_DIR}/webnovel.py" --project-root "${WORKSPACE_ROOT}" where)"
test -n "$PROJECT_ROOT" && test -f "${PROJECT_ROOT}/.webnovel/state.json" || { echo "❌ PROJECT_ROOT 解析失败"; exit 1; }

python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
```

若本次规划会直接落到具体章节，还必须先刷新 Story System runtime 合同：

```bash
# genre 从 state.json 的初始化配置快照读取；写前主链真源是 .story-system 合同树。
# 必须先从详细大纲解析真实 CHAPTER_GOAL，禁止传 {章纲目标} / 第N章章纲目标 这类占位文本。
GENRE="$(python -X utf8 -c "import json,sys; s=json.load(open('${PROJECT_ROOT}/.webnovel/state.json',encoding='utf-8')); print(s.get('project_info',{}).get('genre',''))")"

python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  story-system "${CHAPTER_GOAL}" --genre "${GENRE}" --chapter {chapter_num} --persist --emit-runtime-contracts --format both
```

生成后必须把 `.story-system/MASTER_SETTING.json`、`.story-system/volumes/`、
`.story-system/chapters/`、`.story-system/reviews/` 视为后续写作主链输入。
规划开始/结束都运行 `placeholder-scan`；plan 阶段发现占位先警告并补齐相关文件，进入写章前不得保留当前章相关实体的 `[待...]` / `暂名` / `{占位}`。
每卷规划完成后，只向 `大纲/总纲.md` 渐进追加下一卷概要与本卷新增/承接伏笔，不在 init 阶段预填 V2-V20 空表。
规划完成后写回必须来自显式结构化文件 `大纲/第{volume_id}卷-总纲写回.json`，禁止从卷纲自由文本推断伏笔或开放环。

## 引用加载策略

### md 必读

| Step | Trigger | Reference |
|------|---------|-----------|
| Step 4 | always | `templates/output/大纲-卷节拍表.md` |
| Step 5 | always | `templates/output/大纲-卷时间线.md` |
| Step 6 | always | `../../references/genre-profiles.md` |
| Step 6 | always | `../../references/shared/strand-weave-pattern.md` |
| 章纲拆分 | always | `../../references/outlining/plot-signal-vs-spoiler.md` |

### md 按需

| Step | Trigger | Reference |
|------|---------|-----------|
| Step 6 | 需要爽点设计 | `../../references/shared/cool-points-guide.md` |
| Step 6/7 | 需要冲突设计 | `references/outlining/conflict-design.md` |
| Step 7 | 需要追读力分析 | `../../references/reading-power-taxonomy.md` |
| Step 7 | 需要章纲细化 | `references/outlining/chapter-planning.md` |
| Step 6/7 | 特定题材节奏 | `references/outlining/genre-volume-pacing.md` |

### CSV 检索

| Step | Trigger | 检索命令 |
|------|---------|---------|
| 卷级规划 | always | `python -X utf8 "${SCRIPTS_DIR}/reference_search.py" --skill plan --table 场景写法 --query "卷级结构 叙事功能"` |
| 章纲拆分 | 新增角色出现 | `... --skill plan --table 命名规则 --query "角色命名" --genre {题材}` |

## 执行流程

### Step 1：加载项目数据并确认前置条件

**必须加载**：

```bash
# 项目配置/投影状态（兼容读取，不作为写后事实真源）
cat "$PROJECT_ROOT/.webnovel/state.json"

# 总纲（全局蓝图）
cat "$PROJECT_ROOT/大纲/总纲.md"

# 题材（来自 init 配置快照，后续 CSV 检索和裁决匹配依赖此值）
GENRE="$(python -X utf8 -c "import json; s=json.load(open('${PROJECT_ROOT}/.webnovel/state.json',encoding='utf-8')); print(s.get('project_info',{}).get('genre',''))")"
```

**已有卷的剧情状态**（跨卷规划时必须加载）：

若已有已完成卷（`.webnovel/summaries/` 下有文件），加载以下数据感知已写内容：

```bash
# 最近 5 章摘要（了解剧情走向）
for ch in $(seq $((START_CH - 5)) $((START_CH - 1))); do
  cat "$PROJECT_ROOT/.webnovel/summaries/ch$(printf '%04d' $ch).md" 2>/dev/null
done

# 核心角色当前状态
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  knowledge query-entity-state --entity "{protagonist_id}" --at-chapter {上一卷最后章}

# 核心关系当前状态
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  knowledge query-relationships --entity "{protagonist_id}" --at-chapter {上一卷最后章}

# 活跃伏笔（跨卷未回收的伏笔）
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" \
  memory-contract get-open-loops
```

**CSV 创作参考**（卷级规划时按需检索）：

```bash
python -X utf8 "${SCRIPTS_DIR}/reference_search.py" --skill plan --table 爽点与节奏 --query "{卷级核心冲突}" --genre "${GENRE}"
python -X utf8 "${SCRIPTS_DIR}/reference_search.py" --skill plan --table 桥段套路 --query "{卷级核心冲突}" --genre "${GENRE}"
```

**按需读取**（设定集）：
- `设定集/世界观.md`
- `设定集/力量体系.md`
- `设定集/主角卡.md`
- `设定集/反派设计.md`
- `.webnovel/idea_bank.json`

阻断条件：
- 总纲缺少卷名、章节范围、核心冲突或卷末高潮

### Step 2：补齐设定基线

目标：让设定集从骨架模板进入"可规划、可写作"的状态。

必须补齐：
- `设定集/世界观.md`：世界边界、社会结构、关键地点用途
- `设定集/力量体系.md`：境界链、限制、代价与冷却
- `设定集/主角卡.md`：欲望、缺陷、初始资源与限制
- `设定集/反派设计.md`：小/中/大反派层级与镜像关系

硬规则：
- 只增量补齐，不清空、不重写整文件
- 发现冲突时先列出冲突并阻断

### Step 3：选择目标卷并确认范围

必须确认：
- 卷名
- 章节范围
- 核心冲突
- 是否存在特殊要求，例如视角、情感线、题材偏移

### Step 4：生成卷节拍表

执行前加载模板：

```bash
cat "${SKILL_ROOT}/../../templates/output/大纲-卷节拍表.md"
```

硬要求：
- 必须填写中段反转；若确实没有，写"无（理由：...）"
- 危机链至少 3 次递增
- 卷末新钩子必须能落到最后一章的章末未闭合问题

输出文件：`大纲/第{volume_id}卷-节拍表.md`

### Step 5：生成卷时间线表

执行前加载模板：

```bash
cat "${SKILL_ROOT}/../../templates/output/大纲-卷时间线.md"
```

硬要求：
- 必须明确时间体系
- 必须明确本卷时间跨度
- 有倒计时事件时必须列出并标记 D-N

输出文件：`大纲/第{volume_id}卷-时间线.md`

### Step 6：生成卷纲骨架

必须加载：

```bash
cat "${SKILL_ROOT}/../../references/genre-profiles.md"
cat "${SKILL_ROOT}/../../references/shared/strand-weave-pattern.md"
```

按需加载：

```bash
cat "${SKILL_ROOT}/../../references/shared/cool-points-guide.md"
cat "${SKILL_ROOT}/references/outlining/conflict-design.md"
cat "${SKILL_ROOT}/references/outlining/genre-volume-pacing.md"
cat "$PROJECT_ROOT/.webnovel/idea_bank.json"
```

卷纲必须明确：
- 卷摘要
- 关键人物与反派层级
- Strand 分布
- 爽点密度规划
- 伏笔规划
- 约束触发规划

跨卷一致性检查（非首卷时必须执行）：
- 上一卷未回收的伏笔必须出现在新卷的伏笔规划中（继续推进或标记回收）
- 角色关系变化必须延续（不能当上一卷没发生过）
- 主角能力/境界必须承接（不能回退也不能跳级，除非有剧情解释）

### Step 7：批量生成章纲

批次规则：
- 默认按 `10章/批`
- 复杂题材或多线并进时可降到 `8章/批`
- 简单升级流可放宽到 `12章/批`
- 不建议单批超过 `12章`

按需加载：

```bash
cat "${SKILL_ROOT}/../../references/reading-power-taxonomy.md"
cat "${SKILL_ROOT}/references/outlining/chapter-planning.md"
```

每章必须包含：
- 目标
- 阻力
- 代价
- 时间锚点
- 章内时间跨度
- 与上章时间差
- 倒计时状态
- 爽点
- Strand
- 反派层级
- 视角/主角
- 关键实体
- 本章变化
- 章末未闭合问题
- 钩子
- `章节起点（CBN）`
- `推进节点（CPNs）`
- `章节终点（CEN）`
- `必须覆盖节点`
- `本章禁区`

#### 结构化节点规范

节点格式：`主体 | 动作/变化 | 对象/结果`。详细规范见 `references/outlining/structured-nodes.md`。

核心规则：每章 1 CBN + 2-4 CPN + 1 CEN，相邻章节 CEN→CBN 逻辑承接，必须覆盖节点最多 4 个，禁区不超过 5 条。

输出文件：`大纲/第{volume_id}卷-详细大纲.md`

### Step 8：把新增设定写回现有设定集

输入来源：
- 卷节拍表
- 卷时间线表
- 卷详细大纲
- 现有设定集文件

写回规则：
- 只增量补充相关段落
- 新角色写入角色卡或角色组
- 新势力、地点、规则写入世界观或力量体系
- 新反派层级写入反派设计

硬规则：
- 若发现与总纲或既有设定冲突，标记 `BLOCKER` 并停止后续更新

### Step 9：验证、保存并更新状态

必须通过以下检查：
- 节拍表存在且非空
- 时间线表存在且非空
- 详细大纲存在且非空
- 每章时间字段齐全
- 时间线单调递增
- 倒计时推进正确
- 新设定已回写到现有设定集
- `BLOCKER=0`
- 有节点时，相邻章节 `CEN -> CBN` 无明显逻辑冲突
- 有节点时，每章必须覆盖节点不超过 `4` 个

验证全部通过后，生成显式结构化写回文件：

```json
{
  "next_volume_anchor": {
    "volume": 2,
    "chapters_range": "51-100",
    "volume_name": "下一卷卷名",
    "core_conflict": "下一卷核心冲突",
    "volume_end_climax": "下一卷卷末高潮"
  },
  "foreshadow_writeback": [
    {"content": "本卷规划明确新增的伏笔", "buried_chapter": "第10章", "payoff_chapter": "", "level": "卷级"}
  ],
  "open_loop_writeback": [
    {"content": "本卷结束后仍持续开放的问题", "buried_chapter": "", "payoff_chapter": "", "level": "持续开放环"}
  ]
}
```

只允许写入规划过程中显式列出的结构化伏笔/开放环；不要把自由文本里的暗示整理进去。随后执行最小总纲写回：

```bash
python "${SCRIPTS_DIR}/webnovel.py" --project-root "$PROJECT_ROOT" master-outline-sync \
  --volume {volume_id} \
  --writeback-file "大纲/第{volume_id}卷-总纲写回.json" \
  --format text
```

该步骤只允许更新 `大纲/总纲.md` 的 V+1 卷名 / 核心冲突 / 卷末高潮与伏笔表，不得生成下一卷详细大纲、节拍表、时间线或章纲。

更新状态：

```bash
python "${SCRIPTS_DIR}/webnovel.py" --project-root "$PROJECT_ROOT" update-state -- \
  --volume-planned {volume_id} \
  --chapters-range "{start}-{end}"
```

## 硬失败条件

- 节拍表不存在或为空
- 中段反转缺失且未给出理由
- 时间线表不存在或为空
- 详细大纲不存在或为空
- 任一章节缺少时间字段
- 时间回跳且未标注闪回
- 倒计时算术冲突
- 与总纲核心冲突或卷末高潮明显冲突
- 存在 `BLOCKER` 未裁决

## 恢复规则

1. 只重做失败批次，不覆盖整卷文件。
2. 最后一个批次无效时，只删除并重写该批次。
3. 仅在全部验证通过后更新状态。
