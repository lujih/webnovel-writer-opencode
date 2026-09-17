# 文档中心

`docs/` 目录按功能分区整理，方便查阅。

## 目录索引

### 架构

- [`architecture/overview.md`](./architecture/overview.md)：系统架构、Agent 分工、Story System 设计
- [`architecture/current-system-diagnosis.md`](./architecture/current-system-diagnosis.md)：当前系统状态诊断
- [architecture/system-architecture.md](./architecture/system-architecture.md)：详细系统架构与模块说明
- [architecture/scripts-backend-flow.md](./architecture/scripts-backend-flow.md)：`.opencode/scripts` 后端脚本流程梳理（双入口、数据流、模块清单）
- [architecture/frontend-backend-collab-review.md](./architecture/frontend-backend-collab-review.md)：前后端协作审查报告（写作/审查/Dashboard/初始化/SSOT 逐流程分析）
- [architecture/writing-workflow-analysis.md](./architecture/writing-workflow-analysis.md)：写作工作流详细分析报告（4 阶段 + 6 Agent + 数据流）
- [architecture/workflow-review-report.md](./architecture/workflow-review-report.md)：工作流审查报告（6 严重 + 23 中等 + 15 低）

### 使用指南

- [`guides/commands.md`](./guides/commands.md)：Skill 命令与 CLI 子命令速查
- [`guides/rag-and-config.md`](./guides/rag-and-config.md)：RAG 检索链路、环境变量与配置
- [`guides/genres.md`](./guides/genres.md)：37 个题材模板与复合题材规则

### 运维

- [operations/architecture-review-2026-05-25.md](./operations/architecture-review-2026-05-25.md)：2026-05 分层架构审查报告与修复计划
- [operations/auto-fix-workflow.md](./operations/auto-fix-workflow.md)：自动修复工作流
- [operations/2026-09-16-upstream-sync-merge.md](./operations/2026-09-16-upstream-sync-merge.md)：2026-09 上游同步合并记录（#130/#131/#125 三笔 fix + P0/P1/P2 五项审查修复）

### 记忆系统

- [`memory/long-term-memory-architecture-v2.md`](./memory/long-term-memory-architecture-v2.md)：长期记忆架构说明

### 研究与外部方案

- [`research/long-term-memory-research-report.md`](./research/long-term-memory-research-report.md)：长期记忆论文与开源方案调研
- [`research/storyteller-paper-summary.md`](./research/storyteller-paper-summary.md)：STORYTELLER 论文总结

### Specs

- [`superpowers/README.md`](./superpowers/README.md)：架构 spec 与设计文档导航
- [superpowers/specs/2026-05-25-inkos-inspired-improvements-design.md](./superpowers/specs/2026-05-25-inkos-inspired-improvements-design.md)：inkOS 借鉴改进设计文档
- [superpowers/specs/2026-06-06-dashboard-development-roadmap.md](./superpowers/specs/2026-06-06-dashboard-development-roadmap.md)：看板发展规划（Phase 4-11，含实施状态）

### Plans

- [superpowers/plans/2026-05-25-inkos-inspired-improvements-plan.md](./superpowers/plans/2026-05-25-inkos-inspired-improvements-plan.md)：inkOS 借鉴改进实现计划
- [superpowers/plans/2026-05-25-dashboard-evolution-plan.md](./superpowers/plans/2026-05-25-dashboard-evolution-plan.md)：看板演进实现计划（Phase 1-3，已完成）
- [superpowers/plans/2026-06-07-original-project-sync-plan.md](./superpowers/plans/2026-06-07-original-project-sync-plan.md)：原项目同步计划（P0 已完成，P1/P3 待执行）
- [superpowers/plans/2026-06-07-p1-p3-detailed-plan.md](./superpowers/plans/2026-06-07-p1-p3-detailed-plan.md)：P1/P3 详细实施计划
- [superpowers/plans/2026-06-08-project-phase-sync-plan.md](./superpowers/plans/2026-06-08-project-phase-sync-plan.md)：project_phase.py 同步计划

## 分类原则

- `architecture/`：系统结构与技术架构
- `guides/`：使用者需要查阅的命令、配置、题材说明
- `memory/`：长期记忆架构说明
- `research/`：论文总结与外部方案调研
- `superpowers/`：架构 spec 与设计文档

## 推荐阅读顺序

1. 先看 [`../README.md`](../README.md) 了解安装与基本使用
2. 再看 [`architecture/overview.md`](./architecture/overview.md) 了解整体架构
3. 需要配置检索时看 [`guides/rag-and-config.md`](./guides/rag-and-config.md)
4. 需要使用命令时看 [`guides/commands.md`](./guides/commands.md)
5. 系统运行健康状态用 `preflight` 和 `status` 命令诊断，详见 [commands.md](./guides/commands.md) 运维子命令
