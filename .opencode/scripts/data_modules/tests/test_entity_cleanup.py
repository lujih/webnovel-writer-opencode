#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`entity-clean --mark-invalid` 的幂等性与脏实体判定。

这个命令把「非中文 entity_id」（如 `xiao_yan` / `fire_spirit`）写进
`invalid_facts` 表供人工复核。它没有任何测试覆盖（118 行）。

## 缺陷一：重复运行会让 invalid_facts 无限膨胀

`_mark_invalid()` 对每个脏实体无条件 `INSERT`，没有去重、没有唯一约束。
实测连跑三次：

    第 1 次: 本次写 1 条，表内累计 1 条
    第 2 次: 本次写 1 条，表内累计 2 条
    第 3 次: 本次写 1 条，表内累计 3 条

人工复核时看到三条一模一样的记录，无从判断是真的三个问题还是同一个问题
被重复标记。这个命令的设计意图是「标记一次，等人处理」，重复标记是纯噪声。

## 缺陷二：已 resolved 的实体仍会被重新标记

表里有 `resolved` 列，说明存在「已处理」这个状态。但 `_mark_invalid` 从不
检查它——用户处理完某条、把它标成 resolved，下次再跑 `entity-clean` 又会被
重新插一条未处理记录，**已完成的复核被推翻**。

## 缺陷三：`--mark-invalid` 之外的失败静默

`_scan_dirty_entities()` 在 index.db 不存在时打印 ERROR 并返回 `[]`，而
`cmd_entity_clean` 把 `[]` 当成「没有脏实体」：

    dirty = _scan_dirty_entities(db_path)
    if not dirty:
        print("No dirty entities found.")
        return 0            ← rc=0，脚本链会以为检查通过

「数据库不存在」与「数据库干净」是两件事，前者应当非零退出。

## 本文件钉住

- 重复运行不产生重复记录（幂等）
- 已 resolved 的实体不被重新标记
- index.db 缺失时非零退出，且错误信息可读
- 正常判定逻辑（有 CJK 即干净）保持不变
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.entity_cleanup import (  # noqa: E402
    _is_dirty,
    _mark_invalid,
    _scan_dirty_entities,
    cmd_entity_clean,
)


class _Args:
    def __init__(self, project_root: Path, mark_invalid: bool = False):
        self.project_root = str(project_root)
        self.mark_invalid = mark_invalid


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    return tmp_path


def _make_db(project: Path, entities: list[tuple[str, str, str, int]]) -> Path:
    db = project / ".webnovel" / "index.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS entities ("
        " id INTEGER PRIMARY KEY, entity_id TEXT, entity_type TEXT,"
        " entity_name TEXT, chapter_first_seen INTEGER)")
    for eid, etype, ename, ch in entities:
        conn.execute(
            "INSERT INTO entities (entity_id,entity_type,entity_name,chapter_first_seen)"
            " VALUES (?,?,?,?)", (eid, etype, ename, ch))
    conn.commit()
    conn.close()
    return db


def _count(db: Path, where: str = "1=1") -> int:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(f"SELECT COUNT(*) FROM invalid_facts WHERE {where}").fetchone()[0]
    finally:
        conn.close()


class TestDirtyDetectionUnchanged:
    """既有判定逻辑不能改坏。"""

    @pytest.mark.parametrize("eid,expected", [
        ("萧炎", False),
        ("主角·萧炎", False),
        ("火焰精灵", False),
        ("xiao_yan", True),
        ("XiaoYan", True),
        ("fire_spirit", True),
        ("main_character", True),
        ("", True),
    ])
    def test_is_dirty(self, eid, expected):
        assert _is_dirty(eid) is expected

    def test_scan_finds_only_dirty(self, project):
        _make_db(project, [
            ("xiao_yan", "角色", "萧炎", 1),
            ("萧炎", "角色", "萧炎", 1),
            ("fire_spirit", "物品", "火灵", 2),
        ])
        dirty = _scan_dirty_entities(project / ".webnovel" / "index.db")
        assert sorted(d["entity_id"] for d in dirty) == ["fire_spirit", "xiao_yan"]


