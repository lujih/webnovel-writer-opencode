#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分层记忆的两个结构化出口：`meta.memory_stats` 与 `reader_signal.memory_conflicts`。

背景：`MemoryOrchestrator.build_memory_pack()` 早就算好了三层（working /
episodic / semantic）的条目与统计（`stats` 里有 `layered_total_injected` /
`filtered` / `conflicts`），但 `context_manager._build_pack()` 只把整包塞进
`long_term_memory` 段——于是：

1. **面板看不出分层占比**。`stats` 要解析 JSON 才能读到，而「三层各注入几条、
   过滤掉多少」正是判断记忆预算配比是否合理的唯一依据。
2. **记忆冲突被埋没**。`warnings` 里的 `memory_conflict`（同一事实存在两条
   矛盾记录）落在长期记忆段深处，模型容易忽略；它属于「写之前必须知道」，
   应当进 `reader_signal` 警戒段。

本文件钉住四条：
1. 有 pack 时 `meta.memory_stats` 存在且是**真值**（不是空 dict）；
2. 有 `memory_conflict` 时它出现在 `reader_signal`；
3. 没有冲突时**不伪造**空列表（「没有键」≠「键为空数组」，下游判一次就够）；
4. orchestrator 关闭时不写 `memory_stats`——凭空补空 dict 会让下游误判
   「记忆系统跑过但没数据」。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.context_manager import ContextManager  # noqa: E402


def _pack(stats: dict | None = None, warnings: list | None = None) -> dict:
    return {
        "working_memory": [{"source": "outline", "content": "章纲"}],
        "episodic_memory": [{"summary": "上一段剧情"}],
        "semantic_memory": [{"subject": "萧炎", "predicate": "realm", "object": "斗师"}],
        "long_term_facts": [],
        "active_constraints": [],
        "recent_changes": [],
        "warnings": warnings or [],
        "stats": stats or {},
    }


@pytest.fixture
def manager(tmp_path) -> ContextManager:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    config = DataModulesConfig.from_project_root(str(tmp_path))
    config.context_use_memory_orchestrator = True
    return ContextManager(config)


def _build(manager: ContextManager, monkeypatch, pack: dict | None) -> dict:
    from data_modules.memory import orchestrator as orchestrator_module

    # 大纲与本文件无关：它的加载器要求磁盘上有 `大纲/` 结构，而这里只验证
    # 记忆分层的两个出口。直接打桩，让测试的失败原因保持在关注点上。
    monkeypatch.setattr(ContextManager, "_load_outline",
                        lambda self, chapter: "第1章 大纲（测试桩）")

    if pack is None:
        monkeypatch.setattr(orchestrator_module.MemoryOrchestrator, "build_memory_pack",
                            lambda self, chapter, task_type="write": {})
    else:
        monkeypatch.setattr(orchestrator_module.MemoryOrchestrator, "build_memory_pack",
                            lambda self, chapter, task_type="write": dict(pack))
    return manager._build_pack(1)


class TestMemoryStatsExposed:
    def test_stats_goes_into_meta(self, manager, monkeypatch):
        pack = _pack(stats={
            "total": 12, "layered_total_injected": 3, "filtered": 9, "conflicts": 0,
        })
        payload = _build(manager, monkeypatch, pack)
        assert payload["meta"]["memory_stats"]["layered_total_injected"] == 3
        assert payload["meta"]["memory_stats"]["filtered"] == 9

    def test_no_stats_means_no_key(self, manager, monkeypatch):
        payload = _build(manager, monkeypatch, _pack(stats={}))
        assert "memory_stats" not in payload["meta"]

    def test_orchestrator_pack_still_reachable(self, manager, monkeypatch):
        """分层数据本身仍要在 long_term_memory 段（回归：别为了 meta 把段落删了）。"""
        payload = _build(manager, monkeypatch, _pack(stats={"total": 1}))
        assert payload["long_term_memory"]["episodic_memory"]
        assert payload["long_term_memory"]["semantic_memory"]

    def test_orchestrator_disabled_writes_nothing(self, tmp_path, monkeypatch):
        """"开关关着"与"跑了但没数据"必须可区分——前者不该有键。"""
        (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
        config = DataModulesConfig.from_project_root(str(tmp_path))
        config.context_use_memory_orchestrator = False
        payload = _build(ContextManager(config), monkeypatch, None)
        assert "memory_stats" not in payload["meta"]
        assert "memory_conflicts" not in payload.get("reader_signal", {})


class TestMemoryConflictsSurfaceInReaderSignal:
    def test_conflict_moves_into_reader_signal(self, manager, monkeypatch):
        pack = _pack(stats={"total": 2}, warnings=[
            {"type": "memory_conflict", "count": 2, "sample": ["a", "b"]},
        ])
        payload = _build(manager, monkeypatch, pack)
        conflicts = payload["reader_signal"]["memory_conflicts"]
        assert conflicts[0]["count"] == 2

    def test_other_warning_types_are_not_promoted(self, manager, monkeypatch):
        """只把 memory_conflict 提到警戒段；其余 warning 留在原处。"""
        pack = _pack(warnings=[{"type": "something_else", "count": 1}])
        payload = _build(manager, monkeypatch, pack)
        assert "memory_conflicts" not in payload.get("reader_signal", {})

    def test_empty_warnings_do_not_create_key(self, manager, monkeypatch):
        payload = _build(manager, monkeypatch, _pack())
        assert "memory_conflicts" not in payload.get("reader_signal", {})
