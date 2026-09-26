---
name: webnovel-write-batch
description: |
  连续写作多章。当用户要求多章写作时必须使用此 skill。

  ## 触发条件
  - "连续写N章"、"写第X-Y章"、"批量写X-Y章"、"一次写N章"
  - "重写第X-Y章"、"修改第X-Y章"
  - "多章"、"多章节"

  ## 区分规则
  - 单章 → webnovel-write
  - 多章 → 必须使用本 skill
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

# 批量写作

## ⛔ 硬规则（Anti-Laziness）

以下规则针对 AI 在长循环中的已知偷懒模式。**逐章执行前必须重现此清单。**

| # | 禁止 | 正确做法 |
|---|------|---------|
| 1 | 口头描述代替 `Agent()` 调用 | 必须使用 Agent 工具调用 context-agent / chapter-writer-agent / reviewer / data-agent |
| 2 | 跳过审查 | 每章必须运行 reviewer Agent + review-pipeline。blocking=false 也不能跳过 |
| 3 | 用 Read 工具代替验证命令 | 每步后必须运行 bash 验证命令（test -s / ls / python -c） |
| 4 | "稍后更新" batch_state | 每章完成后立即用 python -c 写 JSON，写完后重新读取验证 |
| 5 | 章数多了开始跳步/简化流程 | 每章必须完整执行本 skill 的 9 步，不得缩减。3 章后强制暂停 |
| 6 | 子代理失败后假装成功 | 每个 Agent() 调用后检查输出文件是否存在且非空。失败重试 1 次→仍失败则停止 |
| 7 | 审查和润色合并执行 | chapter-writer-agent 先写正文 → reviewer 审查 → 有 blocking 则回传修复。禁止在审查前自行修改 |
| 8 | 用简化版代替完整单章流程 | 每章 = 完整闭环。chapter-writer-agent 在干净上下文中执行创作，质量等同于单章 |

## 环境设置

```bash
export SCRIPTS_DIR="${PWD}/.opencode/scripts"
export SKILL_ROOT="${PWD}/.opencode/skills/webnovel-write"
test -d "${SCRIPTS_DIR}" || { echo "错误: 未找到 ${SCRIPTS_DIR}，请确保当前目录是 webnovel-writer 仓库根目录"; exit 1; }

export PYTHONUTF8=1

export PROJECT_ROOT="$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PWD}" where)"
# 归一化为正斜杠，避免路径中的 \b \n 等在 python -c 中被转义
export PROJECT_ROOT="${PROJECT_ROOT//\\//}"
test -n "$PROJECT_ROOT" && test -f "${PROJECT_ROOT}/.webnovel/state.json" || { echo "错误: PROJECT_ROOT 解析失败，请用 --project-root 显式指定"; exit 1; }
```

## Step 0: 解析章节范围

- "写第9-15章" → S=9, E=15
- "连续写3章" → **扫描文件系统获取最新章节号**（同单章 Step 0），S=最新章+1, E=S+2
- "批量写5章" → 同上，E=S+4

**禁止依赖 state.json 的 current_chapter 或对话记忆来确定起始章号。** 章节文件是唯一真源。

```bash
LATEST=$(python -c "
import re
from pathlib import Path
text_dir = Path('${PROJECT_ROOT}') / '正文'
nums = []
for f in text_dir.rglob('第*章*.md'):
    m = re.match(r'第0*(\d+)章', f.name)
    if m:
        nums.append(int(m.group(1)))
print(max(nums) if nums else 0)
")
echo "最新章节: 第${LATEST}章"
```

若无法解析，询问用户明确范围。上限 3 章（可通过 `--force` 参数绕过，上限 5 章）。

## Step 0.5: 断点恢复

```bash
BATCH_STATE="${PROJECT_ROOT}/.webnovel/batch_state.json"

if [ -f "$BATCH_STATE" ]; then
  STATUS=$(python -c "import json; print(json.load(open('$BATCH_STATE',encoding='utf-8-sig')).get('status',''))")
  if [ "$STATUS" = "running" ]; then
    echo "检测到未完成的批量任务"
    python -c "
import json
s = json.load(open('$BATCH_STATE', encoding='utf-8-sig'))
print(f'  已完成: 第{s[\"completed_chapters\"]}章')
print(f'  待恢复: 第{s[\"current_chapter\"]}章')
"
    # 询问用户：继续 / 重新开始 / 放弃
  elif [ "$STATUS" = "completed" ]; then
    echo "上一次批量任务已完成。"
    # 询问是否开始新批次
  fi
fi
```

## Step 0.6: 初始化 batch_state（新任务）

