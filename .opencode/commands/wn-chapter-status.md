---
description: 查第 N 章的工作流阶段状态（只读）
---

以下是第 $1 章的工作流阶段：

!`python .opencode/scripts/webnovel.py workflow status --chapter $1`

报告当前阶段（PLANNING / DRAFTING / REVIEWING / REVISING / COMMITTED），
以及该章已有的检查点。

只做只读汇报。若阶段明显落后（例如已 COMMITTED 但用户以为没写完），
指出来但不要自行改动阶段。