class TestMarkInvalidIsIdempotent:
    """核心缺陷一：重复运行不产生重复记录。"""

    def test_second_run_adds_nothing(self, project):
        db = _make_db(project, [("xiao_yan", "角色", "萧炎", 1)])
        dirty = _scan_dirty_entities(db)

        first = _mark_invalid(db, dirty)
        assert first == 1
        assert _count(db) == 1

        second = _mark_invalid(db, dirty)
        assert _count(db) == 1, (
            f"第二次 --mark-invalid 让 invalid_facts 从 1 条变成 "
            f"{_count(db)} 条。同一个脏实体被重复标记，人工复核时看到"
            f"多条一模一样的记录，无从判断是真问题还是重复。"
        )
        assert second == 0, "已标记过的实体不该再算作「本次新标记」"

    def test_three_runs_still_one_row(self, project):
        db = _make_db(project, [("xiao_yan", "角色", "萧炎", 1)])
        dirty = _scan_dirty_entities(db)
        for _ in range(3):
            _mark_invalid(db, dirty)
        assert _count(db) == 1

    def test_different_entities_each_marked_once(self, project):
        db = _make_db(project, [
            ("xiao_yan", "角色", "萧炎", 1),
            ("fire_spirit", "物品", "火灵", 2),
        ])
        dirty = _scan_dirty_entities(db)
        _mark_invalid(db, dirty)
        _mark_invalid(db, dirty)
        assert _count(db) == 2, "两个不同实体应各有一条记录，重复运行不增加"


class TestResolvedEntitiesStayResolved:
    """核心缺陷二：已处理的实体不该被重新标记。"""

    def test_resolved_not_remarked(self, project):
        db = _make_db(project, [("xiao_yan", "角色", "萧炎", 1)])
        dirty = _scan_dirty_entities(db)
        _mark_invalid(db, dirty)

        # 用户处理完，标成 resolved
        conn = sqlite3.connect(str(db))
        conn.execute("UPDATE invalid_facts SET resolved = 1")
        conn.commit()
        conn.close()

        _mark_invalid(db, dirty)

        unresolved = _count(db, "resolved = 0")
        assert unresolved == 0, (
            f"已 resolved 的实体被重新标记了 {unresolved} 条未处理记录——"
            f"用户完成的复核被推翻"
        )


class TestMissingDatabaseIsAnError:
    """核心缺陷三：db 不存在 ≠ 没有脏实体。"""

    def test_nonzero_exit_when_db_missing(self, project, capsys):
        rc = cmd_entity_clean(_Args(project))
        assert rc != 0, (
            "index.db 不存在时返回 0——脚本链会以为检查通过。"
            "「数据库不存在」与「数据库干净」是两件事。"
        )

    def test_error_message_mentions_db(self, project, capsys):
        cmd_entity_clean(_Args(project))
        captured = capsys.readouterr()
        combined = captured.out + captured.err
        assert "index.db" in combined, f"错误信息没指明 index.db：{combined!r}"

    def test_clean_db_returns_zero(self, project, capsys):
        _make_db(project, [("萧炎", "角色", "萧炎", 1)])
        rc = cmd_entity_clean(_Args(project))
        assert rc == 0, "数据库存在且没有脏实体时应返回 0"

    def test_dirty_without_flag_returns_zero(self, project, capsys):
        """dry-run 不是错误，应返回 0。"""
        _make_db(project, [("xiao_yan", "角色", "萧炎", 1)])
        rc = cmd_entity_clean(_Args(project, mark_invalid=False))
        assert rc == 0
        captured = capsys.readouterr()
        assert "Dry run" in captured.out or "dry" in captured.out.lower()


class TestCliEndToEnd:
    """通过 CLI 验证幂等性（用户实际怎么用）。"""

    def test_cli_twice_single_row(self, project):
        db = _make_db(project, [("xiao_yan", "角色", "萧炎", 1)])
        args = _Args(project, mark_invalid=True)
        cmd_entity_clean(args)
        cmd_entity_clean(args)
        assert _count(db) == 1
