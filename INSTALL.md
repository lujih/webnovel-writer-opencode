---
name: webnovel-install
description: 自动安装 Webnovel Writer。触发条件："安装"、"重新安装"、"更新"、"安装依赖"、"setup"、"初始化环境"。
compatibility: opencode
allowed-tools: Bash
---

# Webnovel Writer 安装

## 目标

一键安装或更新 Webnovel Writer 插件到当前 OpenCode 工作区。

## 安装

```bash
npx @cszx/webnovel-writer-opencode init
```

离线包内置，无需联网下载。自动检测 Python 并安装依赖。

## 命令

| 命令 | 说明 |
|------|------|
| `npx @cszx/webnovel-writer-opencode init` | 安装工作目录 |
| `npx @cszx/webnovel-writer-opencode update` | 更新到最新版 |
| `npx @cszx/webnovel-writer-opencode uninstall` | 卸载 |

## 安装选项

| 选项 | 说明 |
|------|------|
| `--offline` | 仅使用离线包，不联网 |
| `--mirror URL` | 指定下载镜像（国内推荐 ghproxy.com） |
| `--no-pip` | 跳过 Python 依赖安装 |
| `--quiet`, `-q` | 跳过欢迎界面 |

## 安装过程

1. **检测环境** — Node.js ≥ 22
2. **部署工具链** — 从离线包（或网络下载）解压 `.opencode/`
3. **安装 Python 依赖** — `pip install -r requirements.txt`
4. **完成** — 在 OpenCode 中打开工作目录，开始写作

## 常见问题

| 问题 | 解决 |
|------|------|
| Node.js 版本过低 | 升级至 Node.js ≥ 22：https://nodejs.org/ |
| 下载失败 | 国内网络使用 `--mirror https://ghproxy.com/` |
| pip 安装失败 | 检查 Python ≥ 3.10，或用 `--no-pip` 跳过后手动安装 |
| 安装后异常 | 删除 `.opencode/` 重新运行 init |
| 完全移除 | `npx @cszx/webnovel-writer-opencode uninstall` |
| **OpenCode 2.x 下模型看不到项目规则** | 缺 `AGENTS.md`。v2 只发现它、不再回退 `CLAUDE.md`，见下 |

## OpenCode 版本要求

| 版本 | 要求 |
|------|------|
| **1.x** | 需 **≥ 1.18.29**（写保护插件的双入口写法从该版本起生效） |
| **2.x** | 直接可用 |

### `AGENTS.md`（v2 必需）

OpenCode 2.x **只发现 `AGENTS.md`，不再回退 `CLAUDE.md`**。没有它，16 个 skill
与 6 个 agent 赖以工作的硬规则（SSOT 写入纪律、审查 fail-closed 等）对模型
完全不可见——模型会照常调用 skill，但不知道那些约束。

```bash
python .opencode/scripts/webnovel.py sync-agents-md
```

`CLAUDE.md` 是正本，`AGENTS.md` 是它的逐字节镜像；改完 `CLAUDE.md` 需重跑该命令
（CI 会校验二者一致）。

- **npm 安装**：`2.9.2-13` 起离线包内置两份文件，安装时部署到**工作区根**
  （与 `.opencode/` 平级——那正是 OpenCode 2.x 能发现的位置），无需手工生成；
  `≤2.9.2-12` 的旧版不含，执行 `npx @cszx/webnovel-writer-opencode update` 补齐。
  装完若未发现 `AGENTS.md`，安装器会出声警告。
- **git 安装**：需自行执行上面的 `sync-agents-md`。

⚠️ **不要把 `AGENTS.md` 加进 `.gitignore`** —— 它是仓库的正式产物。

### 不要开启 `formatter`

OpenCode 内置的格式化器（prettier / biome）覆盖 `.md`，而**章节正文全是 `.md`**。
开启会把已发布正文重排折行、规范空白——那是改你的作品；且格式化在写盘**之后**
才跑，拦不住。本项目的配置已显式关闭，升级或合并配置时留意别被打开。

## 已知的 OpenCode 自动改写（实测行为，无需处理）

1. 每次运行 OpenCode 都会往 `.opencode/opencode.json` 插入 `"$schema"` 键，
   并往 `.opencode/package.json` 追加 `@opencode-ai/plugin` 依赖。回退后下次运行
   又会出现，属宿主的迁移/依赖管理行为，无副作用。
2. **不要**在 OpenCode 2.x 里执行官方的 "Migrate my OpenCode configuration"
   迁移提示词——它会把配置转成 V2 原生形态，此后 OpenCode **1.x 将无法加载**
   该配置（rc=1 致命拒绝），双版本兼容的前提是配置保持 V1 安全形态。
