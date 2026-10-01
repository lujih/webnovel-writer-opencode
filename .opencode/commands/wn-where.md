---
description: 查看当前绑定的是哪本书、项目根在哪
---

以下是当前项目定位结果：

!`python .opencode/scripts/webnovel.py where`

向用户报告：当前绑定的项目名称与绝对路径。

若这里显示的项目与用户以为在写的那本**不一致**，务必明确指出并停下来
确认——写路径命令会真的改写 state.json，改错书就是静默数据损坏。