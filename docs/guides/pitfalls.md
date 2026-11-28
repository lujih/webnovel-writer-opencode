# 这个项目反复踩的七类坑

> 二十八轮持续重构（v4.0.0 → v4.1.0）沉淀下来的模式清单。
> 每一类都**真实发生过**，而且都**不会让测试变红**——这才是它们值得写成文档的原因。

用法：改动前扫一眼，看这次改的东西是否落在某类坑的射程内。

---

## 一、声明 ≠ 实现

**最频繁的一类。** 项目里有七处属于此类的真实缺陷：

| 实例 | 声明在哪 | 实际状态 |
|---|---|---|
| `projections retry` | `write-guard.js` 的 `ALLOWED_MARKERS` | 命令**从未实现**，作者照着敲得到 `unrecognized arguments` |
| `relationship_context` | 索引层有 `get_entity_relationships()` | `_build_pack` **从不调用**，写章时看不到角色关系 |
| `memory_stats` | `build_memory_pack` 算好了 `stats` | 整包塞进 `long_term_memory`，没人解析 → 面板看不到 |
| `write_gates` | 三个阶段 + 设计文档齐全 | **生产代码从未调用**，是死代码 |
| `ambiguous_to_pending` | 实体消歧模块完整 | 零调用 |
| `--min-changes` | argparse 声明了 | `_run_changes` 不转发（因默认值恰好可用而未暴露） |
| `dist/` 的 git 例外 | `.gitignore` 写了 `!…/dist/**` | 例外路径是**废弃的旧布局**，永不命中 |

**识别信号**：某个东西"应该有作用"，但你从未观测到它的影响；或者某段代码只有测试调用、没有生产调用点。

**已布的防线**（七道）：
1. guard 白名单 ↔ CLI 子命令
2. 模块 import ↔ `requirements.txt`
3. 散文里提到的脚本路径 ↔ 磁盘
4. 文档里的命令 ↔ argparse
5. 新语法 ↔ CI 的 Python 版本
6. npm 打包范围 ↔ git 索引
7. git 忽略方向 ↔ 该进/不该进

**防线之外的部分**：「函数写了但没人调用」没有静态检查能抓——只能靠**端到端测试**（断言"这个能力通过公开入口真的生效"）和源码断言（`assert "_load_relationship_context(" in inspect.getsource(...)`）。

---

## 二、静默失败

不抛错、不报警，只是**没生效**。

| 实例 | 表现 |
|---|---|
| `except (ImportError, Exception)` | 等价于 `except Exception`，把磁盘满/权限不足当成"锁不可用"，再用一次写入掩盖真因 |
| `projection_log` 无入口 | 日志写得好好的，但作者看不到失败 |
| npm 包缺 `AGENTS.md` | 安装后模型照常调 skill，但读不到规则 |
| `dist` 被 ignore | CI 的 `git diff --quiet -- dist` 一直空转 |
| 本机缺 devDeps | `npm run test` 报 `'vitest' is not recognized`，而 CI 绿 |

**识别信号**：某事"配置了/写了/存在"，但行为上没有区别。

**防线**：端到端测试 > 单元测试。单测能证明函数正确，证明不了它被调用。

---

## 三、`None` / `0` / 缺键 三态混淆

| 场景 | `None` | `0` | 缺键 |
|---|---|---|---|
| CED 三口径 | 算不出（分母为 0 / 审查不可解析） | 真的没发现问题 | — |
| `memory_stats` | — | — | orchestrator 没开 |
| `review_parseable` | — | 输出不可解析 | — |
| `latest_value` | 空 delta 不该覆盖 | 合法值 | — |

**原则**：**「算不出」≠「没问题」≠「字段不存在」**。把三者合并成一个 `0` 或一个空 dict，会让下游做出错误判断——趋势图上会留下无法解释的尖峰，面板上会显示"记忆系统跑了但一条没注入"。

