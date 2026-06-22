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
