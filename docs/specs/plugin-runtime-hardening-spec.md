# Plugin Runtime Hardening — 合并计划

> **实现状态（2026-10-19）**：`data_modules/write_gates/` 三个阶段门禁
> （prewrite / precommit / postcommit）已实现并**已接入生产代码**——
> `ChapterCommitService.persist_commit()` 在落盘前跑 precommit，
> `apply_projections()` 收尾跑 postcommit。门禁自身 fail-closed
> （检查器异常 → 转 issue，不吞）。
>
> 本文档第 4–7 节保留的是**原始合并计划**，其中的清单勾选框描述的是
> 当时的验收思路，不是当前待办。若要判断现状请读代码与
> `tests/test_write_gates_wiring.py`（钉住接线、fail-closed、死代码检测）。

## 1. 背景

原项目 `9c4419c` 提交了 "harden webnovel plugin runtime"（7570 行新增），引入了写入门禁、诊断工具、产物验证等运行时防护机制。

本项目基于 OpenCode/KiloCode 框架（非 Claude Code），需要适配后同步关键改进。

## 2. 目标

- 防止 AI 直接写入受保护文件（state.json、index.db、commits）
- 提交前验证产物完整性，阻止畸形数据进入系统
- 提交后验证投影完整性，确保数据不丢失
- 提供项目健康诊断工具

## 3. 不同步的变更及理由

| 原项目变更 | 理由 |
|-----------|------|
| `hooks/`（Claude Code hooks） | 我们用 OpenCode plugin system 替代 |
| `project_phase.py`（10 阶段生命周期） | 与 workflow_checkpoint 互补但不等价，引入成本高（2 天重写），且我们的 5 阶段模型已够用 |
| `run_behavior_evals.py` | 测试框架，不直接影响功能 |
| `validate_plugin_package.py` | 插件验证，我们不是 plugin 分发模式 |
| `projection_log.py` | 可以后续单独评估 |
| `project_status.py` | 可用 status_reporter.py 替代 |

## 4. 阶段 1：写入防护（1 天）

### 4.1 OpenCode Plugin Write Guard

**文件**：`.opencode/plugins/write-guard.js`

**原理**：利用 OpenCode 的 `tool.execute.before` hook，拦截 Write/Edit 调用，阻止直接写入受保护文件。

**受保护路径**：
- `.webnovel/state.json`
- `.webnovel/index.db`
- `.webnovel/vectors.db`
- `.webnovel/memory_scratchpad.json`
- `.story-system/commits/`

**白名单**（允许的写入来源）：
- `webnovel.py` CLI 命令
- `chapter-commit` 命令
- `write-gate` 命令
- Dashboard API 调用

**实现**：
```javascript
// .opencode/plugins/write-guard.js
export default async function({ project }) {
  const PROTECTED = [
    '.webnovel/state.json',
    '.webnovel/index.db',
    '.webnovel/vectors.db',
    '.webnovel/memory_scratchpad.json',
    '.story-system/commits/',
  ]

  return {
    'tool.execute.before': async (input, output) => {
      if (input.tool === 'write' || input.tool === 'edit') {
        const path = output.args?.path || output.args?.file_path || ''
        const normalized = path.replace(/\\/g, '/').toLowerCase()
        if (PROTECTED.some(p => normalized.includes(p))) {
          throw new Error(
            `禁止直接写入 ${path}。请使用 CLI 命令：python webnovel.py chapter-commit`
          )
        }
      }
    }
  }
}
```

**验证标准**：
- [ ] 直接 Write 工具写入 state.json 被阻止
- [ ] 通过 webnovel.py CLI 写入不受影响
- [ ] Dashboard API 写入不受影响（走 HTTP 不走 Write 工具）
- [ ] 所有现有测试通过

### 4.2 Artifact Validator

**文件**：`.opencode/scripts/data_modules/artifact_validator.py`

**原理**：在现有 `chapter_commit_schema.py` 的 Pydantic 模型基础上，添加验证编排层。

**核心函数**：

| 函数 | 功能 |
|------|------|
| `validate_artifact_payload(schema_cls, payload)` | 单个产物的 schema 验证 + 策略检查 |
| `validate_artifact_file(schema_cls, path)` | 从文件读取 + 验证 |
| `validate_commit_artifact_files(project_root, chapter)` | 并行验证 4 个产物，合并报告 |
| `validate_chapter_commit(project_root, chapter)` | 验证已持久化的 commit JSON |

**策略检查（`_policy_issues`）**：
- review_result: blocking_count > 0 → error
- fulfillment_result: missed_nodes 非空 → warning
- disambiguation_result: pending 非空 → error
- extraction_result: accepted_events 为空 → warning

**验证报告格式**：
```json
{
  "ok": true,
  "errors": [{"code": "...", "severity": "blocker", "message": "..."}],
  "warnings": [{"code": "...", "severity": "warning", "message": "..."}]
}
```

**验证标准**：
- [ ] 正确的产物通过验证
- [ ] 缺失的产物返回 error
- [ ] 格式错误的 JSON 返回 error
- [ ] blocking review 返回 error
- [ ] missed nodes 返回 warning
- [ ] 测试覆盖：正常/缺失/格式错误/策略违反

## 5. 阶段 2：写入门禁（2 天）

### 5.1 Gate 基础设施

**文件**：`.opencode/scripts/data_modules/write_gates/__init__.py`

**核心函数**：

| 函数 | 功能 |
|------|------|
| `issue(code, severity, message, impact, repair)` | 标准化错误/警告对象工厂 |
| `gate_report(stage, errors, warnings)` | 结构化报告构建器 |
| `format_gate_report(report, fmt)` | JSON/text 输出格式化 |
| `run_write_gate(stage, project_root, chapter)` | 分发器，路由到对应阶段 |

