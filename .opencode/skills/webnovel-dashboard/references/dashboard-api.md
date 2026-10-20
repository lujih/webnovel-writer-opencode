# Dashboard API 参考

服务默认监听 `http://127.0.0.1:8765`。实际路由以 `.opencode/dashboard/app.py` 为准
（56 个端点），本文只列**面板自己在用**的那些。

## 安全边界（读这两条就够）

所有写方法（POST/PUT/PATCH/DELETE）都过两道守卫，缺一不可绕过：

| 守卫 | 拦什么 | 失败响应 |
|------|--------|----------|
| Host 守卫 | 非本机 Host（DNS rebinding 的**读取**面） | 400 |
| CSRF 守卫 | 带 `Origin` 且不在允许源内的写请求 | 403 |

允许源**跟随实际监听地址**：`create_app(project_root, port=..., host=...)`。
`port` 与 `host` **都必须传**，任一漏传都会造成同一个静默故障——「读得到、
写全废」（GET 一切正常，只有编辑动作 403）：

| 漏传 | 场景 | 后果 |
|------|------|------|
| `port` | 用户按端口占用提示改用 `--port 8766` | Origin 端口对不上 |
| `host` | `--host 0.0.0.0` 后从 `192.168.x.x` 访问 | Origin 主机名对不上（而 Host 守卫放行 IP 字面量，所以读是通的） |

通配绑定（`0.0.0.0` / `::`）无法预先枚举本机网卡，此时放行「**任意 IP 字面量**
+ 已知端口」；**域名永远要求精确匹配**——DNS rebinding 的攻击前提就是域名，
IP 字面量由浏览器地址栏决定，攻击者无法指定。绑定具体 IP（如
`--host 192.168.1.5`）时只放行该 IP，不放宽。

不带 `Origin` 的请求（curl / 本地脚本）放行——能发起本地请求的进程本就有完整
文件系统权限，守卫防的不是它。

## 写入 API

| Endpoint | Method | 功能 |
|----------|--------|------|
| `/api/style/master-setting` | PUT | 更新 `master_constraints`（`override_policy.locked` 里的字段拒绝改，403） |
| `/api/style/anti-patterns` | POST | 追加反模式（重复文本 409） |
| `/api/style/anti-patterns` | DELETE | 按文本删除（不存在 404） |
| `/api/style/prompts` | POST | 创建提示词文件（文件名含路径分隔符/Windows 保留字符 → 400） |
| `/api/style/prompts/{filename}` | PUT | 更新提示词内容 |
| `/api/style/prompts/{filename}` | DELETE | 删除提示词文件 |
| `/api/files/write` | PUT | 保存单个文件，**仅限 正文/大纲/设定集 三个目录**，写入前留 `.bak` |
| `/api/actions/{action}` | POST | 运维操作：`ssot-verify` / `ssot-rebuild` / `entity-clean`（白名单之外 403，1 秒限流） |
| `/api/batch/{action}` | POST | 批量 `write` / `delete`；`chapters` 只接受数字/逗号/连字符，其余 400 |

写入细节：

- `atomic_write_json(path, data, use_lock=...)` 原子写 + `.bak` 备份。
  ⚠️ **若调用方已持有同一把 `.lock`（如 `_locked_anti_patterns()`），必须传
  `use_lock=False`**。filelock 不可重入，内层再 acquire 会等锁超时并抛
  `AtomicWriteError`（对外的表现是 500）。2026-10 的「反模式写入 100% 失败」
  就是这个自锁。
- 批量操作走 `asyncio.create_subprocess_exec`，不阻塞事件循环。
- 除 `.story-system/` 下这几个文风文件外，SSOT（`.webnovel/state.json`）与
  其余 `.story-system/` 内容**不可经面板写入**。

## 只读 API（面板自用）

