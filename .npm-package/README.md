# Webnovel Writer OpenCode Edition

网文 AI 写作工具链 — 一键安装器（lujih 分支）。

## 安装

```bash
npx @cszx/webnovel-writer-opencode init
```

需要 Node.js ≥ 22。

## 命令

| 命令 | 说明 |
|------|------|
| `npx @cszx/webnovel-writer-opencode init` | 初始化写作工作目录 |
| `npx @cszx/webnovel-writer-opencode update` | 更新写作工具链 |
| `npx @cszx/webnovel-writer-opencode uninstall` | 卸载 |

## init 选项

| 选项 | 说明 |
|------|------|
| `--offline` | 仅使用离线包，不联网 |
| `--mirror URL` | 指定下载镜像 |
| `--no-pip` | 跳过 Python 依赖安装 |
| `--quiet`, `-q` | 跳过欢迎界面 |

## 下一步

安装完成后，编辑 `.env` 文件配置 API Key，然后在工作目录打开 AI 工具（Claude Code / Codex）开始写作。