**验证标准**：
- [ ] issue() 返回标准格式对象
- [ ] gate_report() 正确计算 ok 字段
- [ ] format_gate_report() 支持 JSON 和 text 格式

### 5.2 Prewrite Gate

**文件**：`.opencode/scripts/data_modules/write_gates/prewrite.py`

**检查项**：
1. 合同文件存在性（chapter_*.json、volume_*.json）
2. 复用现有 `PrewriteValidator.build()` 检查
3. runtime sources 加载（复用 `story_runtime_sources.load_runtime_sources()`）

**不引入**：project_phase 检查（我们没有 10 阶段模型）

**验证标准**：
- [ ] 合同文件缺失时返回 error
- [ ] disambiguation pending 时返回 error
- [ ] 正常情况返回 ok=true

### 5.3 Precommit Gate

**文件**：`.opencode/scripts/data_modules/write_gates/precommit.py`

**检查项**：
1. chapter file 存在且非空
2. 调用 `validate_commit_artifact_files()` 验证 4 个产物
3. 收集所有 error/warning 到 gate report

**验证标准**：
- [ ] chapter file 不存在时返回 error
- [ ] 产物验证失败时返回 error
- [ ] 正常情况返回 ok=true

### 5.4 Postcommit Gate

**文件**：`.opencode/scripts/data_modules/write_gates/postcommit.py`

**检查项**：
1. commit JSON 文件存在且可解析
2. commit status 为 accepted
3. 投影状态检查（state/index/summary/memory/vector）
4. summary 文件存在性检查（当投影声称 done 时）
5. index.db 存在性检查

**验证标准**：
- [ ] commit 文件不存在时返回 error
- [ ] 投影失败时返回 error
- [ ] 投影声称 done 但文件不存在时返回 error

### 5.5 集成到 chapter-commit 流程

在 `chapter_commit_service.py` 的 `build_commit()` 和 `apply_projections()` 中集成 gates：

```python
# build_commit() 开头
from .write_gates import run_write_gate
precommit = run_write_gate("precommit", self.project_root, chapter)
if not precommit["ok"]:
    raise ValueError(f"Precommit gate failed: {precommit['errors']}")

# apply_projections() 结尾
postcommit = run_write_gate("postcommit", self.project_root, chapter)
if not postcommit["ok"]:
    logger.warning("Postcommit gate failed: %s", postcommit['errors'])
```

**验证标准**：
- [ ] precommit gate 阻止畸形提交
- [ ] postcommit gate 记录投影失败
- [ ] 正常流程不受影响
- [ ] 所有现有测试通过

## 6. 阶段 3：诊断工具（2 天）

### 6.1 Doctor 模块

**文件**：`.opencode/scripts/data_modules/doctor.py`

**检查类别**：

| 类别 | 检查内容 |
|------|----------|
| 文件检查 | 必需目录（8个）、必需文件（7个）、合同文件 |
| JSON 检查 | state.json 解析、MASTER_SETTING.json 解析、必需键 |
| SQLite 检查 | index.db 表存在性、行数 |
| 投影检查 | 最新投影状态、失败/待处理检测 |
| RAG 检查 | embed/rerank API key 配置 |
| Python 检查 | 版本 >= 3.10、必需模块导入 |

**输出格式**：
```json
{
  "ok": true,
  "checks": [
    {"name": "state.json", "status": "pass", "detail": ""},
    {"name": "index.db", "status": "warn", "detail": "表 entities 缺失"}
  ],
  "blocking": 0,
  "warnings": 1,
  "repair_hints": ["运行 webnovel.py migrate 修复 index.db"]
}
```

**验证标准**：
- [ ] 健康项目返回 ok=true
- [ ] 缺失文件返回 blocking error
- [ ] JSON 解析失败返回 blocking error
- [ ] SQLite 表缺失返回 warning

### 6.2 webnovel-doctor Skill

**文件**：`.opencode/skills/webnovel-doctor/SKILL.md`

**功能**：
- 调用 `doctor.py` 执行诊断
- 格式化输出诊断报告
- 提供修复建议

**验证标准**：
- [ ] `/webnovel-doctor` 命令可执行
- [ ] 输出格式化的诊断报告
- [ ] 修复建议可操作

## 7. 实施顺序

```
阶段 1（1天）
├── 1.1 write-guard.js plugin
└── 1.2 artifact_validator.py + 测试

阶段 2（2天）
├── 2.1 write_gates/__init__.py
├── 2.2 write_gates/prewrite.py
├── 2.3 write_gates/precommit.py
├── 2.4 write_gates/postcommit.py
└── 2.5 集成到 chapter_commit_service.py

阶段 3（2天）
├── 3.1 doctor.py
└── 3.2 webnovel-doctor SKILL.md
```

## 8. 验证标准汇总

- [ ] Write guard 阻止直接写入受保护文件
- [ ] Write guard 不阻止 CLI 和 Dashboard 写入
- [ ] artifact_validator 正确验证 4 个产物
- [ ] precommit gate 阻止畸形提交
- [ ] postcommit gate 检测投影失败
- [ ] doctor 输出可操作的诊断报告
- [ ] 所有现有测试通过（645 passed）
- [ ] 新增测试覆盖 write gates 和 artifact validator

## 9. 风险与缓解

| 风险 | 缓解 |
|------|------|
| Write guard 误阻止合法写入 | 白名单机制 + 环境变量禁用开关 |
| Gate 检查导致 commit 变慢 | 纯本地文件检查，无网络调用，<100ms |
| Doctor 检查过于频繁 | 只在用户主动调用时执行，不自动运行 |
| project_phase 缺失导致 gate 检查不完整 | 轻量方案：gate 内部直接检查必要条件，不依赖外部阶段解析 |