```bash
if [ ! -f "$BATCH_STATE" ] || [ "$STATUS" != "running" ]; then
  python -c "
import json, datetime
s = {
  'task_id': f'batch_{datetime.datetime.now(datetime.timezone.utc).strftime(\"%Y%m%d_%H%M%S\")}',
  'range': {'start': $S, 'end': $E},
  'status': 'paused',
  'current_chapter': $S,
  'completed_chapters': [],
  'failed_chapters': [],
  'chapter_results': {},
  'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat()
}
open('$BATCH_STATE', 'w', encoding='utf-8').write(json.dumps(s, ensure_ascii=False, indent=2))
"
fi
```

---

## 批前一次性验证

在进入逐章循环前执行一次 preflight，后续各章不再重复。

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" preflight
```

---

## 逐章循环

> **每章 = 9 步完整闭环。创作在独立 subagent 干净上下文中执行。**

### 逐章检查清单（每章开始前重现）

**步骤映射：** 0=环境变量验证, A=上章完整性, 1=刷新合同, 1b=结构自检, 2=context-agent, 3=写作, 4=审查, 5=review-pipeline, 6=修复轮, 7=data-agent, 8=commit+验证, 9=更新状态

```
□ 0. 环境变量验证
□ A. 上章完整性检查（N > S 时）
□ 1. 刷新合同树
□ 1b. 结构自检
□ 2. Agent(context-agent) → 写作任务书
□ 3. Agent(chapter-writer-agent) → 起草+润色
□ 4. Agent(reviewer) → 审查结果
□ 5. review-pipeline → 判定 blocking
□ 6. blocking? → 提取反馈 → 回到 3（最多 2 轮）
□ 7. Agent(data-agent) → 事实提取
□ 8. chapter-commit + 验证投影 + Git 备份
□ 9. 更新 batch_state + 进度反馈
```

每步执行前打印进度标识：

```bash
echo "[Ch{N} Step {M}/9] {step_name}..."
```

---

### Step 0: 环境变量验证（每章开始前强制）

```bash
test -n "$PROJECT_ROOT" || { echo "❌ PROJECT_ROOT 未设置"; exit 1; }
test -n "$SCRIPTS_DIR" || { echo "❌ SCRIPTS_DIR 未设置"; exit 1; }
test -d "$PROJECT_ROOT" || { echo "❌ PROJECT_ROOT 目录不存在: $PROJECT_ROOT"; exit 1; }
test -d "${PROJECT_ROOT}/.webnovel" || { echo "❌ ${PROJECT_ROOT}/.webnovel 不存在，PROJECT_ROOT 可能不正确"; exit 1; }
echo "✅ PROJECT_ROOT=${PROJECT_ROOT}"
```

**状态门**: 若 batch_state.status 为 "paused"，输出进度摘要并等待用户输入 "继续" 才将 status 改为 "running"。

---

### Step A: 上章完整性检查（N > S 时强制）

```bash
if [ "$N" -gt "$S" ]; then
  PREV=$((N - 1))

  CHAPTER_FILE=$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-path --chapter $PREV)
  if [ ! -s "${PROJECT_ROOT}/${CHAPTER_FILE}" ]; then
    echo "❌ 第${PREV}章文件缺失。立即停止。"
    exit 1
  fi

  IN_BATCH=$(python -c "import json; s=json.load(open('$BATCH_STATE',encoding='utf-8-sig')); print($PREV in s.get('completed_chapters',[]))")
  if [ "$IN_BATCH" != "True" ]; then
    echo "❌ 第${PREV}章未在 batch_state 中标记完成。立即停止。"
    exit 1
  fi

  echo "✅ 第${PREV}章完整性检查通过"
fi
```

---

### Step 1: 刷新合同树

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" placeholder-scan --format text
```

genre 从 `.webnovel/state.json` 的初始化配置快照读取。调用 story-system 前必须先从详细大纲解析真实本章目标，禁止传 `{章纲目标}`、`第N章章纲目标` 等占位 query。

```bash
# 用 skill_runner 传递 CJK，genre 自动从 state.json 读取，goal 从 stdin 传入
echo "${CHAPTER_GOAL}" | python -X utf8 "${SCRIPTS_DIR}/skill_runner.py" story-system \
  --project-root "${PROJECT_ROOT}" --chapter {N}
if [ $? -ne 0 ]; then
  echo "❌ story-system 合同刷新失败，阻断流程"
  exit 1
fi
```

必备文件：`MASTER_SETTING.json`、`volume_{NNN}.json`、`chapter_{NNN}.review.json`。缺失则阻断。

