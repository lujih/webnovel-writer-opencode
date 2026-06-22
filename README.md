# Webnovel Writer for OpenCode — AI 长篇网文创作系统

[![License](https://img.shields.io/badge/License-GPL%20v3-blue.svg)](LICENSE)
[![OpenCode](https://img.shields.io/badge/OpenCode-Compatible-purple.svg)](https://opencode.ai)
[![Python](https://img.shields.io/badge/Python-3.10+-green.svg)](https://python.org)
[![npm](https://img.shields.io/npm/v/@cszx/webnovel-writer-opencode?color=red)](https://www.npmjs.com/package/@cszx/webnovel-writer-opencode)

## 1. 引言

在 AI 辅助写作的浪潮中，一个优秀的创作系统就像是一座灯塔，指引着作者在漫长的连载旅程中保持方向感和一致性。正如 Antoine de Saint-Exupéry 在《小王子》中所说："一个人只有用心去看，才能看到真实。事物的真实本质是肉眼无法看到的。"

### 1.1 为什么我们需要这个项目

长篇网文创作的核心挑战不在于"写出一章"，而在于"写到第二百章时还记得第一章的伏笔"。传统 AI 写作工具面临三个根本性困境：

| 困境 | 表现 | 本项目的应对 |
|------|------|-------------|
| **遗忘** | AI 忘记前文设定、角色关系、已埋伏笔 | 三层 RAG 上下文 + 故事合约引擎 + DebtTracker 伏笔追踪 |
| **幻觉** | AI 凭空编造设定、前后矛盾 | Graph-RAG 实体关系图谱 + Code Checker 硬约束阻断 |
| **风格漂移** | 长篇连载中写作风格逐渐偏离初始设定 | MASTER_SETTING 合约 + OOC 审查器 + 题材模板约束 |

在《代码大全》中，Steve McConnell 强调："良好的文档能够帮助开发者避免很多不必要的错误和困惑，提高工作效率。" 对于 AI 写作而言，"文档"就是设定集、大纲和故事合约——它们是确保 AI 在 200 章后仍能遵循初始设定的关键。

### 1.2 项目的主要组成部分

一个完整的 AI 写作系统，就像一支专业的编辑团队，每个模块各司其职：

| 组成部分 | 角色 | 类比 |
|----------|------|------|
| **故事合约引擎** | 维护世界规则的"法律体系" | 总编辑 |
| **上下文管理** | 每章组装最相关的设定和伏笔 | 责任编辑 |
| **分层审查** | 13 维度结构化检查章节质量 | 审校团队 |
| **记忆系统** | 三层记忆压缩和检索 | 资料管理员 |
| **知识库** | CSV 结构化写作技法和命名规则 | 风格指南 |

正如《人月神话》中 Fred Brooks 所言："良好的文档是软件产品成功的关键。" 本项目的每个模块都围绕着一个核心理念：**将写作知识结构化，让 AI 能够持续、一致地创作**。

## 2. 项目标题和描述

### 2.1 项目名称的含义

**Webnovel Writer for OpenCode** —— 这个名字传达了三个关键信息：

| 要素 | 含义 |
|------|------|
| **Webnovel** | 专为长篇网文设计，理解网文的独特节奏和爽点体系 |
| **Writer** | 不是"生成器"，而是辅助作者完成创作的"写作伙伴" |
| **for OpenCode** | 基于 OpenCode 框架，与 AI 编程助手深度集成 |

### 2.2 项目描述

Webnovel Writer for OpenCode 是一个面向长篇中文网文的 AI 辅助写作系统。它通过分层 RAG 检索、故事合约引擎和结构化质量审查，解决 AI 在连载创作中的"遗忘"和"幻觉"问题，支持从项目初始化到平台发布的全流程。

本项目源于 [lingfengQAQ/webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer)，已深度重构为 OpenCode 架构，扩展了审查器体系、CSV 结构化知识库、故事合约引擎、记忆系统、发布通道等模块。v2.8 起借鉴 [Narcooo/inkOS](https://github.com/Narcooo/inkOS) 的核心设计，引入了 SSOT 事件溯源、Observer→Reflector 双段事实提取、运行时产物持久化和真相文件 Markdown 投影。

**核心设计理念**：不是让 AI "代替"作者，而是让 AI "理解"作者创造的世界，并在这个世界的约束下持续创作。

## 3. 安装和使用说明
> **新手指南** &nbsp; 从安装到完成第一本书的手把手教程，每一步都有详细说明：[docs/guides/getting-started.md](docs/guides/getting-started.md)

### 3.1 一键安装（推荐）

**系统要求**：Node.js ≥ 22、Python 3.10+、OpenCode 运行环境。

```bash
npx @cszx/webnovel-writer-opencode init
```

离线包内置（1.7 MB），无需联网下载。自动检测 Python 并安装依赖。完成后在工作目录打开 OpenCode 即可开始写作。

后续更新和卸载：

```bash
npx @cszx/webnovel-writer-opencode update   # 更新到最新版
npx @cszx/webnovel-writer-opencode uninstall # 卸载
```

### 3.2 Python 安装脚本（备选）

适用于无 Node.js 环境的场景。

**macOS / Linux**：

```bash
curl -fsSL https://raw.githubusercontent.com/lujih/webnovel-writer-opencode/master/install.py | python3
```

**Windows**（PowerShell）：

```powershell
Invoke-WebRequest -Uri https://raw.githubusercontent.com/lujih/webnovel-writer-opencode/master/install.py -OutFile install.py
python install.py
```

运行 `python install.py`（无参数）进入交互式菜单：

| 命令 | 说明 |
|------|------|
| `python install.py` | 交互菜单 |
| `python install.py --update` | 更新到最新版 |
| `python install.py --incremental` | 增量更新（仅变更文件） |
| `python install.py --clean` | 清洁安装 |
| `python install.py --uninstall` | 卸载 |
| `python install.py --venv` | 虚拟环境安装 |

中国大陆用户下载失败时自动切换 GitHub 镜像。

### 3.2 配置 API Key

编辑 `.env` 文件：

```bash
# Embedding 模型（向量化章节内容）
EMBED_BASE_URL=https://api-inference.modelscope.cn/v1
EMBED_MODEL=Qwen/Qwen3-Embedding-8B
EMBED_API_KEY=your_api_key

# Rerank 模型（检索结果重排）
RERANK_BASE_URL=https://api.jina.ai/v1
RERANK_MODEL=jina-reranker-v3
RERANK_API_KEY=your_api_key
```

### 3.3 在 OpenCode 中使用

所有功能通过 OpenCode 的斜杠命令调用：

| 命令 | 功能 | 使用场景 |
|------|------|----------|
| `/webnovel-init` | 初始化新书项目 | 开始一本新书 |
| `/webnovel-plan` | 规划大纲 | 设计故事结构 |
| `/webnovel-write` | 撰写单章 | 日常写作 |
| `/webnovel-write-batch` | 批量写作 | 集中赶稿 |
| `/webnovel-review` | 审查润色 | 质量把控 |
| `/webnovel-delete` | 安全删除章节 | 回退重写 |
| `/webnovel-rewrite` | 重写章节 | 翻修烂章 |
| `/webnovel-heal` | 修复问题章节 | 诊断修复 |
| `/webnovel-export` | 导出正文 | 分发备份 |
| `/webnovel-publish` | 发布番茄小说 | 平台上线 |
| `/webnovel-dashboard` | 可视化看板 + 文风约束编辑 | 全局监控 + 文风管理 |
| `/webnovel-query` | 查询设定 | 快速检索 |

正如《C++编程思想》中所说："代码的清晰是优秀软件的基石。" 每个命令都设计为自解释的——你只需要告诉系统"做什么"，它会自动处理"怎么做"。

### 3.4 提供帮助和支持

遇到问题时，可以通过以下渠道获取帮助：

- **问题追踪**：[GitHub Issues](https://github.com/lujih/webnovel-writer-opencode/issues)
- **新手完全指南**：[docs/guides/getting-started.md](docs/guides/getting-started.md)
- **项目文档**：[docs/](docs/) 目录下的架构、指南和运维文档

Donald Knuth 在《计算机程序设计艺术》中写道："程序是为人类读写的，不是为机器执行的。" 我们的文档同样遵循这一原则——为作者而写，而非为 AI 而写。

## 4. 项目结构和文件组织

### 4.1 核心目录结构

```
webnovel-writer/                  # 仓库根目录（OpenCode 工作区）
├── install.py                    # 一键安装引导脚本
├── manifest.json                 # 文件清单（SHA256，增量更新用）
├── .env                          # API 配置（不提交）
├── README.md                     # 项目技术概览
├── README_CN.md                  # 项目详细说明（本文件）
│
├── .opencode/                    # OpenCode 引擎核心
│   ├── agents/                   # Agent 定义
│   │   ├── context-agent.md      # 上下文搜集 Agent
│   │   ├── data-agent.md         # 数据处理 Agent
│   │   ├── reviewer.md           # 审查 Agent（13 维度结构化检查）
│   │   └── deconstruction-agent.md # 作品解构 Agent
│   │
│   ├── skills/                   # 13 个 Skills（webnovel-*）
│   │
│   ├── dashboard/                # 可视化面板
│   │   ├── app.py                # FastAPI 应用入口
│   │   ├── server.py             # 服务器配置
│   │   └── frontend/             # React 19 前端（ECharts）
│   │
│   ├── installer/                # 安装器（纯 stdlib）
│   ├── genres/                   # 38+ 题材模板
│   ├── references/               # 结构化知识库
│   │   ├── csv/                  # 9 张知识表 + BM25 检索
│   │   ├── writing/              # 写作技法参考
│   │   └── review/               # 审查规则
│   │
│   └── scripts/                  # Python 核心脚本
│       ├── webnovel.py           # CLI 统一入口（28 个子命令）
│       ├── data_modules/         # 核心数据模块（60+ 个）
│       └── tests/                # 测试（59 个测试文件）
│
└── docs/                         # 文档
    ├── architecture/             # 系统架构
    ├── guides/                   # 使用指南
    ├── operations/               # 运维文档
    └── research/                 # 研究笔记
```

### 4.2 六层数据流架构

项目采用六层管道架构，每层为下一层提供经过加工的数据：

| 层级 | 名称 | 职责 | 核心模块 |
|------|------|------|----------|
| **L1** | Knowledge | CSV 结构化知识 + BM25 检索 | `references/csv/`, `reference_search.py` |
| **L2** | Reasoning | 题材路由 + 反模式排序 | `genres/` |
| **L3** | Contract | MASTER_SETTING + 卷纲章纲 + 审查合约 | `story_system_engine.py`, `story_contracts.py` |
| **L4** | Context | JSON 拼装写作上下文 | `context_manager.py` |
| **L5** | Commit | 事实提取 + 事件溯源 + 投影路由 | `chapter_commit_service.py`, `event_log_store.py` |
| **L6** | Projection | 5 个 Writers：state/index/summary/memory/vector | 各 `*_writer.py` 模块 |

正如《代码大全》中所说："一个好的目录结构可以帮助开发者快速地找到他们需要的信息，从而提高生产效率。" 六层架构的设计确保了每一层都有明确的输入和输出，修改某一层不会影响其他层。

### 4.3 关键子系统

| 子系统 | 核心文件 | 功能 |
|--------|----------|------|
| **故事合约引擎** | `story_system_engine.py` | MASTER_SETTING 维护 + Runtime 合同生成 |
| **SSOT 事件溯源** | `ssot_enforcer.py` | append-only 事件日志 + 投影重建 + 一致性校验 |
| **Override 合约引擎** | `override_contract_engine.py` | 世界规则版本化演进 + 上下文提示生成 |
| **Observer→Reflector** | `observer_settler.py` | 双段事实提取——自由文本提取+Pydantic Schema 校验 |
| **记忆系统** | `memory/orchestrator.py` | 三层记忆（工作/情节/语义）+ 压缩编排 |
| **审查管线** | `review_pipeline.py` | Code Checker 预处理 → 13 维度结构化审查（最多 3 轮收敛） |
| **Graph-RAG** | `entity_linker.py` | 实体关系图谱 + SQLite 持久化 |
| **DebtTracker** | `index_debt_mixin.py` | 伏笔创建 → 偿还 → 硬约束阻塞 |
| **发布模块** | `publisher/` | Playwright 浏览器自动化 + HTTP API |

## 5. 贡献指南

### 5.1 如何为项目做出贡献

贡献开源项目不仅仅是代码的贡献，还包括文档完善、问题报告、新功能建议。正如《人月神话》中所说："好的程序员不仅仅是写出能工作的代码，还需要写出能维护的代码。"

**了解项目**：
- 阅读 [docs/architecture/](docs/architecture/) 了解系统架构
- 阅读 [docs/guides/getting-started.md](docs/guides/getting-started.md) 了解使用流程
- 参与 Issues 讨论，与社区交流

**找到贡献的机会**：
- 查看 [GitHub Issues](https://github.com/lujih/webnovel-writer-opencode/issues)，找到可以解决的问题
- 关注项目的未来计划和里程碑

**贡献代码**：
1. Fork 项目到你的 GitHub 账户
2. 在本地开发和测试你的代码
3. 确保代码遵循项目的编码标准和风格指南
4. 提交 Pull Request

### 5.2 提交问题和拉取请求的流程

| 方面 | 提交问题 | 提交拉取请求 |
|------|----------|-------------|
| 标题 | 明确、具体 | 清晰、描述目的 |
| 描述 | 详细、包含重现步骤 | 详细、解释更改的必要性 |
| 附加信息 | 屏幕截图、日志 | 符合编码和风格标准 |

在《代码大全》中，作者强调："代码是写给人看的，顺便给机器执行。" 提交的 Issue 和 PR 同样应该让人容易理解。

**提交规范**：所有提交必须遵循 [Conventional Commits](https://www.conventionalcommits.org/) 格式：

```
<type>: <简短描述>

Co-Authored-By: AI Assistant <noreply@anthropic.com>
```

常用类型：`feat:`（新功能）、`fix:`（Bug 修复）、`docs:`（文档）、`refactor:`（重构）、`test:`（测试）。

## 6. 许可证

### 6.1 选择合适的开源许可证

本项目采用 **GPL v3** 许可证，继承自原项目 [lingfengQAQ/webnovel-writer](https://github.com/lingfengQAQ/webnovel-writer)。

GPL v3 要求任何使用、修改或分发本代码的人必须将其更改公开，并使用相同的许可证。正如 Richard Stallman 在《自由软件，自由社会》中所说："自由软件是关于自由和合作。"

### 6.2 许可证的含义

| 权限 | 允许 | 条件 |
|------|------|------|
| 商业使用 | ✓ | 必须开源衍生作品 |
| 修改 | ✓ | 必须使用 GPL v3 协议 |
| 分发 | ✓ | 必须附上原始许可证 |
| 私人使用 | ✓ | 无限制 |

完整许可证文本见 [LICENSE](LICENSE) 文件。

## 7. 联系信息和致谢

### 7.1 联系方式

如果你有任何问题或建议，请通过以下方式联系：

- **GitHub Issues**：[github.com/lujih/webnovel-writer-opencode/issues](https://github.com/lujih/webnovel-writer-opencode/issues)
- **GitHub Discussions**：[github.com/lujih/webnovel-writer-opencode/discussions](https://github.com/lujih/webnovel-writer-opencode/discussions)

### 7.2 致谢

每一个成功的开源项目背后，都有一个支持和贡献的社区。正如《人类简史》中所说："合作是人类成功的秘诀。"

| 贡献者/项目 | 贡献内容 |
|------------|----------|
| **[lingfengQAQ](https://github.com/lingfengQAQ)** | 原项目作者，奠定了核心架构和写作流程 |
| **[Narcooo/inkOS](https://github.com/Narcooo/inkOS)** | 自主写作 AI Agent，提供了 Observer→Reflector 双段提取、7 文件真相投影、输入治理三层结构等关键设计灵感 |
| **[OpenCode](https://opencode.ai)** | AI 编程助手框架，提供了 Skills 和 Agents 运行时 |
| **[Cppys/OpenNovel](https://github.com/Cppys/OpenNovel)** | 番茄小说发布技术方案参考 |
| **[@YuerLee](https://github.com/YuerLee)** | 在 macOS 兼容性、安装脚本优化和流程反馈上贡献了重要改进 |
| **所有社区贡献者** | 通过 Issues、反馈和实际写作测试帮助项目持续迭代 |

### 7.3 社区文化

我们欢迎每一个人的参与和贡献，无论你的技能水平如何，都有你的一席之地。无论你是经验丰富的 Python 开发者、热爱写作的网文作者，还是刚刚接触开源的新手，你的每一次 Issue、每一个 PR、每一条建议，都是项目前进的动力。

---

## 结语

在我们的 AI 写作探索之旅中，理解系统架构是迈向高质量创作的重要一步。然而，掌握新工具、新理念，始终需要时间和坚持。从写作的角度看，使用 AI 辅助创作往往伴随着不断的试错和调整——作者和 AI 在一次次互动中逐渐找到最佳的协作节奏。

这就是为什么当我们遇到 AI 的"遗忘"或"幻觉"时，我们应该将其视为理解系统运作方式的机会，而不仅仅是困扰。通过理解上下文管理、故事合约和审查管线的工作原理，我们不仅可以修复当前的章节，更可以提升整体的创作质量，在未来的章节中保持更高的一致性。

我鼓励大家积极参与进来，不断探索 AI 辅助写作的可能性。无论你是网文作者还是技术开发者，希望这个项目能对你的创作之路有所帮助。如果你觉得这个项目有用，不妨点击 Star，或者留下你的 Issue 分享你的使用体验和改进建议。

每一次的 Star、Issue、PR 和分享都是对这个项目的最大支持，也是我们持续改进和创新的动力。让我们一起，用代码和创意，书写更好的故事。
