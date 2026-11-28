---
description: 最近 20 章里「改错代价大」的状态变更（只读：高危字段 + 违背 canon lock）
---

以下是自动审计结果——最近 20 章中风险等级为 high 的状态变更（境界/生死等），
以及所有与作者钉死设定（canon lock）冲突的记录：

!`python .opencode/scripts/webnovel.py changes audit --limit 20`

请用中文回答：哪几章动了关键设定、有没有违背锁定值的记录。

若某项看起来异常（同一字段在相邻章节反复变更、或某字段凭空消失），指出可能原因
（投影漏项 / 章纲与正文不符 / lock 本身需要更新），并给出该跑哪条只读命令进一步确认。

**不要**自行修改 `.webnovel/state.json` 或 `.story-system/canon_locks.json`：
前者是投影产物（应通过重新提交或 `projections retry` 修复），后者是作者的硬约束。
