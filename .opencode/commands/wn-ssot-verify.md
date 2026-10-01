---
description: SSOT 一致性校验——state.json 与事件日志是否漂移（只读）
---

以下是 SSOT 一致性校验结果：

!`python .opencode/scripts/webnovel.py ssot verify`

只做**只读**分析，不要自行修复。

- 若无漂移：说明 state.json 与 `.story-system/events/` 的回放结果一致。
- 若有漂移：列出漂移的字段与事件类型，指出可能的成因，并**建议**用户运行
  `/wn-rebuild` 或 `python .opencode/scripts/webnovel.py ssot rebuild`。
  **绝不要**自己执行 rebuild——它会按事件日志重写全部投影，用户没批准前
  不许碰。