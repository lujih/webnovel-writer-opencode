"""compactor 不得静默删除孤立的旧时间线事件（第5轮）。

`enforce_capacity` 把 >50 章的 timeline 条目归为 old，**仅当 old 超过一条时**
才生成摘要——但 `data.timeline = fresh` 原本在这层 if 之外无条件执行。
于是「第 1 章」这种只剩一条旧事件的情况：既没有摘要，又被直接删掉，
无痕迹的数据丢失。

留着的成本只有一行，摘要反而会把一条具体事实压成「早期关键事件」这类
泛化文本——所以此处既不摘要也不丢弃。
"""
import pytest

from data_modules.config import DataModulesConfig
from data_modules.memory.compactor import enforce_capacity
from data_modules.memory.schema import MemoryItem, ScratchpadData


def _tl(chapter: int, label: str) -> MemoryItem:
    return MemoryItem(
        id=f"tl-{chapter}",
        layer="episodic",
        category="timeline",
        subject=label,
        field="event",
        value=label,
        payload={},
        status="active",
        source_chapter=chapter,
    )


def _data(*rows: MemoryItem) -> ScratchpadData:
    return ScratchpadData(timeline=list(rows))


def _filler(n: int) -> list[MemoryItem]:
    """凑条数用的低章事件（source_chapter=0，排序时排在时间线之后）。"""
    return [
        MemoryItem(
            id=f"cs-{i}", layer="semantic", category="character_state",
            subject=f"角色{i}", field="mood", value="平静",
            payload={}, status="active", source_chapter=0,
        )
        for i in range(n)
    ]


class TestLoneOldTimelineIsKept:
    def test_single_old_item_is_not_deleted(self):
        """只有一条旧事件时：必须留下（这正是被修掉的静默删除）。"""
        data = _data(_tl(1, "开局"), _tl(60, "近事"))
        data.character_state = _filler(10)

        out = enforce_capacity(data, max_items=5)

        kept = [r.value for r in out.timeline]
        assert "开局" in kept, f"孤立的旧时间线事件被静默删除，剩余: {kept}"

    def test_no_summary_fabricated_for_lone_item(self):
        """不该为一条事件编造摘要——那会丢失它的具体内容。"""
        data = _data(_tl(1, "开局"), _tl(60, "近事"))
        data.character_state = _filler(10)

        out = enforce_capacity(data, max_items=5)

        assert not [f for f in out.story_facts if f.subject == "timeline_summary"]

    def test_two_old_items_are_summarized_and_dropped(self):
        """两条以上仍按原设计：摘要 + 从 timeline 移除。"""
        data = _data(_tl(1, "开局"), _tl(2, "变故"), _tl(60, "近事"))
        data.character_state = _filler(10)

        out = enforce_capacity(data, max_items=5)

        kept = [r.value for r in out.timeline]
        assert "开局" not in kept and "变故" not in kept, f"旧事件未按设计移除: {kept}"
        assert "近事" in kept

    def test_repeated_compaction_is_stable(self):
        """反复压缩不应让时间线逐渐变空。"""
        data = _data(_tl(1, "开局"), _tl(60, "近事"))
        data.character_state = _filler(10)
        once = enforce_capacity(data, max_items=5)
        twice = enforce_capacity(once, max_items=5)
        assert [r.value for r in twice.timeline] == [r.value for r in once.timeline]
        assert "开局" in [r.value for r in twice.timeline]

    def test_all_recent_untouched(self):
        data = _data(_tl(55, "a"), _tl(56, "b"), _tl(57, "c"))
        out = enforce_capacity(data, max_items=10_000)
        assert len(out.timeline) == 3
        assert not out.story_facts