`chapter_{NNN}.json` 必须优先检查顶层 `chapter_directive`。`chapter_focus` 只能来自 `chapter_directive.goal` 或真实 query，不得从 `dynamic_context` 的参考摘要继承。

---

### 准备：结构自检

```bash
# 从章纲提取 intended_strand（统一小写，避免大小写不匹配）
INTENDED_STRAND=$(python -c "
import json
contract_file = '${PROJECT_ROOT}/.story-system/chapters/chapter_$(printf '%03d' {N}).json'
try:
    d = json.load(open(contract_file))
    s = d.get('chapter_directive', {}).get('strand', '')
    print(s.strip().lower())
except: pass
")

python -X utf8 "${SCRIPTS_DIR}/skill_runner.py" check-structural \
  --project-root "${PROJECT_ROOT}" --chapter {N} --intended-strand "${INTENDED_STRAND}" --format json \
  --output "${PROJECT_ROOT}/.webnovel/tmp/structural_check.json"
```

```bash
python -c "
import json, sys
d = json.load(open('${PROJECT_ROOT}/.webnovel/tmp/structural_check.json'))
if not d.get('passed'):
    print('❌ 结构自检未通过，停止流程')
    for c in d['checks']:
        if c['severity'] == 'blocking' and not c['passed']:
            print(f'  BLOCKING: {c[\"name\"]}: {c[\"detail\"]}')
            print(f'  FIX: {c[\"fix\"]}')
    sys.exit(1)
" || exit 1
# (use $? check for PowerShell compatibility)
```

---

### Step 2: Agent(context-agent) → 写作任务书

**必须使用 Agent 工具调用 context-agent，不得由主流程自行整理。**

```text
Agent(
  subagent_type: "context-agent",
  prompt: "chapter={N}; project_root=${PROJECT_ROOT}; scripts_dir=${SCRIPTS_DIR}; storage_path=${PROJECT_ROOT}/.webnovel; state_file=${PROJECT_ROOT}/.webnovel/state.json（projection/read-model，仅兼容读取）。先 research，再按 本章硬性约束→CBN/CPNs/CEN→本章禁区→风格指引→dynamic_context补充参考 的顺序输出五段写作任务书。"
)
```

验证：任务书非空且包含"硬性约束"或"CBN"字样。失败或返回空→重试 1 次→仍失败则停止。

---

### Step 3: Agent(chapter-writer-agent) → 起草+润色

**核心变更：原来主线程的起草(Step 3)和润色(Step 5)合并为一次 Agent 调用，在干净上下文中完成完整创作闭环。**

传入 prompt 结构：

```text
Agent(
  subagent_type: "chapter-writer-agent",
  prompt: """
【章节】第{N}章
【字数】2000-2500 字

【写作任务书】
{context-agent 产出的完整任务书}

【章纲约束】
{从 chapter_{NNN}.json 提取的 chapter_directive.goal / time_anchor / countdown / chapter_end_open_question / must_cover_nodes / forbidden_zones}

【润色指南】
- 风格适配：与 MASTER_SETTING 保持一致的人称/视角/叙事距离
- 排版：标题格式 `## 第{NNNN}章 标题`，段落间空行，对话分行
- Anti-AI 终检：消除"不是...而是..."句式、段落首尾总结句、冗余"突然/忽然"、机械动作罗列、直接情感告知

