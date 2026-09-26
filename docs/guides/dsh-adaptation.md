# DeepSeek Harness 适配

webnovel-writer 的 16 个 skill（`SKILL.md`）与 6 个 agent（`*.md`）原生面向
**OpenCode** 分发在 `.opencode/skills/` 与 `.opencode/agents/`。
**DeepSeek Harness**（[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)，
"Cordis 全插件 agent 框架"）的 skill provider（`@deepseek-ai/dsh-skill-filesystem`）
只扫描以下目录（**不读 `.opencode/`**）：

| 根 | 来源 | rank |
|----|------|------|
| `<project>/.dsh/skills` | 项目级，按 `.git` 向上定位根 | 100 |
| `<project>/.agents/skills` | 项目级共享 agent 配置 | 200 |
| `customSkillDirs`（cordis.yml 配置） | 自定义 | 300 |
| `$DSH_HOME/skills`（默认 `~/.dsh`） | 用户级 | 400 |
| `$DSH_AGENTS_HOME/skills`（默认 `~/.agents`） | 用户级共享 | 500 |
| `DSH_BUNDLED_SKILL_DIR` | 宿主内置 | 600 |

适配层 `.dsh/` 即把 OpenCode 资产**镜像**进 DSH 的项目级 skill 根，
使 webnovel-writer 在 DSH 下可用；OpenCode 侧资产**零改动**，二者可并存。

## 目录结构

```
.dsh/
  skills/
    webnovel-writer/SKILL.md        # 桥接层（入口）：按意图路由到 16 个子 skill + 6 个 agent
    webnovel-write/SKILL.md         # 各子 skill 的 DSH 镜像（frontmatter 保留 + DSH 适配段）
    ... （共 17 个，含桥接层）
  agents/
    context-agent.md                # 各 agent 的 DSH 镜像
    ... （共 6 个）
```

## 生成器

`.dsh/` 是派生产物，由生成器单向维护（`.opencode/` 为 SSOT）：

```bash
python -X utf8 .opencode/scripts/webnovel.py dsh-sync            # 全量重新生成
python -X utf8 .opencode/scripts/webnovel.py dsh-sync --check     # 幂等校验（有 stale 返回 1）
```

生成器 `.opencode/scripts/data_modules/dsh_sync.py` 做三件事：

1. **镜像**：把 16 个 `SKILL.md` + 6 个 agent `.md` 复制进 `.dsh/`。
2. **保留 frontmatter**：原 YAML `name`/`description`（满足 DSH 的
   `name`+`description` 必填 + kebab-case 校验；目录名即 skill name）。
   非标准键（`compatibility`/`allowed-tools`/`mode`/`tools`）原样保留——
   DSH 解析器只校验 `name`/`description`/`whenToUse`/`invocation`/`metadata`，
   未知键忽略，不影响加载。
3. **注入 DSH 适配段**（frontmatter 之后、原正文之前）：
   - **SCRIPTS_DIR 自举**：OpenCode 侧 skill 依赖调用方在 prompt 传入
     `${SCRIPTS_DIR}`；DSH 下模型读 skill 正文时可能没有该变量，适配段给出
     `export SCRIPTS_DIR="$(git rev-parse --show-toplevel)/.opencode/scripts"`
     的自举命令（兜底 `$PWD/.opencode/scripts`）。
   - **工具映射表**：OpenCode 工具 → DSH 等价物（见下表），命令/硬规则/
     步骤顺序不变。

## 工具映射（OpenCode → DSH）

| OpenCode 工具 | DSH 等价物 | 备注 |
|---|---|---|
| `Agent`（委派 subagent，`subagent_type` 指定 agent 名） | `subagent` 工具 | 委派 prompt 附上对应 `.dsh/agents/<name>.md` 正文；默认后台 |
| `AskUserQuestion` | `ask_user_question` | 单选/多选/确认 |
| `Task`（后台任务） | `job_*` 系列（job_list / job_output / job_kill） | |
| `Bash` / `pwsh` | DSH `pwsh`（Windows）/ `bash` | skill 内 bash 代码块走 shell 工具 |
| 文件读写（Write/Edit/Read/Grep/Glob） | DSH 同名工具 | 行为一致 |

## 与 OpenCode 的差异点

1. **Skill 发现**：DSH 只扫 `.dsh/skills` 与 `.agents/skills`；
   OpenCode 的 `.opencode/skills/` 不被 DSH 直接发现——`.dsh/` 是粘合点。
2. **Agent 发现**：DSH 无 `.opencode/agents/` 等价物；委派走 `subagent` 工具 +
   把 agent 定义正文拼入 prompt（桥接 skill 已给出 6 个 agent 的映射表）。
3. **插件钩子**：OpenCode `.opencode/plugins/write-guard.js`
   （`tool.execute.before` 拦截 Write/Edit/Bash 直写受保护文件）在 DSH
   **不自动生效**。受保护文件（`.webnovel/state.json`、`.webnovel/index.db`、
   `.story-system/commits/` 等）的写约束在 DSH 下退化为**流程纪律**——
   skill/agent 正文已写明"保护文件一律走 CLI（`chapter-commit` 等），
   不用写工具直改"。`WEBNOVEL_DISABLE_WRITE_GUARD=1` 仅在 OpenCode 侧有意义。
4. **不变式（与 harness 无关）**：SSOT 事件溯源（`publish_event` 单一写路径）、
   `atomic_write_json` 原子写 + WinError 5 退避、`chapter_status` 字符串值、
   伏笔三侧内容契约（`foreshadowing_utils.coerce_loop_content`）、
   SQLite WAL/busy_timeout——全部为**数据层**行为，DSH 与 OpenCode 两侧一致，
   换 harness 不影响数据正确性。

## 使用

- **DSH**：在含 `.webnovel/state.json` 的书项目或本仓库根打开 DSH，
  模型会按意图加载 `.dsh/skills/webnovel-writer/SKILL.md`（桥接层），
  再路由到对应子 skill；委派 agent 走 `subagent`。
- **OpenCode**：行为不变，仍走 `.opencode/skills/` + `.opencode/agents/`。
- **新书初始化**（`webnovel-init`）不复制 `.dsh/`——书项目只需 OpenCode 资产 +
  本仓库作为 skills 源；如需在 DSH 下写新书，在书项目侧再跑一次 `dsh-sync`
  或把 `.dsh/` 随仓库分发。

## 维护

改 `.opencode/` 下任何 skill/agent 后，重跑一次
`python -X utf8 .opencode/scripts/webnovel.py dsh-sync` 同步到 `.dsh/`，
再提交。`--check` 可在 CI 里做幂等门禁（stale 返回 1）。
