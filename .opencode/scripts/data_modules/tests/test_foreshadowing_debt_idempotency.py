"""伏笔债建债幂等：章节被重复提交不得让债务成倍膨胀（第5轮真书取证）。

真书项目（凡尘之舞）实测数据：

    事件日志  open_loop_created 事件        26 条
    债务表    created 债务                    141 条（note 唯一 91，49 种重复）
    逐章对账  ch29-ch38 债务 2-6 条/章，但这些章的 created 事件数为 0
              ch52-ch55 被重复提交 2-3 次

成因：state / memory 投影侧的 open_loop_created **本就按 content 去重**，
债务侧却每见一次事件就插一条。章节一被重复提交（真书里 ch52-55 就是），
债务就成倍膨胀；而 ch29-51 的债是事件溯源之前的存量。

修法：对同一 note 的活跃债务幂等——与投影侧的语义对齐。已 resolved 的
旧债不阻止重新建债（伏笔可以再次埋下）。
"""
import pytest

from data_modules.config import DataModulesConfig
from data_modules.index_manager import IndexManager


@pytest.fixture
def idx(tmp_path):
    (tmp_path / ".webnovel").mkdir()
    return IndexManager(DataModulesConfig.from_project_root(str(tmp_path)))


def _all(idx):
    with idx._get_conn() as conn:
        return conn.execute(
            "SELECT id, status FROM chase_debt ORDER BY id").fetchall()


class TestIdempotentCreation:
    def test_same_note_twice_creates_one_debt(self, idx):
        a = idx.create_simple_debt("foreshadowing", 10, 60, note="黑色棺材的来历")
        b = idx.create_simple_debt("foreshadowing", 10, 60, note="黑色棺材的来历")
        assert a == b, "重复建债返回了不同 id"
        assert len(_all(idx)) == 1

    def test_repeated_commits_do_not_inflate(self, idx):
        """模拟一章被重复提交 3 次（真书 ch52-55 的情形）。"""
        for _ in range(3):
            idx.create_simple_debt("foreshadowing", 52, 102, note="三年之约")
        assert len(_all(idx)) == 1

    def test_different_notes_still_create_separately(self, idx):
        idx.create_simple_debt("foreshadowing", 1, 51, note="三年之约")
        idx.create_simple_debt("foreshadowing", 2, 52, note="身世之谜")
        assert len(_all(idx)) == 2

    def test_different_debt_types_do_not_collide(self, idx):
        idx.create_simple_debt("foreshadowing", 1, 51, note="同一个文本")
        idx.create_simple_debt("other", 1, 51, note="同一个文本")
        assert len(_all(idx)) == 2

    def test_resolved_debt_does_not_block_replanting(self, idx):
        """伏笔可以再次埋下：销掉后应能重新建债。"""
        a = idx.create_simple_debt("foreshadowing", 1, 51, note="三年之约")
        assert idx.resolve_debt_by_subject("三年之约") is True
        b = idx.create_simple_debt("foreshadowing", 30, 80, note="三年之约")
        assert b != a, "已回收的伏笔没能重新建债"
        assert len([r for r in _all(idx) if r[1] == "active"]) == 1

    def test_dedupe_survives_interleaved_inserts(self, idx):
        for i in range(5):
            idx.create_simple_debt("foreshadowing", i, i + 50, note="A")
            idx.create_simple_debt("foreshadowing", i, i + 50, note="B")
        assert len(_all(idx)) == 2

    def test_empty_note_still_creates(self, idx):
        """无 note 的债无法去重（也无从匹配），保持每次创建。"""
        idx.create_simple_debt("foreshadowing", 1, 51, note="", subject="")
        idx.create_simple_debt("foreshadowing", 1, 51, note="", subject="")
        assert len(_all(idx)) == 2


class TestMatchesProjectionSemantics:
    def test_state_projection_dedupes_created_by_content(self, tmp_path):
        """对照：投影侧按 content 去重，债务侧现在与之语义一致。"""
        from data_modules.ssot_enforcer import publish_event, rebuild_state_json

        (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
        for _ in range(3):
            publish_event(tmp_path, "open_loop_created", {"content": "三年之约"}, chapter=1)

        st = rebuild_state_json(tmp_path)
        loops = ((st.get("plot_threads") or {}).get("foreshadowing") or [])
        assert len(loops) == 1, "投影侧未按 content 去重，语义基准需重新确认"