| Endpoint | 功能 |
|----------|------|
| `/api/project/info` | `state.json` 全文 |
| `/api/story-runtime/health` | 运行时健康度（最近提交状态、fallback 源） |
| `/api/stats/chapter-trend` | 每章聚合（字数/审查分/钩子/strand/卷），支持 `limit`+`offset` |
| `/api/entities` | 实体列表，可按 `type` 过滤 |
| `/api/entities/{id}/timeline` | 实体状态变化 + 出场记录 |
| `/api/relationships` | 关系边 |
| `/api/consistency/anomalies` | 实体状态异常检测（值回退、无变化） |
| `/api/review/analytics` | 审查维度分析：`dimension_averages` / `dimension_trends` / `severity_totals` / `weakest_dimensions` |
| `/api/commits` | 最近 N 个 commit 与五路 projection 状态 |
| `/api/contracts/summary` | 合同树概览（MASTER/volume/chapter/review/commit 计数） |
| `/api/env-status` · `/api/env-status/probe` | RAG/Rerank 配置与主动探测 |
| `/api/context/health/{chapter}` | 上下文健康度；**该章无快照时返回 404**（前端应显示引导而非报错） |
| `/api/context/history` | 最近 N 章上下文健康趋势 |
| `/api/foreshadowing/reminders` | 即将到期（`threshold` 内）与无期限的活跃伏笔 |
| `/api/debts/summary` | 追读债总览：统计 + 明细 + 按类型/状态分桶 |
| `/api/debts/{id}` | 单笔债务 + `debt_events` 时间线 |
| `/api/diagnostics/summary` | 运维诊断聚合：RAG 延迟 P50/P95、空命中率、工具失败率、最慢检索、最近失败 |
| `/api/alerts` | 质量预警（分数下滑/债超期/角色久未出场/strand 单调） |
| `/api/workflow/status` | 章节阶段进度 |
| `/api/files/tree` · `/api/files/read` | 三目录只读浏览（越界 403） |
| `/api/events` | SSE：`.webnovel/` 关键文件与 `.story-system/*.json` 变更推送 |

### 追读债的两个关键语义

1. **`is_overdue` 按当前章推算，不读 `status` 字段。** `status` 只在
   `accrue_interest` 被调用时推进；作者若很久没跑章节提交，一堆早已过期的债
   仍标 `active`。两个字段都返回，值不一致本身就是「该还了但系统没提醒」的信号。
2. **`totals` 是全表口径，`items` 受 `limit` 限制。** 两者分开查：
   早先实现用同一条带 `LIMIT` 的查询既算统计又出明细，于是 `limit=10` 时
   「总余额」会从 60 变成 10——总额随分页参数跳动。响应里的 `truncated`
   告诉你明细是否被截断。
3. **缺表返回空而非 500。** 旧库可能没有 `chase_debt`，`_fetchall_safe` 会
   把 `no such table` 降级成空列表。

### 运维诊断的两张数据源

`rag_query_log` 与 `tool_call_stats` **一直在被写入**（`rag_adapter` 每次检索、
`context_manager.build` 每次组装），但此前只有原始日志转储端点，信息密度太低。
`/api/diagnostics/summary` 把它们聚合成可读指标：延迟分位数、空命中（检索无结果）
比例、工具失败率与最近失败记录。

`total` 是**全表口径**，最慢检索/最近失败各自受 `limit` 限制——与追读债同款约定。

### 审查维度数量是数据驱动的

`/api/review/analytics` 的 `dimension_averages` 有几项取决于该书实际跑过的审查
维度（可能只有 plot/pacing/character 三项）。前端**不要**写死「8 维度」。

## 配置

`.webnovel/dashboard_config.json` 可自定义关键 Section 列表：

```json
{
  "critical_sections": ["core", "scene", "story_contract", "user_prompts", "memory"]
}
```

不配置时默认 `{"core", "scene", "story_contract", "user_prompts"}`。支持运行时修改（无需重启）。

## 前端

```bash
cd .opencode/dashboard/frontend
npm ci          # 必须用 ci：lock 与 package.json 脱节会当场报错
npm run check   # lint + test + build，与 CI 一致
npm run dev     # 开发服务器，/api 代理到 8765
```

测试分两个 project（`vitest.config.js`）：

| project | 环境 | 范围 |
|---------|------|------|
| `unit` | node | `src/lib/**`、`src/api.js` 的纯函数——格式化、分箱、伏笔聚合、错误语义 |
| `component` | jsdom | `src/components/**`、`src/pages/**` 的渲染与状态 |

组件测试覆盖的是**状态与渲染的对应关系**：loading / 空态 / 错误态 / 竞态，
以及"给了什么数据该显示什么"。它**不**验证 ECharts 画布像素——那需要真浏览器，
用 Playwright 截图 + 统计 canvas 非空像素比例来验证（雷达图空白事故就是这么查出来的）。

`src/test-setup.js` 提供 jsdom 缺的三个 API（canvas 2D 上下文桩、ResizeObserver、
matchMedia）。canvas 桩返回最小可用的上下文而不是 `null`——返回 null 会让 ECharts
在卸载时炸出 `Cannot read properties of null (reading 'clearRect')`，把被测页面
本身的问题淹没在无关报错里。

`src/test-utils.jsx` 的 `renderPage(Page, context)` 把页面放在带 `Outlet context`
的路由里——所有页面都通过 `useOutletContext()` 取数据，直接 render 会崩在解构上。