**已布的处理**：CED 用 `None`（SQLite NULL）；`memory_stats` 只在真有时写键；`latest_value` 遇空值不覆盖。

---

## 四、合成数据测不出的脏数据

这一类的四例**全部**由真书（58 章）暴露，没有一条能被 fixture 测出：

| 现象 | 真书里的样子 |
|---|---|
| 空 delta | `chen_sheng.realm` 的 `— → —`（old/new 全缺） |
| 跨章重复 | 第 020 与 024 章有**完全相同**的两条 status 变更 |
| 长叙述值 | `status` 是「受伤（右肩与上臂间能量刃切口…）」这种整段文字，不是枚举 |
| 空变更被误判 | `29 → 29` 被当成"值回退" |

**教训**：fixture 里的人写的是"理想数据"，真实世界里数据会脏。**每加一个分析类功能，都要拿真数据跑一次**。

---

## 五、版本 / 环境差异

**最惨的一次**：`tempfile.TemporaryDirectory(delete=)` 是 Python 3.14 的参数，而 CI 跑 3.11。本机全绿，CI 在**收集阶段**全灭（filelock 导入期探测是第一个触发者），**连红 12 次**都没人联想到版本差。

**防线**：`check_py311_compat.py` 双层检查（`ast.parse(feature_version=(3,11))` + PEP 701 f-string 同引号嵌套的 AST 检测），并把「全仓库无 3.11 不兼容语法」钉进 pytest——CI 的 pytest 步骤自动执行，不需要改 workflow。

**但语法层 ≠ 运行时**：依赖版本、路径分隔符、locale 行为仍未验证。**只有在 CI 上真跑才算数。**

---

## 六、本地验证链断裂

`node_modules` 只装了 dependencies，`npm run test` 直接报 vitest 不存在。而 CI 用 `npm ci` 装全，所以**CI 绿、本地跑不了**。

**识别信号**：「本地跑不了，所以不跑了」。

**处置**：`npm install` 补齐。更要紧的是**发现它是断的**——如果没人跑过前端测试，那 155 个用例等于不存在。

---

## 七、文档腐烂

文档比代码更容易腐烂：代码改了有人 CI，文档改了没人看。

| 曾经的错误说法 | 事实 |
|---|---|
| reviewer 检查 13 维 | 6 维（`reviewer.md:84,88,140` 明确排除 ai_flavor/pacing/毒点） |
| 有 `DebtTracker` 类、活跃债 > 2 触发 15% token 预算 | `grep -rn "class DebtTracker"` 零命中，机制不存在 |
| V2 不加载 `instructions` 键 | **确实会加载**（/docs/config#instructions） |
| `pytest.ini` 默认开覆盖率 | `addopts` 里没有任何 `--cov` |
| npm 包里有 `AGENTS.md` | 2.9.2-12 实测无此文件 |

**防线**：
- `test_docs_commands_exist.py`：文档里的 `webnovel.py <cmd>` 与 `.opencode/scripts/*.py` 路径必须真实存在（67 例）
- `test_readme_accuracy.py`：README 的计数必须等于磁盘实际
- `sync-agents-md --check`：`CLAUDE.md` 与 `AGENTS.md` 逐字节一致（CI 门禁）
- **历史计划文档排除在外**：`docs/superpowers/plans/` 是当时的快照，要求它与当前实现一致等于篡改档案

---

## 一条元教训

这七类坑有一个共同点：**它们都不会让任何测试变红，只在"提交 / 打包 / 安装 / 上线"那一刻才炸**。

所以真正的防线不是"多写测试"，而是**主动去想哪一步没有被覆盖**：

- 这个能力有测试，但**它被调用了吗**？
- 这个配置写了，但**它真的生效吗**？
- 这个字段算了，但**有人读吗**？
- 这段文档说的，**代码里还是这样吗**？
- 本机能跑，**CI 也能吗**？
