# 审查-润色职责分离重构方案

> ✅ **已实施**。本文保留为该次重构的决策记录。
>
> ⚠️ 下文"当前 reviewer 检查 13 维度……最多 3 轮"描述的是**重构前**的状态。
> 实施后已变为：reviewer 只查 **6 个维度**（设定/时间线/连贯/角色/逻辑/项目规则），
> AI 味与节奏移交 polish 阶段；写-修循环上限 **2 轮**。
> 现行形态见 [`../../CLAUDE.md`](../../CLAUDE.md) 的 "Review Pipeline" 一节。

## 1. 背景

当前 reviewer 检查 13 维度（事实×5 + AI味×5 + 节奏 + 毒点 + 项目规则），最多 3 轮审查。存在：
- Token 消耗高（3 轮 × 13 维 ≈ 39 次检查）
- reviewer 和 polish 都查 AI 味，职责重叠
- 多轮审查边际收益递减（Self-Refine 研究：第 3 轮后收益接近零）

原项目改为 reviewer 只查事实（5 维）+ 1 轮，AI 味全交给润色。结合两者优势，制定混合方案。

## 2. 设计原则

1. **职责分离**（Constitutional AI）：reviewer 查可验证事实，polish 查主观风格
2. **结构化反馈**（Self-Refine）：dimension_results 强制逐维度结论
3. **自查跳过**（Token 优化）：evidence 子串匹配避免无意义重审
4. **门禁机制**：anti_ai_force_check 确保 AI 味问题不被漏检

## 3. 改动清单

### 3.1 reviewer.md：13 维 → 6 维 + 结构化结论

**保留（事实审查 + 数值统计）**：
| 维度 | category | 检查内容 |
|------|----------|----------|
| 设定一致性 | setting | 角色能力/地点/物品 vs state.json（必须 bash 查询） |
| 时间线 | timeline | 时间衔接/倒计时/角色位置冲突 |
| 叙事连贯 | continuity | 上章钩子/场景过渡/情绪弧 |
| 角色一致性 | character | 对话风格/行为动机/知识边界 |
| 逻辑 | logic | 因果关系/决策动机/战力对比 |
| 项目规则 | other | 破折号≤20、但≤6、不是X是Y≤1、句号≤70/千字（必须 python 统计，polish 不做数值检查） |

**移除（交给 polish）**：
| 维度 | 移到 | 理由 |
|------|------|------|
| AI味-词汇 | polish-guide 第 1 层 | polish 有完整 200+ 词库，reviewer 只引用了 K/L/M/N 四类 |
| AI味-句式 | polish-guide 第 2 层 | polish 有 10 条规则，reviewer 只检查 5 条 |
| AI味-叙事 | polish-guide 第 6 层 + 通过阈值 | 与 polish 的段落结构/章末锚点重叠 |
| AI味-情感 | polish-guide 第 1 层 E 类 + 改写算法 | polish 有具体改写动作，reviewer 只标记问题 |
| AI味-对话 | polish-guide 第 5 层 | polish 有 7 条规则，reviewer 只检查 3 条 |
| 节奏 | polish-guide Soft 规则 | 与章首钩子/中段脉冲/章末锚点完全重叠 |
| 毒点 | polish-guide 第 5 节 No-Poison | 完全重叠，reviewer 检测 + polish 修复改为 polish 一站式 |

**新增 `dimension_results` 字段**：
```json
{
  "issues": [...],
  "dimension_results": [
    {"dimension": "setting", "conclusion": "pass"},
    {"dimension": "timeline", "conclusion": "发现1个问题：..."},
    {"dimension": "continuity", "conclusion": "pass"},
    {"dimension": "character", "conclusion": "pass"},
    {"dimension": "logic", "conclusion": "pass"},
    {"dimension": "rules", "conclusion": "pass"}
  ],
  "summary": "..."
}
```

### 3.2 polish-guide.md：新增强制执行检查清单

在现有 7 层 Anti-AI + No-Poison 基础上，新增**强制执行检查清单**（Anti-AI 终检之后、输出之前执行）：

```markdown
### 强制执行检查（Anti-AI 终检之后，输出之前）

以下检查必须执行，不通过则继续修改：

- [ ] 节奏：章首 200-400 字内有冲突/悬念
- [ ] 节奏：中段有至少一次节奏脉冲
- [ ] 节奏：章末有未闭合问题或下一步期待
- [ ] 毒点：无降智推进/强行误会/圣母无代价/工具人配角/双标裁决
- [ ] 项目规则：破折号≤20、但≤6、句号密度≤70/千字（python 统计）
```

**职责变化**：
- 节奏：从 reviewer "检测+报告" → polish "检测+修复"
- 毒点：从 reviewer "检测+报告" → polish "检测+修复"（No-Poison 第 5 节已有修复指令）
- 项目规则：reviewer 检测（Step 3）+ polish 兜底检查（Step 4）

### 3.3 SKILL.md：审查轮数 3→2，自查保留

**改动前**：
```
Step 3: reviewer（13 维）→ 最多 3 轮 + 自查
Step 4: polish（排版 + 风格适配）
```

**改动后**：
```
Step 3: reviewer（6 维：事实×5 + 项目规则）→ 最多 2 轮 + 自查
  → blocking 修完 → 自查 evidence → 通过则进 Step 4
  → 未通过 → 重审 1 次（第 2 轮）
  → 仍有 blocking → AskUserQuestion 让用户裁决
Step 4: polish（7 层 Anti-AI + 项目规则 + 节奏 + 毒点 + anti_ai_force_check）
```

