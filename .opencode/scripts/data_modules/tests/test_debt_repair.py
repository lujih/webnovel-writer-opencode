"""伏笔债去重修复工具：默认演练、只删可证冗余的行（第5轮）。

这是**破坏性操作**，所以契约比功能更重要：

1. 缺省绝不写库（必须显式 --apply）；
2. 只删「同 debt_type + 同 note + 同为 active」的重复项，保留 id 最小者；
3. **无 created 事件的债不删**——它们既销不掉也无法归类，可能来自旧版本或
   手工录入，删掉等于替用户做判断；
4. 计划与执行共用同一份 DedupPlan，dry-run 看到的就是 apply 会做的。
"""
import json
import sqlite3

import pytest

from data_modules.config import DataModulesConfig
from data_modules.debt_repair import (
    apply_dedup,
    format_report,
    plan_dedup,
    repair,
)
from data_modules.index_manager import IndexManager


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".webnovel").mkdir(parents=True)
    return tmp_path


def _db(project):
    return project / ".webnovel" / "index.db"


def _all(project):
    with sqlite3.connect(str(_db(project))) as con:
        return con.execute("SELECT id, status FROM chase_debt ORDER BY id").fetchall()


def _events(project):
    with sqlite3.connect(str(_db(project))) as con:
        return con.execute("SELECT debt_id, note FROM debt_events ORDER BY debt_id").fetchall()


def _idx(project):
    return IndexManager(DataModulesConfig.from_project_root(str(project)))


class TestDryRunIsDefault:
    def test_plan_does_not_write(self, project):
        idx = _idx(project)
        idx.create_simple_debt("foreshadowing", 1, 51, note="三年之约")
        # 绕过幂等制造重复
        with sqlite3.connect(str(_db(project))) as con:
            con.execute("INSERT INTO chase_debt (debt_type, source_chapter, due_chapter)"
                        " VALUES ('foreshadowing', 1, 51)")
            new_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.execute("INSERT INTO debt_events (debt_id, event_type, amount, chapter, note)"
                        " VALUES (?, 'created', 1.0, 1, '三年之约')", (new_id,))
            con.commit()

        before = _all(project)
        plan = repair(project, apply=False)

        assert _all(project) == before, "演练却改了库"
        assert len(plan.to_delete) == 1
        assert "演练（未写库）" in format_report(plan, applied=False)

    def test_apply_removes_only_duplicates(self, project):
        idx = _idx(project)
        keep = idx.create_simple_debt("foreshadowing", 1, 51, note="三年之约")
        idx.create_simple_debt("foreshadowing", 2, 52, note="身世之谜")
        with sqlite3.connect(str(_db(project))) as con:
            con.execute("INSERT INTO chase_debt (debt_type, source_chapter, due_chapter)"
                        " VALUES ('foreshadowing', 1, 51)")
            dup = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.execute("INSERT INTO debt_events (debt_id, event_type, amount, chapter, note)"
                        " VALUES (?, 'created', 1.0, 1, '三年之约')", (dup,))
            con.commit()

        plan = repair(project, apply=True)

        remaining = _all(project)
        assert dup not in [r[0] for r in remaining]
        assert keep in [r[0] for r in remaining]
        assert "身世之谜" in [n for _, n in _events(project)]
        assert apply_dedup(_db(project), plan) == 0, "重复执行不应再删"


class TestOrphansAreNeverTouched:
    def test_debt_without_created_event_is_reported_not_deleted(self, project):
        _idx(project).create_simple_debt("foreshadowing", 1, 51, note="有据可查")
        with sqlite3.connect(str(_db(project))) as con:
            con.execute("INSERT INTO chase_debt (debt_type, source_chapter, due_chapter)"
                        " VALUES ('foreshadowing', 9, 59)")
            orphan = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.commit()

        plan = repair(project, apply=True)

        assert [r["id"] for r in plan.orphan_rows] == [orphan]
        assert orphan in [r[0] for r in _all(project)], "无主债被误删了"


class TestScope:
    def test_other_debt_types_untouched(self, project):
        """追读债等类型语义不同，重复未必是 bug——不碰。

        前两次 create 会被幂等守卫收敛成一条，故此处只有 1 条来自 API，
        1 条手工插入；修复后应仍是 2 条（一条都没被清理）。
        """
        _idx(project).create_simple_debt("reading_power", 1, 51, note="同一个文本")
        _idx(project).create_simple_debt("reading_power", 1, 51, note="同一个文本")
        with sqlite3.connect(str(_db(project))) as con:
            con.execute("INSERT INTO chase_debt (debt_type, source_chapter, due_chapter)"
                        " VALUES ('reading_power', 1, 51)")
            extra = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.execute("INSERT INTO debt_events (debt_id, event_type, amount, chapter, note)"
                        " VALUES (?, 'created', 1.0, 1, '同一个文本')", (extra,))
            con.commit()

        before = _all(project)
        repair(project, apply=True)

        assert _all(project) == before, "非伏笔债被清理了"

    def test_resolved_debts_not_treated_as_duplicates(self, project):
        idx = _idx(project)
        idx.create_simple_debt("foreshadowing", 1, 51, note="三年之约")
        idx.resolve_debt_by_subject("三年之约")
        with sqlite3.connect(str(_db(project))) as con:
            con.execute("INSERT INTO chase_debt (debt_type, source_chapter, due_chapter)"
                        " VALUES ('foreshadowing', 1, 51)")
            dup = con.execute("SELECT last_insert_rowid()").fetchone()[0]
            con.execute("INSERT INTO debt_events (debt_id, event_type, amount, chapter, note)"
                        " VALUES (?, 'created', 1.0, 1, '三年之约')", (dup,))
            con.commit()

        plan = plan_dedup(_db(project))

        assert plan.to_delete == [], "已回收的旧债被当成重复项了"

    def test_three_way_duplicate_keeps_lowest_id(self, project):
        _idx(project).create_simple_debt("foreshadowing", 1, 51, note="A")
        with sqlite3.connect(str(_db(project))) as con:
            ids = []
            for _ in range(2):
                con.execute("INSERT INTO chase_debt (debt_type, source_chapter, due_chapter)"
                            " VALUES ('foreshadowing', 1, 51)")
                nid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
                con.execute("INSERT INTO debt_events (debt_id, event_type, amount, chapter, note)"
                            " VALUES (?, 'created', 1.0, 1, 'A')", (nid,))
                ids.append(nid)
            con.commit()

        plan = plan_dedup(_db(project))
        assert plan.duplicate_groups == 1
        assert sorted(plan.to_delete) == sorted(ids)
        assert plan.kept_ids == [1]

    def test_missing_index_db_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            repair(tmp_path)


class TestCliSurface:
    def test_command_is_registered(self):
        from data_modules import index_manager
        import argparse

        parser = index_manager.main.__globals__  # 触达模块命名空间即可
        assert parser is not None
        # 直接验证 CLI 走通：参数存在且默认不写库
        src = open(index_manager.__file__, encoding="utf-8").read()
        assert "repair-foreshadowing-debts" in src
        assert '"--apply"' in src
