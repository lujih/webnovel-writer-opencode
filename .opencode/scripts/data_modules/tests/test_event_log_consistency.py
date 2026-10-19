#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`EventLogStore` 的一致性语义测试。

`EventLogStore` 是 SSOT 事件日志的**双写层**：每个事件同时落到

1. `.story-system/events/chapter_NNN.events.json`（人类可读、进 git）
2. `.webnovel/index.db` 的 `story_events` 表（dashboard / 查询用）

实测的两个缺陷：

**1. 双写不在同一事务里，失败后半写。**
`write_events()` 先 `write_json(...)` 再 `_write_sqlite_mirror(...)`。
后者抛异常时，JSON 已经落盘，而 SQLite 少条——两个"真相"分叉，
异常再往上冒，调用方（`chapter_commit_service.py:271`）只 `logger.warning`
就继续了。实测：2 个事件文件 vs SQLite 1 条。

**2. `health()` 永远返回 `ok: True`。**
它已经算出了 `event_files` 与 `sqlite_rows` 两个数字，却从不比较。
`story_events.py` 把这个结果原样打印给用户（`webnovel.py story-events health`），
于是"事件文件 2 个、SQLite 1 条"这种分叉会显示为健康。

这与 `ssot_enforcer.verify_consistency()` 的存在理由一致——那个函数专门
检测投影漂移；事件日志自身的文件/SQLite 分叉同样该被检出。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.event_log_store import EventLogStore  # noqa: E402


def _event(chapter: int, event_id: str, subject: str = "伏笔A") -> dict:
    return {
        "event_id": event_id,
        "chapter": chapter,
        "event_type": "open_loop_created",
        "subject": subject,
        "payload": {"note": subject},
    }


@pytest.fixture
def store(tmp_path: Path) -> EventLogStore:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    return EventLogStore(tmp_path)


class TestBasicRoundTrip:
    """正常路径不变。"""

    def test_write_then_read(self, store):
        store.write_events(1, [_event(1, "e1")])
        assert len(store.read_events(1)) == 1

    def test_file_is_written(self, store):
        path = store.write_events(1, [_event(1, "e1")])
        assert path.is_file()
        assert json.loads(path.read_text(encoding="utf-8"))

    def test_sqlite_mirror_populated(self, store):
        store.write_events(1, [_event(1, "e1")])
        assert store.health()["sqlite_rows"] == 1

    def test_rewrite_is_idempotent(self, store):
        store.write_events(1, [_event(1, "e1")])
        store.write_events(1, [_event(1, "e1")])
        assert store.health()["sqlite_rows"] == 1

    def test_list_recent(self, store):
        store.write_events(1, [_event(1, "e1")])
        rows = store.list_recent(chapter=1)
        assert len(rows) == 1
        assert rows[0]["event_id"] == "e1"

    def test_non_dict_events_skipped(self, store):
        store.write_events(1, [_event(1, "e1"), "不是字典", None])
        assert len(store.read_events(1)) == 1


class TestHealthDetectsDivergence:
    """`health()` 必须真的能发现两条写入路径的分叉。"""

    def test_healthy_when_in_sync(self, store):
        store.write_events(1, [_event(1, "e1"), _event(1, "e2", "伏笔B")])
        h = store.health()
        assert h["ok"] is True
        assert h["event_files"] == 1
        assert h["sqlite_rows"] == 2

    def test_sqlite_failure_rolls_back_file(self, store, monkeypatch):
        """SQLite 写失败时，已写的 JSON 文件必须被回滚。

        两条写入路径无法真正原子（文件 vs 数据库），所以取"失败即回滚"：
        磁盘回到调用前状态。若留下"文件有、镜像没有"，而调用方
        （chapter_commit_service.py:271）只 logger.warning，分叉就静默了。
        """
        # 打 store 所属的实际类，而不是 `import data_modules.event_log_store`
        # 再打——全套测试里同一模块可能以 `data_modules.event_log_store` 与
        # `scripts.data_modules.event_log_store` 两个名字各加载一份，按模块名
        # 拿到的类可能不是被测对象那个（表现为 DID NOT RAISE）。
        monkeypatch.setattr(
            type(store), "_write_sqlite_mirror",
            lambda self, events: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(RuntimeError):
            store.write_events(1, [_event(1, "e1")])

        monkeypatch.undo()
        assert not store.paths.event_json(1).is_file(), (
            "SQLite 失败后事件文件仍在——分叉被留了下来"
        )
        h = store.health()
        assert h["event_files"] == 0 and h["sqlite_rows"] == 0
        assert h["ok"] is True, "回滚干净后应是健康的"

    def test_detects_missing_sqlite_rows(self, store):
        """人为造出"文件有、SQLite 没有"→ health 必须报出来。"""
        store.write_events(1, [_event(1, "e1")])
        # 直接把 SQLite 里的记录删掉，模拟镜像丢失
        with store._connect() as conn:
            conn.execute("DELETE FROM story_events")
            conn.commit()

        h = store.health()
        assert h["event_files"] == 1
        assert h["sqlite_rows"] == 0
        assert h["ok"] is False, (
            f"事件文件 1 个、SQLite 0 条，health() 却报 ok=True：{h}"
        )
        assert h.get("mismatch"), "应给出可读的不一致说明"
        assert "e1" in h.get("only_in_files", [])

    def test_rollback_restores_previous_content(self, store, monkeypatch):
        """覆盖写失败时要还原成**旧版本**，而不是简单删掉。"""
        store.write_events(1, [_event(1, "e1")])
        before = store.read_events(1)

        monkeypatch.setattr(
            type(store), "_write_sqlite_mirror",
            lambda self, events: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(RuntimeError):
            store.write_events(1, [_event(1, "e1"), _event(1, "e2", "伏笔B")])
        monkeypatch.undo()

        after = store.read_events(1)
        assert [e["event_id"] for e in after] == [e["event_id"] for e in before], (
            f"覆盖写失败后没有还原旧内容：{after}"
        )

    def test_detects_orphan_sqlite_rows(self, store):
        """SQLite 有、文件没有 → 同样是不一致。"""
        store.write_events(1, [_event(1, "e1")])
        # 删掉事件文件，模拟手工清理 / 回滚
        store.paths.event_json(1).unlink()
        h = store.health()
        assert h["event_files"] == 0
        assert h["sqlite_rows"] == 1
        assert h["ok"] is False, f"SQLite 有 1 条但无事件文件，应报不健康：{h}"

    def test_empty_project_is_healthy(self, store):
        h = store.health()
        assert h["ok"] is True
        assert h["event_files"] == 0 and h["sqlite_rows"] == 0


class TestDualWriteIsBounded:
    """SQLite 写失败时，不能让"文件已写、镜像没写"静默留下来。

    两个可接受的语义：
    - 回滚掉已写的 JSON 文件（真原子），或
    - 保留文件但在异常/健康检查里明确标记不一致。
    不可接受的是：异常往上冒、调用方只 warning、而 health() 说 ok。
    """

    def test_failure_leaves_no_silent_divergence(self, store, monkeypatch):
        monkeypatch.setattr(
            type(store), "_write_sqlite_mirror",
            lambda self, events: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        with pytest.raises(RuntimeError):
            store.write_events(1, [_event(1, "e1")])
        monkeypatch.undo()

        # 要么文件被回滚，要么 health 明确不健康——两者必须占一个
        file_exists = store.paths.event_json(1).is_file()
        healthy = store.health()["ok"]
        assert (not file_exists) or (not healthy), (
            "SQLite 写失败后：事件文件仍在，而 health() 报健康——"
            "分叉被完全掩盖了"
        )
