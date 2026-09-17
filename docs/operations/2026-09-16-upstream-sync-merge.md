# 上游同步合并记录 — 2026-09-16

> 记录本次从上游 `lingfengQAQ/webnovel-writer` 同步的 5 笔 fix（v2.9.2 之后），以及全量审查与 P0 修复的决策链。
> 基线：本项目 `2cf7be7`（PR #23 合并后 7 项修复）。

## 一、本次落地 5 笔 commit

| Commit | 类型 | 内容 | 对应上游 |
|--------|------|------|----------|
| `82a0a5b` | fix | open_loop 事件聚合进 `plot_threads.foreshadowing`（#130） | `e2420a9`/PR #133 |
| `4bda741` | fix | `atomic_write_json` 对 WinError 5 退避重试（#125） | `f75e913`/PR #126 |
| `e5f124d` | fix | global 段注入 + 文风记忆排序截断（#131） | `768dcb2`/PR #132 |
| `2e2340f` | fix | 审查修复 3 项：rebuild 双轨 + content 对齐 + renderer 防护 | 本项目独有 |
| `a8f2041` | fix | P0：`ssot rebuild` 保留非事件顶层字段（防擦除） | 本项目独有 |

## 二、各项决策与适配

### #130 open_loop 伏笔聚合
- 路由表 `open_loop_created/closed → ["state", "memory"]`（与上游逐字一致）
- `StateProjectionWriter._apply_foreshadowing` 聚合进 `plot_threads.foreshadowing`（dashboard 消费路径）
- **本项目增强**：content 提取候选字段含 `unanswered_question`（live 项目事件确实携带该字段）；新增 `_coerce_loop_content` 与 memory 侧 `loop_type+description` 组合规则对齐，避免 content 匹配键分裂导致"活跃伏笔"残留

### #125 WinError 5 退避重试
- `_replace_with_retry`：PermissionError 指数退避（20ms→500ms ×10，约 2.6s 窗口）
- 全部 JSON 原子写（state.json/memory_scratchpad.json/summaries）统一受益
- 测试移植自上游 f75e913，含 Windows 真实句柄占用复现

### #131 global 注入 + 文风记忆
- **本项目额外修正**：移除 `_assemble_json_payload` 的 `!= "global"` 特例（bebc9e7 移植清理引入的 bug，global 段从未进入最终 payload，但 context_weights 仍分配 0.08–0.35 权重给它）
- `author_style_patterns` section：按 importance 排序取前 10 条，description 截断 200 字，从 memory 段剥离避免重复注入
- `style_contract_ref` 截断 2000 字

### P1 ssot rebuild 双轨分裂
- **本项目独有缺陷**（上游无 `ssot_enforcer`）：`ssot_enforcer.rebuild_state_json` 是独立 replay 路径，原实现只写顶层 `state.foreshadowing` 不写 `plot_threads.foreshadowing`——对存量项目跑 `ssot rebuild` 会把 `StateProjectionWriter` 增量路径填充的 `plot_threads` 整体冲掉，dashboard 伏笔面板回到空白
- 修复：rebuild 的 open_loop 分支同步调用 `_apply_foreshadowing_event` 聚合进 `plot_threads.foreshadowing`（与 writer 幂等规则同构），顶层旧路径保留向后兼容

### P2 content 匹配键分裂
- **本项目独有缺陷**：state writer 的 content 提取简化为 `content→unanswered_question→description→subject`，漏了 memory 侧的 `loop_type+description` 组合规则——结构化伏笔在两路投影中提取结果不同，`open_loop_closed` 在 state 侧匹配不到 created 条目，活跃伏笔残留
- 修复：state writer 新增 `_coerce_loop_content`，与 memory/writer 逐字同规则（兜底兼容 `payload._subject`，兼容 ssot_enforcer 事件格式）

### P3 renderer 边界
- **本项目独有缺陷**：`_render_foreshadowing_panel` 对 `plot_threads` 未做类型检查，非 dict 脏数据直接 `.get` 抛 `AttributeError`
- 修复：加 `isinstance` 防护，非 dict 时回退顶层 `foreshadowing`

### P0 ssot rebuild 擦除非事件字段
- **本项目独有缺陷**：`ssot_enforcer.rebuild_projections` 整体重写 state.json，擦掉 9 个非事件顶层字段（`project_info` 30 键/`chapter_meta` 12 章/`review_checkpoints` 15 条/`world_settings`/`strand_tracker`/`entities`/`entity_state`/`protagonist_state`）——这些来自 init 初始化与审查流水线写入，不被事件日志覆盖。用户跑 `ssot rebuild` 即永久丢失项目元数据，dashboard 项目信息/世界观状态/章节摘要面板随之变空
- 修复：`rebuild_projections` 写入前读既有 state.json，`_merge_non_event_fields` 把非事件字段合并进重建结果——顶层 key 缺失才回填；事件字段（如 `progress.current_chapter`）以重建值为准，旧值不覆盖新值；`protagonist_state` 做子字段级补全（避免既有 `name` 被空 dict 屏蔽）

## 三、测试基线

全量测试 **733 passed / 17 failed**（17 个全为预存基线：15 个 `test_context_manager.py` 缺大纲文件 + 1 个 `test_memory_orchestrator.py` + 1 个 `test_prompt_integrity.py`）。本次 5 笔 fix 新增 16 个测试，0 新回归。

## 四、live 项目验证

- 凡尘之舞（`E:\workspace\webnovel2\凡尘之舞`）实跑 `ssot rebuild`：`plot_threads.foreshadowing` 回填 27 条（active 26 / resolved 1），顶层 `foreshadowing` 保留 26 条，9 个非事件字段全部保留
- 验证后已恢复备份（字节级一致），不留测试痕迹

## 五、后续可合并项

上游 `73447d73` 之后的 9 笔 commit 中，3 笔 fix（#130/#131/#125）已覆盖，4 笔 docs/chore（README 赞助商/版本导览、v7 RFC 公告、v6.2.1 release 元数据）不适用于本项目（无 `releases/` 目录、v6.2.1 版本号线、外部赞助商 banner 等上游特有内容），2 笔 merge commit 无独立内容。**无遗漏可合并项**。