【审查反馈】
（首轮为空。修复轮时填入：）
- [{category}] {description} (位置: {location})
"""
)
```

**验证：**

```bash
CHAPTER_PATH=$(python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-path --chapter {N})
CHAPTER_FILE="${PROJECT_ROOT}/${CHAPTER_PATH}"

test -s "$CHAPTER_FILE" || { echo "❌ 章节文件为空"; exit 1; }

WORDS=$(python -c "
import re
t = open('$CHAPTER_FILE', encoding='utf-8').read()
print(len(re.findall(r'[一-鿿]', t)))
")
echo "字数: $WORDS"
if [ "$WORDS" -lt 1500 ]; then
  echo "⚠️ 字数不足 1500"
fi
```

字数不足→重试 chapter-writer-agent（提示字数不足）。仍不足→标记 warning 继续。

---

### Step 4: Agent(reviewer) → 审查

**必须使用 Agent 工具调用 reviewer，不得由主流程伪造审查 JSON。**

```text
Agent(
  subagent_type: "reviewer",
  prompt: "chapter={N}; chapter_file=${CHAPTER_FILE}; project_root=${PROJECT_ROOT}; scripts_dir=${SCRIPTS_DIR}; REVIEW_OUTPUT=${PROJECT_ROOT}/.webnovel/tmp/review_results.json。

【自检系统状态 - 审查时需额外关注】
$(echo "$CHECK_RESULT" | python -c "
import json,sys
d=json.load(sys.stdin)
warnings=[c for c in d['checks'] if c['severity']=='warning' and not c['passed']]
if warnings:
    for w in warnings:
        print(f'- {w[\"name\"]}: {w[\"detail\"]}')
else:
    print('（无异常）')
")

严格输出 reviewer schema JSON，并保存到 ${PROJECT_ROOT}/.webnovel/tmp/review_results.json。"
)
```

---

### Step 5: review-pipeline

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" review-pipeline \
  --chapter {N} \
  --review-results "${PROJECT_ROOT}/.webnovel/tmp/review_results.json" \
  --metrics-out "${PROJECT_ROOT}/.webnovel/tmp/review_metrics.json" \
  --report-file "审查报告/第{N}章审查报告.md" \
  --save-metrics
```

验证审查结果：

```bash
python -c "
import json
d = json.load(open('${PROJECT_ROOT}/.webnovel/tmp/review_results.json'))
assert isinstance(d, dict), 'review_results 不是 JSON 对象'
blocking_count = len([i for i in d.get('issues',[]) if i.get('severity')=='blocking'])
print(f'blocking: {blocking_count}, score: {d.get(\"score\",\"unknown\")}')
"
```

---

### Step 6: 修复轮

```
blocking_count > 0 → 进入修复轮（最多 2 轮）

修复轮流程：
  round = 0
  while blocking_count > 0 and round < 2:
    1. 从 review_results.json 提取 blocking issues 文本：
       python -c "import json; d=json.load(open('${PROJECT_ROOT}/.webnovel/tmp/review_results.json')); [print(f\"- [{i.get('category','')}] {i.get('description','')} (位置: {i.get('location','')})\") for i in d.get('issues',[]) if i.get('severity')=='blocking']"

    2. Agent(chapter-writer-agent) 修复模式
       将提取的 issue 列表填入 prompt 的【审查反馈】部分
       prompt 明确指出："修复模式：只修改上述审查反馈指出的具体问题，不大面积重写。保留未涉及部分的原文。"

    python -X utf8 "${SCRIPTS_DIR}/skill_runner.py" clean-tmp --project-root "${PROJECT_ROOT}"

    3. Agent(reviewer) 重新审查
       → 覆盖更新 review_results.json

    4. review-pipeline 重新判定 blocking

    5. round += 1

  if blocking_count > 0:
    标记本章 failed，记录 blocking issues 到 batch_state，继续下一章
  else:
    阻塞已清除，继续 Step 7
```

---

### Step 7: Agent(data-agent) → 事实提取

**必须使用 Agent 工具调用 data-agent，不得跳过或手动模拟。**

```bash
# 清空旧 tmp 文件，保留 review_results.json（chapter-commit 仍需）
python -X utf8 "${SCRIPTS_DIR}/skill_runner.py" clean-tmp --project-root "${PROJECT_ROOT}" --keep review_results.json
```

```text
Agent(
  subagent_type: "data-agent",
  prompt: "chapter={N}; chapter_file=${CHAPTER_FILE}; project_root=${PROJECT_ROOT}; scripts_dir=${SCRIPTS_DIR}。从正文提取事实，生成 .webnovel/tmp/ 下的 fulfillment_result.json、disambiguation_result.json、extraction_result.json；不直接写 state/index/summaries/memory。"
)
```

验证输出文件：

```bash
for f in fulfillment_result.json disambiguation_result.json extraction_result.json; do
  FP="${PROJECT_ROOT}/.webnovel/tmp/${f}"
  if [ ! -s "$FP" ]; then
    echo "❌ 缺失: $f"
  else
    echo "✅ $f ($(wc -c < $FP) bytes)"
  fi
done
```

任一缺失→重试 Agent 调用 1 次。仍缺失→标记 failed 并停止。

---

### Step 8: chapter-commit + 验证投影 + Git 备份

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" chapter-commit \
  --chapter {N} \
  --review-result "${PROJECT_ROOT}/.webnovel/tmp/review_results.json" \
  --fulfillment-result "${PROJECT_ROOT}/.webnovel/tmp/fulfillment_result.json" \
  --disambiguation-result "${PROJECT_ROOT}/.webnovel/tmp/disambiguation_result.json" \
  --extraction-result "${PROJECT_ROOT}/.webnovel/tmp/extraction_result.json"
```

自动判定：blocking_count>0 或 missed_nodes 非空 或 pending 非空 → rejected，否则 accepted。

验证投影：projection_status 五项（state/index/summary/memory/vector）全部 done 或 skipped。

失败隔离：commit 未生成→重跑 2 次。projection 失败→只补跑失败项。不回退 Step 1-7。

#### 写后校验

```bash
python -X utf8 "${SCRIPTS_DIR}/skill_runner.py" verify-chapter-files \
  --project-root "${PROJECT_ROOT}" --chapter {N} \
  || { echo "❌ 写后校验失败"; exit 1; }
```

```bash
python -X utf8 "${SCRIPTS_DIR}/webnovel.py" --project-root "${PROJECT_ROOT}" backup \
  --chapter {N} \
  --chapter-title "{title}"
```

备份必须以解析后的 `PROJECT_ROOT` 为准，禁止从工作区父目录执行裸全量 Git add。

---

### Step 9: 更新 batch_state + 进度反馈

```bash
python -c "
import json, pathlib, datetime

# 读取审查得分
review_path = '${PROJECT_ROOT}/.webnovel/tmp/review_results.json'
score = json.load(open(review_path)).get('score', 0)

# 更新 batch_state
p = pathlib.Path('$BATCH_STATE')
s = json.loads(p.read_text())
if $N not in s['completed_chapters']:
    s['completed_chapters'].append($N)
s['current_chapter'] = $N + 1
s['chapter_results'][str($N)] = {
    'status': 'success',
    'score': score,
    'words': $WORDS,
    'completed_at': datetime.datetime.now(datetime.timezone.utc).isoformat()
}
p.write_text(json.dumps(s, ensure_ascii=False, indent=2))

# 重新读取验证（磁盘级校验）
s2 = json.loads(p.read_text())
assert $N in s2['completed_chapters'], '写入验证失败'

# 跨章完整性校验：范围内所有已完成的章都必须在 completed_chapters 中
contiguous = list(range($S, $N + 1))
actual = sorted(s2['completed_chapters'])
gaps = [ch for ch in contiguous if ch not in actual]
if gaps:
    raise AssertionError(f'batch_state 不完整！缺失章节: {gaps}')
total_in_batch = $E - $S + 1
done = len(actual)
print(f'✅ batch_state 已验证 ({done}/{total_in_batch} 章已记录)')
"
```

更新失败→重试 3 次。仍失败→停止。

> 若跨章校验发现缺失章节，说明前面某章的 Step 9 静默失败。必须补写缺失章到 completed_chapters 后再继续，不得忽略。

```
✅ 第{N}章完成 | 审查: {SCORE}/100 | 字数: {WORDS} | 进度: {N-S+1}/{E-S+1}
```

---

## 分批暂停点（每 3 章）

```
当 (N - S + 1) % 3 == 0 且 N != E:
  ═══════════════════════════════════════
  🚦 已完成第 {S}-{N} 章（共 {N-S+1} 章）

  章节摘要：
  第{S}章 ✅ score:{xx} words:{xxxx}
  ...

  batch_state 完整性: ✅

  确认下一步：
  - "继续" 撰写第 {N+1}-{min(N+3, E)} 章
  - "停止" 保存进度并退出
  ═══════════════════════════════════════

若用户设置 AUTO_CONTINUE=1，跳过暂停直接继续。
```

---

## 循环完成：汇总报告

```
═══════════════════════════════════════
📊 批量写作完成

| 章节 | 状态 | 得分 | 字数 |
|------|------|------|------|
| ... | ... | ... | ... |

总计: {count} 章 | 成功: {success} | 失败: {failed}
平均得分: {avg}
═══════════════════════════════════════
```

更新 batch_state status = "completed"。

---

## 失败处理速查

| 场景 | 处理 | 阻断 |
|------|------|------|
| 环境变量未设置/PROJECT_ROOT 错误 | 立即停止 | 阻断 |
| 上章完整性失败 | 立即停止 | 阻断 |
| preflight 失败（批前） | 修复环境→重试1次→停止 | 阻断 |
| 合同树刷新失败 | 检查缺失文件→修复→重试1次→停止 | 阻断 |
| context-agent 失败 | 重试1次→停止 | 阻断 |
| chapter-writer-agent 失败 | 重试1次→停止 | 阻断 |
| 字数不足 | 重试 chapter-writer-agent | 章内 |
| reviewer blocking(1-2轮) | 提取反馈→回传 chapter-writer-agent→重审 | 章内 |
| reviewer blocking(3轮) | 标记failed→继续 | 否 |
| data-agent 失败 | 重试1次→标记failed并停止 | 阻断 |
| chapter-commit 失败 | 修复→重试(3次)→停止 | 阻断 |
| projection 失败 | 只补跑失败项 | 章内 |
| batch_state 更新失败 | 重试3次→停止 | 阻断 |
