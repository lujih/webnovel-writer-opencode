"""SSOT 事件日志的底层行为钉（2026-10-18 深度审查，P2-4）。

覆盖事件文件名的**数值序**语义：`_next_event_seq`、`_event_paths_in_seq_order`、
`read_events` 三者都必须按数值而非字典序推进/重放。
"""
import json
from pathlib import Path

class TestEventSeqOrdering:
    """事件日志必须按**数值**序号排序，不能按文件名字典序（P2-4）。

    文件名是 `%06d.event.json`（6 位补零）。序号一旦到第 7 位，
    字典序就与数值序错位：

        字典序: "1000000.event.json" < "999999.event.json"
        数值序:  999999 < 1000000

    后果不止是重放顺序错 —— `_next_event_seq()` 取 `existing[-1]` 拿最大序号，
    字典序下会取到 "999999"，+1 = 1000000，**正好撞上已存在的文件名**，
    `os.replace()` 静默覆盖一条真实事件，append-only 日志变成可丢事件的日志。
    """

    @staticmethod
    def _write(path: Path, seq: int) -> None:
        path.write_text(
            json.dumps({"event_id": f"e{seq}", "event_type": "chapter_status_changed",
                        "chapter": seq, "subject": "", "payload": {}}),
            encoding="utf-8")

    def test_seq_paths_are_numeric_not_lexicographic(self, tmp_path):
        from data_modules.ssot_enforcer import _event_paths_in_seq_order
        for seq in (999998, 999999, 1000000):
            self._write(tmp_path / f"{seq:06d}.event.json", seq)
        names = [p.name for p in _event_paths_in_seq_order(tmp_path)]
        assert names == [
            "999998.event.json", "999999.event.json", "1000000.event.json",
        ], f"排序不是数值序：{names}"

    def test_next_seq_does_not_collide_past_six_digits(self, tmp_path):
        from data_modules.ssot_enforcer import _next_event_seq
        for seq in (999998, 999999, 1000000):
            self._write(tmp_path / f"{seq:06d}.event.json", seq)
        assert _next_event_seq(tmp_path) == 1000001, (
            "第 100 万条事件之后 _next_event_seq 会返回已占用的序号，"
            "os.replace 将静默覆盖真实事件")

    def test_unparseable_names_sort_last_and_are_skipped(self, tmp_path):
        from data_modules.ssot_enforcer import _event_paths_in_seq_order
        self._write(tmp_path / "000001.event.json", 1)
        (tmp_path / "garbage.event.json").write_text("not json", encoding="utf-8")
        names = [p.name for p in _event_paths_in_seq_order(tmp_path)]
        assert names == ["000001.event.json", "garbage.event.json"]


class TestReadEventsNumericOrder:
    def test_replay_order_matches_numeric_seq(self, tmp_path, monkeypatch):
        """read_events 的返回顺序必须是数值序，否则重放会产生错误状态。"""
        from data_modules import ssot_enforcer

        log_dir = tmp_path / ".story-system" / "events"
        log_dir.mkdir(parents=True)
        for seq in (999999, 1000000, 1000001):
            (log_dir / f"{seq:06d}.event.json").write_text(json.dumps({
                "event_id": f"e{seq}", "event_type": "chapter_status_changed",
                "chapter": 1, "subject": "", "payload": {"status": f"s{seq}"},
            }), encoding="utf-8")

        events = ssot_enforcer.read_events(tmp_path)
        seqs = [int(e["event_id"][1:]) for e in events]
        assert seqs == sorted(seqs), f"read_events 返回了字典序：{seqs}"
