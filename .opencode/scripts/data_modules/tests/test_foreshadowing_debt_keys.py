"""伏笔债建/销必须用同一个键（第5轮）。

`_sync_foreshadowing` 的 docstring 声称已用 `coerce_loop_content` 统一了键，
但只有建债侧用了：

    建债: note = content or subject or "ch{N} foreshadowing"   ← 键是 content
    销债: resolve_debt_by_subject(subject=subject)              ← 搜的是 subject

`content`（coerce_loop_content 的归一结果）与 `subject`（事件 subject 字段）
在多候选 schema 下本就不同，于是两边对不上：伏笔债永远闭合不掉，
active 计数虚高——正是 _FORESHADOW_DUE_OFFSET 之后集中爆雷的那种。

另一处：create_simple_debt 接受 subject 形参却**从未使用**，而 debt_events 的
note 只在 note 非空时才写。note 为空就留下一条没有任何可匹配文本的债，
之后无论按什么 key 都销不掉。

第三处：resolve_debt_by_subject 用 LIKE %subject%，空 subject 会匹配**全部**
活跃债务，一次误调用把整本书的伏笔债清空。
"""
import pytest

from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.config import DataModulesConfig


@pytest.fixture
def project(tmp_path):
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    return tmp_path


def _active_debts(project):
    from data_modules.index_manager import IndexManager
    idx = IndexManager(DataModulesConfig.from_project_root(str(project)))
    with idx._get_conn() as conn:
        return conn.execute(
            "SELECT id, status FROM chase_debt WHERE debt_type='foreshadowing'"
        ).fetchall()


def _commit(chapter, *events):
    return {"meta": {"chapter": chapter}, "accepted_events": list(events)}


def _loop(etype, chapter, content, subject=""):
    payload = {"content": content, "description": content}
    if subject:
        payload["subject"] = subject
    return {"event_type": etype, "subject": subject or content, "payload": payload}


class TestCreateAndCloseUseSameKey:
    def test_debt_created_then_closed_with_different_subject(self, project):
        """建债用 content、销债用 subject 时，债销不掉——这是被修掉的 bug。"""
        svc = ChapterCommitService(project_root=project)
        svc._sync_foreshadowing(_commit(3, _loop("open_loop_created", 3, "黑色棺材的来历")))

        before = _active_debts(project)
        assert len(before) == 1

        # 闭合事件的 subject 与建债时的 content 不同
        svc._sync_foreshadowing(_commit(
            12, _loop("open_loop_closed", 12, "黑色棺材的来历", subject="chen_sheng")
        ))

        remaining = [r for r in _active_debts(project) if r[1] == "active"]
        assert remaining == [], (
            f"伏笔债没能闭合，active 计数虚高: {remaining}"
        )

    def test_close_by_promise_paid_off_also_works(self, project):
        svc = ChapterCommitService(project_root=project)
        svc._sync_foreshadowing(_commit(3, _loop("open_loop_created", 3, "三年之约")))
        svc._sync_foreshadowing(_commit(
            20, _loop("promise_paid_off", 20, "三年之约", subject="su_wan")
        ))
        assert [r for r in _active_debts(project) if r[1] == "active"] == []

    def test_distinct_loops_do_not_close_each_other(self, project):
        """不能因为都用同一段文本就互相闭合。"""
        svc = ChapterCommitService(project_root=project)
        svc._sync_foreshadowing(_commit(3, _loop("open_loop_created", 3, "三年之约")))
        svc._sync_foreshadowing(_commit(4, _loop("open_loop_created", 4, "身世之谜")))

        svc._sync_foreshadowing(_commit(20, _loop("open_loop_closed", 20, "三年之约")))

        active = [r for r in _active_debts(project) if r[1] == "active"]
        assert len(active) == 1, f"应为「身世之谜」仍活跃，实际 {active}"


class TestEmptyKeySafety:
    def test_empty_subject_does_not_resolve_everything(self, project):
        """resolve_debt_by_subject 用 LIKE %x%——空 x 会匹配全部活跃债。"""
        from data_modules.index_manager import IndexManager
        idx = IndexManager(DataModulesConfig.from_project_root(str(project)))
        idx.create_simple_debt("foreshadowing", 1, 5, note="三年之约")
        idx.create_simple_debt("foreshadowing", 2, 6, note="身世之谜")

        assert idx.resolve_debt_by_subject(subject="") is False, (
            "空 subject 把全部伏笔债标记为已解决"
        )
        assert len([r for r in _active_debts(project) if r[1] == "active"]) == 2

    def test_close_event_with_no_usable_key_is_ignored(self, project):
        """闭合事件既无 content 也无 subject 时不得销任何债。"""
        svc = ChapterCommitService(project_root=project)
        svc._sync_foreshadowing(_commit(3, _loop("open_loop_created", 3, "三年之约")))

        svc._sync_foreshadowing(_commit(9, {
            "event_type": "open_loop_closed", "subject": "",
            "payload": {"description": ""},
        }))

        assert len([r for r in _active_debts(project) if r[1] == "active"]) == 1


class TestCreatedDebtIsAlwaysFindable:
    def test_debt_without_note_is_still_matchable(self, project):
        """subject 形参此前被完全忽略，note 空时留下无法匹配的债。"""
        from data_modules.index_manager import IndexManager
        idx = IndexManager(DataModulesConfig.from_project_root(str(project)))
        idx.create_simple_debt("foreshadowing", 1, 5, note="", subject="三年之约")

        with idx._get_conn() as conn:
            rows = conn.execute(
                "SELECT note FROM debt_events WHERE event_type='created'"
            ).fetchall()
        assert rows and rows[0][0], (
            "只给 subject 不给 note 时，债没有任何可匹配文本——永远销不掉"
        )
        assert idx.resolve_debt_by_subject("三年之约") is True

    def test_explicit_note_still_takes_precedence(self, project):
        """给了 note 就用 note，不被 subject 覆盖。"""
        from data_modules.index_manager import IndexManager
        idx = IndexManager(DataModulesConfig.from_project_root(str(project)))
        idx.create_simple_debt("foreshadowing", 1, 5, note="三年之约", subject="chen_sheng")
        assert idx.resolve_debt_by_subject("三年之约") is True

