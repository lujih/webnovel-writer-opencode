"""open_loop_closed 必须在 memory 投影里把伏笔标记为已回收（第4轮）。

memory/writer.py 此前只处理 open_loop_created。结果：已回收的伏笔在
scratchpad 里永远停在 status=active。后果有两处——

1. `compactor.collect_garbage` 只清理 payload.status ∈
   {resolved, closed, done, paid_off, payoff} 的条目，收过的线永远清不掉；
2. `memory_contract_adapter.load_context` 第 6 段每章把「紧急伏笔」前 3 条
   注入**写手上下文**，等于反复告诉 AI 去收一条早就收了的线——白烧 token，
   还会诱发把已收的线重新提起。

state 投影（StateProjectionWriter._apply_foreshadowing）一直是对的，缺口
只在 memory 投影这一侧。
"""
import pytest

from data_modules.config import DataModulesConfig
from data_modules.memory.compactor import collect_garbage
from data_modules.memory.writer import MemoryWriter

LOOP = "黑色棺材的来历"


def _writer(tmp_path) -> MemoryWriter:
    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    return MemoryWriter(cfg)


def _commit(chapter: int, *events) -> dict:
    return {
        "meta": {"chapter": chapter},
        "accepted_events": [
            {"event_type": t, "payload": p, "subject": p.get("content", "")}
            for t, p in events
        ],
    }


@pytest.fixture
def writer(tmp_path):
    return _writer(tmp_path)


def _open_loops(writer):
    data = writer.store.load()
    return list(data.open_loops)


def test_closed_loop_is_marked_resolved(writer):
    writer.apply_commit_projection(_commit(
        1, ("open_loop_created", {"content": LOOP, "urgency": 90}),
    ))
    assert [r.subject for r in _open_loops(writer)] == [LOOP]
    assert _open_loops(writer)[0].payload.get("status") == "active"

    writer.apply_commit_projection(_commit(
        12, ("open_loop_closed", {"content": LOOP}),
    ))

    rows = _open_loops(writer)
    assert len(rows) == 1, "关闭事件新建了一条而不是替换原条目"
    assert rows[0].payload.get("status") == "resolved"
    assert rows[0].payload.get("resolved_chapter") == 12


def test_compactor_now_removes_the_closed_loop(writer):
    writer.apply_commit_projection(_commit(
        1, ("open_loop_created", {"content": LOOP, "urgency": 90}),
    ))
    writer.apply_commit_projection(_commit(12, ("open_loop_closed", {"content": LOOP})))

    cleaned = collect_garbage(writer.store.load())
    assert cleaned.open_loops == [], "已回收的伏笔没有被 compactor 清理"


def test_urgent_loops_no_longer_injected_after_close(writer):
    """写手上下文里的「紧急伏笔」不应再包含已回收的线。"""
    from data_modules.memory_contract_adapter import MemoryContractAdapter

    writer.apply_commit_projection(_commit(
        1, ("open_loop_created", {"content": LOOP, "urgency": 90}),
    ))
    writer.apply_commit_projection(_commit(12, ("open_loop_closed", {"content": LOOP})))

    adapter = MemoryContractAdapter(config=writer.config)
    active = [l.subject for l in adapter.get_open_loops()]
    assert LOOP not in active, f"已回收的伏笔仍被当作活跃伏笔注入上下文: {active}"


def test_still_active_loop_is_untouched(writer):
    other = "三年之约"
    writer.apply_commit_projection(_commit(
        1, ("open_loop_created", {"content": LOOP}),
        ("open_loop_created", {"content": other}),
    ))
    writer.apply_commit_projection(_commit(12, ("open_loop_closed", {"content": LOOP})))

    rows = {r.subject: r.payload.get("status") for r in _open_loops(writer)}
    assert rows == {LOOP: "resolved", other: "active"}, "误伤未闭合的伏笔"


def test_orphan_close_kept_as_resolved_but_never_injected(writer):
    """从未登记过的线被关闭：不抛异常，按 resolved 记录保留，但绝不注入写手。

    与 state 侧行为一致（StateProjectionWriter 保留孤儿 closed 记录不丢数据）。
    """
    writer.apply_commit_projection(_commit(5, ("open_loop_closed", {"content": "从未登记"})))

    rows = _open_loops(writer)
    assert len(rows) == 1, "孤儿 closed 事件应保留为 resolved 记录，不丢数据"
    assert rows[0].payload.get("status") == "resolved"

    from data_modules.memory_contract_adapter import MemoryContractAdapter
    adapter = MemoryContractAdapter(config=writer.config)
    assert "从未登记" not in [l.subject for l in adapter.get_open_loops()]


def test_content_fallback_fields_match_created(writer):
    """两路投影靠同一字符串匹配；created 用 description 而非 content 时也要对得上。"""
    writer.apply_commit_projection(_commit(
        1, ("open_loop_created", {"description": "身份之谜"}),
    ))
    writer.apply_commit_projection(_commit(
        9, ("open_loop_closed", {"unanswered_question": "身份之谜"}),
    ))

    rows = _open_loops(writer)
    assert len(rows) == 1
    assert rows[0].payload.get("status") == "resolved"

