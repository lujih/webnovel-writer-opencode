# Webnovel Writer for OpenCode — 新手完全指南

本教程带你从零开始，完成一篇网文的初始化、规划、写作、审查、导出全流程。每一步都有可直接复制的命令和详细说明。

**预计总耗时**：首次安装 2 分钟。

### 完整体验路线图

```text
1. npx init  →  2. 配 API Key  →  3. /webnovel-init
         ↓
5. /webnovel-init  →  6. /webnovel-plan  →  7. /webnovel-write
         ↓
8. /webnovel-review  →  9. /webnovel-export  →  10. 更新/迭代
```

> 每步约 2-10 分钟。写一章完整走完约 5 分钟，之后逐章循环第 7-8 步即可。

---

## 目录

- [1. 准备工作](#1-准备工作)
- [2. 安装](#2-安装)
- [3. 初始化你的第一本书](#3-初始化你的第一本书)
- [4. 大纲规划](#4-大纲规划)
- [5. 开始写作](#5-开始写作)
- [6. 章节审查](#6-章节审查)
- [7. 导出与发布](#7-导出与发布)
- [8. 更新插件](#8-更新插件)
- [9. 进阶玩法](#9-进阶玩法)
- [10. 常见问题 (FAQ)](#10-常见问题-faq)

---

## 1. 准备工作

### 1.1 什么是 OpenCode？

OpenCode 是一个 AI 编程助手，类似 Cursor 或 GitHub Copilot，但更强大——它能执行命令、读写文件、调用子代理完成复杂任务。Webnovel Writer 就是运行在 OpenCode 上的"插件"，为你提供全套网文创作能力。

**安装 OpenCode**：访问 https://opencode.ai 下载对应你操作系统的版本，安装后打开即可。

### 1.2 你需要什么

| 软件 | 最低版本 | 检查方法 |
|------|---------|---------|
| Node.js | 22+ | 终端输入 `node --version` |
| Python | 3.10+ | 终端输入 `python --version` |
| OpenCode | 最新版 | 确认能启动 OpenCode |

> 安装器会自动检测 Node.js 和 Python，缺失时给出安装指引。Git 由安装器自动配置。

### 1.3 安装 Python

**Windows 用户**（没有 Python 或版本低于 3.10）：

1. 访问 https://www.python.org/downloads/
2. 下载最新安装包（如 Python 3.12.x）
3. **安装时务必勾选 "Add Python to PATH"**（页面底部复选框）
4. 安装完成后，打开**新的** PowerShell 窗口，输入：
   ```
   python --version
   ```
   应显示 `Python 3.12.x` 或更高

**Mac 用户**：
```bash
brew install python@3.12
```

**Linux 用户**：
```bash
sudo apt install python3 python3-pip git  # Ubuntu/Debian
```

### 1.4 安装 Git

**Windows**：下载 https://git-scm.com/download/win 安装，一路默认选项即可。

**Mac**：
```bash
brew install git
```

---

## 2. 安装

### 2.1 创建工作目录

打开 OpenCode，创建一个工作目录（所有书项目将存在这里）：

```
File → Open Folder → 新建文件夹 → 命名（如 D:\novels 或 ~/novels）
```

### 2.2 一键安装

在 OpenCode 的终端中运行：

```bash
npx @cszx/webnovel-writer-opencode init
```

内置离线包（约 2 MB），几秒内完成。安装器自动检测 Python 并安装依赖。

```text
┌──────────────────────────────────────────────────────────┐
│ Webnovel Writer OpenCode Edition — 安装          │
├──────────────────────────────────────────────────────────┤
│ 目录：D:\novels                                          │
│                                                          │
│ 将在此目录部署 .opencode/ 写作工具链                     │
│ 按 Enter 继续，输入 q 退出                               │
└──────────────────────────────────────────────────────────┘

  [1/2] 部署 .opencode/ 写作工具链
  ✓ 已解压 499 个文件
  ✓ [1/2] 已部署（离线包）

  [2/2] 安装 Python 依赖
  ✓ Python 依赖安装完成
  ✓ [2/2] Python 依赖安装完成
```

### 2.3 配置 API Key

安装后即可开始写作。如需 Embedding/Rerank 功能，复制 `.env.example` 为 `.env` 并填入 API Key：

```
EMBED_API_KEY=你的API_Key填在这里
```

> 💡 去[硅基流动](https://siliconflow.cn)或[ModelScope](https://modelscope.cn)注册获取 Key。Rerank Key 可选。

## 3. 初始化你的第一本书

### 3.1 启动初始化

在 OpenCode 的对话框中输入：

```
/webnovel-init
```

AI 会和你进行多轮对话，逐步收集创作信息。整个过程大约 5-10 分钟。

> 💡 **书项目会创建在哪？** 初始化时 AI 会确认书名和位置。书名会自动做安全化处理（去除特殊字符、空格转 `-`），在**工作区目录下**创建一个以书名命名的子文件夹。比如书名是"凡人资本论"，项目就在 `D:\novels\凡人资本论\`。

### 3.2 你需要回答的问题

AI 会分 7 步和你交互。以下是每一步会问什么，以及你需要提前想好的内容：

**Step 1：预检** — AI 确认环境 OK，告诉你接下来会收集哪些信息。

**Step 2：故事核与商业定位** — 需要准备的：
- 书名（可以先给工作名，后面再改）
- 题材（支持复合题材，如 "都市异能+规则怪谈"）
- 目标字数或章节数（新手建议 50-100 章，约 20-40 万字）
- 一句话故事（用一句话概括整本书）
- 目标读者/平台

**Step 3：角色骨架** — 需要准备的：
- 主角姓名 + 核心欲望 + 性格缺陷
- 感情线配置（无女主/单女主/多女主）
- 反派分层（至少想一个小反派和一个大反派）

**Step 4：金手指** — 需要准备的：
- 金手指类型（系统流/重生/签到/传承/无金手指）
- 金手指的代价或限制

**Step 5：世界观与力量体系** — 需要准备的：
- 世界规模（一座城/一个大陆/多个世界）
- 力量体系类型（修仙境界/异能等级/科技层级 等）
- 主要势力格局

**Step 6：创意约束** — AI 会帮你生成 2-3 套创意方案，你选一套。

**Step 7：确认** — AI 会复述你的完整设定，确认无误后生成项目文件。

> **💡 新手提示**：如果某个问题你不知道怎么回答，告诉 AI "我不确定"或"给点建议"，AI 会帮你提供候选方案。

### 3.3 初始化产出了什么

书项目作为工作区的一个子文件夹创建：

```text
工作区/                             ← D:\novels 或 ~/novels
├── .opencode/                     ← 插件代码
├── .env
└── 凡人资本论/                     ← 你的书项目！
    ├── .webnovel/
    │   ├── state.json              ← 项目状态（核心，不要手动改）
    │   └── idea_bank.json          ← 创意约束包
    ├── .story-system/
    │   └── MASTER_SETTING.json     ← 全书主设定合同
    ├── 设定集/
    │   ├── 世界观.md               ← 世界规则、势力、地点
    │   ├── 力量体系.md             ← 境界划分、技能规则
    │   ├── 主角卡.md               ← 主角详细信息
    │   └── 反派设计.md             ← 反派层级设计
    ├── 大纲/
    │   └── 总纲.md                 ← 全书蓝图
    ├── 正文/                        ← 章节目录（目前为空）
    └── 审查报告/                    ← 审查输出目录
```

> ⚠️ **非常重要**：之后所有的写作、规划、审查操作，OpenCode 的当前目录必须是**书项目目录**（`D:\novels\凡人资本论\`），不是工作区目录（`D:\novels`）。系统会自动识别，但如果你发现命令报错"找不到 project_root"，在 OpenCode 中用 `File → Open Folder` 切到书项目目录即可。

---

## 4. 大纲规划

初始化完成后，需要把"总纲"细化为可执行的章纲。确保 OpenCode 当前在**书项目目录**下。

### 4.1 规划第一卷

在 OpenCode 中输入：

```
/webnovel-plan 1
```

"1" 表示规划第 1 卷。AI 会产出三份文件：

```text
大纲/
├── 第1卷-节拍表.md       ← 卷级节奏蓝图
├── 第1卷-时间线.md       ← 时间锚点与倒计时
└── 第1卷-详细大纲.md     ← 每章的详细大纲
```

### 4.2 章纲结构说明

每章的纲包含以下关键字段：

| 字段 | 说明 | 示例 |
|------|------|------|
| 目标 | 本章主角要完成什么 | "搞清楚借据条款的荒谬" |
| 阻力 | 阻碍是什么 | "杂役不能随意离开宗门" |
| 代价 | 做不到会失去什么 | "暴露自己懂账" |
| 时间锚点 | 发生在什么时候 | "D-Day 清晨" |
| 爽点 | 读者看到这里会爽 | "展示复利算法碾压高利贷" |
| 章末钩子 | 吸引读下一章 | "谁改了借据？" |

### 4.3 规划后续卷

第 1 卷写完后，再规划第 2 卷：

```
/webnovel-plan 2
```

---

## 5. 开始写作

### 5.1 写单章

在 OpenCode 中输入：

```
/webnovel-write 1
```

这会生成 `正文/第0001章-章标题.md`。写入流程如下：

```text
上下文收集 → 起草 (Context Agent) → 审查 (Reviewer Agent)
→ 数据提取 (Data Agent) → 保存 → 提交
```

每章大约需要 2-3 分钟。写完后系统会自动保存、提取关键信息、更新角色状态。

**写下一章**：直接输入 `/webnovel-write 2`，系统会自动加载上一章的摘要和当前状态。

### 5.2 批量写多章

```
/webnovel-write-batch 1-5
```

一次写 5 章。系统会自动维护章节间的连贯性。

### 5.3 查看写作进度

在 OpenCode 中问 AI 即可：

> "查看我的项目进度"

或者通过 `/webnovel-query` 查询状态：

```
/webnovel-query 项目进度如何？还有多少伏笔未回收？
```

你也可以用 `/webnovel-query` 直接问 AI 任何关于项目的问题。

---

## 6. 章节审查

写完后检查质量：

```
/webnovel-review 1
```

审查包含 6 个维度：

| 维度 | 检查什么 |
|------|---------|
| 设定一致性 | 角色能力是否与当前境界匹配 |
| 时间线 | 与上章是否衔接，倒计时是否正确 |
| 叙事连贯 | 场景转换、情绪弧是否连续 |
| 角色一致性 | 对话风格、行为动机是否一致 |
| 逻辑 | 因果关系、战斗结果是否合理 |
| AI 味 | 是否有"眸中闪过""缓缓"等高频模板 |

审查结果会自动保存到 `审查报告/` 目录，并写入 `index.db`。

### 6.1 审查报告怎么看

打开 `审查报告/第0001章审查报告.md`：

```markdown
# 第1章审查报告

## 总览
- 问题数：3
- 阻断数：1          ← 有 1 个必须修复的问题
- 结论：需修复后重审

## 阻断问题
1. **主角使用了已失去的能力**    ← 这是必须改的
   - 严重级别：critical
   - 位置：第12段
   - 证据：第3章能力已废除 vs 本章仍在用
```

- **阻断问题**：必须修，修完重新审查
- **其他问题**：建议修，不修也能继续

---

## 7. 导出与发布

### 7.1 导出

```
/webnovel-export
```

支持的格式：

| 格式 | 用途 |
|------|------|
| TXT | 纯文本，上传到大部分小说平台 |
| Markdown | 带排版，本地阅读 |
| EPUB | 电子书，Kindle/手机阅读 |
| HTML | 网页格式，可自建阅读站 |
| DOCX | Word 文档，便于编辑排版 |
| PDF | 打印/分发 |

导出文件默认保存在书项目目录下。

### 7.2 发布到小说平台

目前支持番茄小说：

```
/webnovel-publish
```

首次使用需要扫码登录，之后全自动。发布前确保：
- 章节已审查通过（无阻断问题）
- 章节内容符合平台规则

---

## 8. 更新插件

当有新版本时：

1. 在 OpenCode 中 `File → Open Folder` 切到**工作区目录**（`D:\novels` 或 `~/novels`）
2. 运行：

```bash
npx @cszx/webnovel-writer-opencode init
```

3. 选择 `[1] 安装 / 更新`

或者一句命令搞定：

```bash
npx @cszx/webnovel-writer-opencode update
```

---

## 9. 进阶玩法

### 9.1 自定义文风

编辑书项目下的 `.story-system/MASTER_SETTING.json`：

```json
{
  "master_constraints": {
    "core_tone": "冷峻克制",        // 整体调性
    "pacing_strategy": "慢热推进"    // 节奏策略
  }
}
```

更多文风定制方式见 [GitHub Issue #12 的详细回复](https://github.com/lujih/webnovel-writer-opencode/issues/12)。

### 9.2 使用 Dashboard

如果安装时选了 Dashboard 模块，可以启动 Web 面板：

```
/webnovel-dashboard
```

Dashboard 提供：
- 项目进度总览（字数、章数、完成度）
- 实体关系图谱（角色之间的关系网）
- 章节内容直接预览
- 追读力数据

### 9.3 扩展写作知识库

在 `.opencode/references/csv/` 目录下，可以编辑 CSV 表来添加你自己的写作技法：

编辑 `写作技法.csv`，添加一行：

```csv
WT-099,write,文风,知识补充,"冷峻文风","全部","冷峻文风：多用短句，避免心理描写",
"每段不超过3句；删除所有'感到''觉得'类心理动词；情感通过动作暗示"
```

添加后无需重启，下次写作自动生效。

### 9.4 从已有项目迁移

如果你已有 markdown 格式的正文，可以：

1. 按标准目录结构组织：
   ```
   正文/
   ├── 第0001章-第一章标题.md
   ├── 第0002章-第二章标题.md
   └── ...
   ```
2. 手动补齐 `设定集/` 下的文件
3. 运行索引重建：
   ```bash
   # 先 cd 到书项目目录
   cd ./你的书名
   # 然后运行索引重建
   python ../.opencode/scripts/webnovel.py index process-chapter --chapter 1
   ```

---

## 10. 常见问题 (FAQ)

### Q: 我是纯新手，OpenCode 是什么？怎么用？

OpenCode 是一个 AI 编程助手软件。把它想象成一个"超级对话框"——你在里面打字，AI 帮你执行任务。安装 Webnovel Writer 后，输入 `/webnovel-init` 这样的"斜杠命令"，AI 就会按预设流程引导你完成创作。

**基本操作**：
1. 打开 OpenCode → `File → Open Folder` → 选择工作区目录
2. 在底部对话框输入命令（如 `/webnovel-init`），回车
3. AI 回应后继续对话即可

### Q: Windows 提示 "python 不是内部或外部命令"

说明 Python 没装好或没加到 PATH。解决方法：
1. 重新运行 Python 安装包
2. **务必勾选 "Add Python to PATH"**（安装界面底部复选框）
3. 安装完后**重新打开** PowerShell（已打开的窗口不会生效）
4. 再试 `python --version`

### Q: 安装时卡住不动怎么办？

**现象**：运行 `npx @cszx/webnovel-writer-opencode init` 后，在 "安装依赖" 步骤长时间没有反应。

**原因**：pip 在下载大文件（特别是 Chromium ~150MB），国内网络访问 pypi.org 可能很慢。

**解决**：
1. 如果超过 5 分钟没反应，按 `Ctrl+C` 取消
2. 重新安装，在功能选择时按 `N` 选"仅核心"（不装浏览器和面板）
3. 或者使用国内 pip 镜像重试

### Q: 提示 "无法连接到 GitHub"

**解决**：（国内网络）使用镜像重试：
npx @cszx/webnovel-writer-opencode init
```

### Q: 写作时上下文丢失（写着写着就忘了前面的设定）

这是系统已处理的问题。每次写新章时，系统会自动：
- 加载故事合同（MASTER_SETTING + 章级合同）
- 加载最近章节摘要
- 检查活跃伏笔和角色状态

如果仍然感觉"失忆"，可以在 OpenCode 中告诉 AI：

> "刷新一下项目状态，把所有角色的最新信息同步一遍"

### Q: 如何修改已生成的设定？

直接编辑 `设定集/` 下的 `.md` 文件即可。下次写作会读取新的设定。

### Q: 可以不用 OpenCode 直接命令行运行吗？

可以，但会失去交互式体验：

```bash
# 初始化
python .opencode/scripts/webnovel.py init ./书名 "书名" "题材" --protagonist-name "主角名"

# 写大纲/写章/审查需要用 skill 流程（交互式），建议至少保留 OpenCode
```

### Q: 想要的功能缺失怎么办？

- 去 https://github.com/lujih/webnovel-writer-opencode/issues 提交需求
- 或者自己修改源码（`.opencode/` 下所有代码都是开放的）

### Q: 运行 `/webnovel-plan` 时提示 "找不到 project_root"

这说明 OpenCode 当前目录不在书项目目录下。解决方法：

1. 在 OpenCode 中点击 `File → Open Folder`
2. 选择书项目目录（如 `D:\novels\凡人资本论\`）
3. 重新运行命令

### Q: 工作区目录 和 书项目目录 有什么区别？

- **工作区目录**（如 `D:\novels`）：包含 `.opencode/`（插件代码）是你安装插件的地方
- **书项目目录**（如 `D:\novels\凡人资本论\`）：包含 `.webnovel/`、`设定集/`、`正文/`等，是你写书的地方

写作时 OpenCode 需要在**书项目目录**下（不是工作区目录）。

### Q: 如何完全卸载？

```
npx @cszx/webnovel-writer-opencode uninstall
```

这会删除 `.opencode/`、`.venv/` 和所有依赖。你的书项目文件不会受影响（在独立目录下）。

### Q: 我安装时选了"仅核心"，现在想用导出/发布/Dashboard 怎么办？

运行 `npx @cszx/webnovel-writer-opencode update` 更新到最新版。

### Q: 写好的章节在哪里？怎么阅读？

章节文件在 `正文/` 目录下，是 Markdown 格式（`.md` 文件）。你可以：
- 用任意文本编辑器（记事本、VS Code）直接打开阅读
- 用 `/webnovel-export` 导出为 EPUB 放在手机上读
- 导出为 TXT 上传到小说平台

### Q: 如何备份项目？

复制整个书项目文件夹即可。最小需要备份的是：

```bash
# 关键数据（必须）
书目录/.webnovel/state.json
书目录/.webnovel/index.db
书目录/.story-system/

# 内容（必须）
书目录/大纲/
书目录/正文/
书目录/设定集/

# 可选
书目录/.webnovel/summaries/
```

---

> **遇到其他问题？** 到 https://github.com/lujih/webnovel-writer-opencode/issues 搜索或提交新 issue。
