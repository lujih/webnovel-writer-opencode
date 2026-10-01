---
description: 项目健康总览（字数、章节进度、伏笔与追读债、投影一致性）
---

以下是当前绑定项目的状态报告：

!`python .opencode/scripts/webnovel.py status`

请用中文简要总结：当前写到第几章、累计字数、有无 blocking 级问题。

若报告显示投影与事件日志不一致（SSOT drift），**不要**自行执行
`ssot rebuild`——那会重写全部投影。先把差异报告给用户，等明确批准再动手。