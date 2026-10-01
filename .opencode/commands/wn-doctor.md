---
description: 项目健康诊断（文件/JSON/SQLite/投影/Python 五类检查，只读）
---

以下是项目诊断结果：

!`python .opencode/scripts/webnovel.py doctor --format text`

按严重度分类呈现：先列 blocking，再列 warning。

- **blocking**：必须修复才能继续写作。针对每一条给出具体修复命令，但
  **先不要执行**——先说明修复动作与影响，等用户确认。
- **warning**：列出即可，提示用户可稍后处理。

诊断本身是只读操作，不会修改任何文件。