**关键变化**：
- 第 2 轮后仍有 blocking → AskUserQuestion（不是停止循环）
- 用户可选择：接受当前版本 / 手动修复 / 放弃
- `allowed-tools` 补 `AskUserQuestion`

**AskUserQuestion 实现细节**：

```text
AskUserQuestion(
  question: "第 {chapter_num} 章经 2 轮审查仍有 {N} 个 blocking issue。请选择：",
  options: [
    { label: "接受当前版本", description: "忽略剩余 blocking，强制进 Step 4" },
    { label: "手动修复", description: "暂停流程，你手动修改后重新运行" },
    { label: "放弃本章", description: "跳过本章，不生成正文" }
  ]
)
```

- **接受当前版本**：修改 `review_results.json`，将剩余 blocking issue 的 `blocking` 设为 `false`、`severity` 降为 `medium`，然后进 Step 4。chapter-commit 不会因此 rejected。
- **手动修复**：停止流程，用户修改后重新运行。
- **放弃本章**：停止流程，不生成正文。

**frontmatter 改动**：`allowed-tools: Agent` → `allowed-tools: Agent AskUserQuestion`

### 3.4 review_schema.py：category 保持兼容

保留 `ai_flavor` 和 `pacing` category（向后兼容），但 reviewer 不再使用它们。polish 阶段如果需要记录 AI 味问题，仍可用这些 category。

### 3.5 项目规则检查：reviewer + polish 双保险

| 阶段 | 谁检查 | 怎么检查 | 不通过怎么办 |
|------|--------|----------|-------------|
| Step 3 reviewer | ✅ 检测 | python 统计（破折号/但/句号密度） | 输出 issue → writer-agent 修复 → 重审 |
| Step 4 polish | ✅ 兜底 | 强制执行检查清单（python 统计） | 继续修改直到通过 |

reviewer 是主检，polish 是兜底。两层都检查确保 Step 3 修复后 polish 改写不会引入新的规则违反。

## 4. 流程对比

### 改动前
```
Step 3: reviewer(13维) → blocking? → writer修复 → 自查 → 重审 → blocking? → ...
                                                        ↑ 最多 3 轮
Step 4: polish(排版+风格适配)
```

### 改动后
```
Step 3: reviewer(6维: 事实×5 + 项目规则) → blocking? → writer修复 → 自查 → 通过? → Step 4
                                                                    ↓ 未通过
                                                             重审(第2轮) → blocking? → AskUserQuestion
Step 4: polish(7层Anti-AI + 节奏 + 毒点 + anti_ai_force_check)
```

## 5. Token 效率预估

| 场景 | 改动前 | 改动后 | 节省 |
|------|--------|--------|------|
| 无问题 | 1 轮×13维 = 13维 | 1 轮×6维 = 6维 | 54% |
| 1 轮修复 | 2 轮×13维 = 26维 | 2 轮×6维 = 12维 | 54% |
| 2 轮修复 | 3 轮×13维 = 39维 | 2 轮×6维 + AskUser = 12维 | 69% |
| 典型情况 | ~26 维 | ~12 维 | ~54% |

## 6. 风险与缓解

| 风险 | 缓解措施 |
|------|----------|
| AI 味问题到 Step 4 才发现 | polish 有 7 层 Anti-AI + anti_ai_force_check 门禁 |
| polish 漏检 AI 味 | anti_ai_force_check=fail 时不进 Step 5 |
| 2 轮不够修完事实问题 | AskUserQuestion 让用户裁决，不卡死 |
| dimension_results 增加 reviewer 输出长度 | 5 维 vs 13 维，总输出量反而减少 |

## 7. 涉及文件

| 文件 | 改动类型 | 说明 |
|------|----------|------|
| `.opencode/agents/reviewer.md` | 重写 | 13 维 → 6 维（事实×5 + 项目规则）+ dimension_results |
| `.opencode/skills/webnovel-write/references/polish-guide.md` | 修改 | 强化节奏 Soft 规则，毒点改为一站式检测+修复 |
| `.opencode/skills/webnovel-write/SKILL.md` | 修改 | 3 轮 → 2 轮 + AskUserQuestion |
| `.opencode/scripts/data_modules/review_schema.py` | 不改 | 保持向后兼容 |
| Dashboard `/api/style/reviewer-checklist` | 同步 | 13 维 → 6 维 |
| `docs/specs/review-polish-refactor-spec.md` | 新增 | 本方案文档 |

## 8. 验证标准

- [ ] reviewer.md 只输出 6 个 dimension_results（setting/timeline/continuity/character/logic/rules）
- [ ] reviewer.md 不再输出 ai_flavor/pacing 类别的 issue
- [ ] polish-guide.md 新增强制执行检查清单（节奏/毒点/项目规则）
- [ ] SKILL.md 审查最多 2 轮
- [ ] SKILL.md 第 2 轮后仍有 blocking → AskUserQuestion（接受/手动/放弃）
- [ ] SKILL.md frontmatter 包含 `AskUserQuestion`
- [ ] "接受当前版本"修改 review_results.json 清除 blocking
- [ ] anti_ai_force_check 门禁仍然生效
- [ ] Dashboard `/api/style/reviewer-checklist` 更新为 6 维
- [ ] 所有测试通过
