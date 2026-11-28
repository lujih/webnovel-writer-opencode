---
description: 最近的投影运行记录与失败项（只读）
---

以下是投影运行日志（新 → 旧）。每条记录标明该章的 writer 执行结果：

!`python .opencode/scripts/webnovel.py run-log list --limit 10`

请用中文回答：有没有失败的 writer、涉及哪几章。

若有失败，按下面的次序给建议，**不要**越级：

1. 先跑日志里打印的 `projections retry --chapter N` —— 只重跑该章投影，幂等；
2. 仍失败再查 `ssot verify`（事件日志与 state.json 是否有漂移）；
3. 最后才考虑 `ssot rebuild`（重建全部投影，代价最大）。

`ssot rebuild` 会重写全部投影，必须由用户明确批准后才能执行。
