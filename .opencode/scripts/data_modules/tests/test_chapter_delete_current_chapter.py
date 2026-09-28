"""删除章节后 current_chapter 不得继续指向已删章节（第4轮）。

`chapter_status_changed` 只在提交时把 `current_chapter` 往前推、从不回退；
`chapter_deleted` 此前只从 `chapter_status` 里摘掉条目，不动 `current_chapter`。
于是删掉最新一章后（增量删除与 ssot rebuild 两条路径都是），状态里仍然写着
一个已经不存在的章号——状态面板/dashboard 会显示「第 N 章」而正文里没有。

两条路径都要修，因为它们各写各的。
"""
import json
from pathlib import Path

from data_modules.chapter_delete_service import _clean_state_json, _latest_committed
from data_modules.ssot_enforcer import publish_event, rebuild_state_json


def _seed(root: Path, chapters=()) -> None:
    (root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (root / ".webnovel" / "state.json").write_text(json.dumps({
        "progress": {"current_chapter": 0, "chapter_status": {}},
    }, ensure_ascii=False), encoding="utf-8")
    for ch in chapters:
        publish_event(root, "chapter_status_changed", {"status": "committed"}, chapter=ch)


def _state(root: Path) -> dict:
    return json.loads((root / ".webnovel" / "state.json").read_text(encoding="utf-8"))


class TestLatestCommitted:
    def test_max_committed(self):
        assert _latest_committed({"1": "chapter_committed", "2": "chapter_committed",
                                  "3": "chapter_reviewed"}) == 2

    def test_accepts_unprefixed_legacy_shape(self):
        assert _latest_committed({"1": "committed", "5": "committed"}) == 5

    def test_empty_is_zero(self):
        assert _latest_committed({}) == 0

    def test_ignores_non_numeric_keys(self):
        assert _latest_committed({"draft": "chapter_committed", "7": "chapter_committed"}) == 7


class TestIncrementalDeletePath:
    def test_deleting_latest_chapter_moves_current_chapter_back(self, tmp_path):
        _seed(tmp_path, (1, 2, 3))
        progress = _state(tmp_path)["progress"]
        progress["chapter_status"] = {"1": "chapter_committed", "2": "chapter_committed",
                                      "3": "chapter_committed"}
        progress["current_chapter"] = 3
        (tmp_path / ".webnovel" / "state.json").write_text(
            json.dumps({"progress": progress}, ensure_ascii=False), encoding="utf-8")

        _clean_state_json(tmp_path, [3], dry_run=False)

        after = _state(tmp_path)["progress"]
        assert after["current_chapter"] == 2
        assert "3" not in after["chapter_status"]

    def test_deleting_middle_chapter_keeps_current(self, tmp_path):
        _seed(tmp_path, (1, 2, 3))
        (tmp_path / ".webnovel" / "state.json").write_text(json.dumps({
            "progress": {"current_chapter": 3,
                         "chapter_status": {str(i): "chapter_committed" for i in (1, 2, 3)}},
        }, ensure_ascii=False), encoding="utf-8")

        _clean_state_json(tmp_path, [2], dry_run=False)
        assert _state(tmp_path)["progress"]["current_chapter"] == 3

    def test_deleting_all_chapters_resets_to_zero(self, tmp_path):
        _seed(tmp_path, (1, 2))
        (tmp_path / ".webnovel" / "state.json").write_text(json.dumps({
            "progress": {"current_chapter": 2,
                         "chapter_status": {"1": "chapter_committed", "2": "chapter_committed"}},
        }, ensure_ascii=False), encoding="utf-8")

        _clean_state_json(tmp_path, [1, 2], dry_run=False)
        assert _state(tmp_path)["progress"]["current_chapter"] == 0


class TestRebuildPath:
    def test_rebuild_does_not_resurrect_deleted_chapter(self, tmp_path):
        _seed(tmp_path, (1, 2, 3))
        publish_event(tmp_path, "chapter_deleted", {"chapters": [3]}, chapter=3)

        st = rebuild_state_json(tmp_path)
        assert st["progress"]["current_chapter"] == 2, (
            f"rebuild 把已删章节当成了当前章: {st['progress']['current_chapter']}"
        )
        assert sorted(st["progress"]["chapter_status"]) == ["1", "2"]

    def test_rebuild_after_deleting_middle_keeps_latest(self, tmp_path):
        _seed(tmp_path, (1, 2, 3))
        publish_event(tmp_path, "chapter_deleted", {"chapters": [2]}, chapter=2)

        st = rebuild_state_json(tmp_path)
        assert st["progress"]["current_chapter"] == 3
        assert sorted(st["progress"]["chapter_status"]) == ["1", "3"]

    def test_rebuild_drops_chapter_status_seq_entry(self, tmp_path):
        _seed(tmp_path, (1, 2))
        publish_event(tmp_path, "chapter_deleted", {"chapters": [2]}, chapter=2)

        st = rebuild_state_json(tmp_path)
        assert "2" not in (st["progress"].get("chapter_status_seq") or {})

    def test_rebuild_with_all_chapters_deleted(self, tmp_path):
        _seed(tmp_path, (1, 2))
        publish_event(tmp_path, "chapter_deleted", {"chapters": [1, 2]}, chapter=2)

        st = rebuild_state_json(tmp_path)
        assert st["progress"]["current_chapter"] == 0
        assert st["progress"]["chapter_status"] == {}
