"""protagonist_state 在 rebuild 中的真实契约（第4轮核实项）。

审查报告称「回放把 protagonist_state 写成扁平点键，破坏形状」。**核实结论：
该路径经 rebuild 不可达**——回放从 `_empty_state()` 起步，而那里
`protagonist_state` 恒为 `{}`，于是镜像条件 `ps.get("name") == ent.get("name")`
永不成立。真实行为是：rebuild **不推进** protagonist_state，而是通过
`_DICT_MERGE_FIELDS` 把它整体保留为 rebuild 之前的快照。

本文件把「不可达」这一事实钉死，防止有人照着不成立的结论去改；同时钉住
形状正确性——若将来 `_empty_state()` 开始预填主角身份，扁平键写法会立刻
污染 state.json。
"""
import json
from pathlib import Path

from data_modules.ssot_enforcer import (
    _set_dotted,
    publish_event,
    rebuild_projections,
    rebuild_state_json,
)


def _seed(root: Path, protagonist: dict) -> None:
    (root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (root / ".webnovel" / "state.json").write_text(json.dumps({
        "progress": {"current_chapter": 0, "chapter_status": {}},
        "protagonist_state": protagonist,
    }, ensure_ascii=False), encoding="utf-8")


def _state(root: Path) -> dict:
    return json.loads((root / ".webnovel" / "state.json").read_text(encoding="utf-8"))


class TestSetDotted:
    def test_dotted_path_becomes_nested(self):
        target = {}
        _set_dotted(target, "power.realm", "练气五层")
        assert target == {"power": {"realm": "练气五层"}}, "点号路径未展开成嵌套"

    def test_plain_key_stays_flat(self):
        target = {}
        _set_dotted(target, "name", "陈升")
        assert target == {"name": "陈升"}

    def test_deep_path_creates_intermediate_dicts(self):
        target = {}
        _set_dotted(target, "a.b.c", 1)
        assert target == {"a": {"b": {"c": 1}}}

    def test_preserves_sibling_keys(self):
        target = {"power": {"layer": 3}}
        _set_dotted(target, "power.realm", "筑基")
        assert target == {"power": {"layer": 3, "realm": "筑基"}}

    def test_matches_writer_shape(self):
        """必须与 StateProjectionWriter._set_path 产出完全一致的形状。"""
        from data_modules.state_projection_writer import StateProjectionWriter

        writer_out, replay_out = {}, {}
        StateProjectionWriter._set_path(writer_out, "power.realm", "筑基")
        _set_dotted(replay_out, "power.realm", "筑基")
        assert writer_out == replay_out


class TestProtagonistStateSurvivesRebuild:
    def test_rebuild_preserves_protagonist_state_wholesale(self, tmp_path):
        """rebuild 不推进 protagonist_state，而是原样保留既有快照。"""
        original = {
            "name": "陈升",
            "entity_id": "chen_sheng",
            "power": {"realm": "凡人", "layer": 1},
            "location": {"current": "乱坟岗"},
        }
        _seed(tmp_path, original)
        publish_event(tmp_path, "state_changed", {
            "entity_id": "chen_sheng", "entity_name": "陈升",
            "field": "power.realm", "new": "练气五层",
        }, chapter=1)

        rebuild_projections(tmp_path)
        assert _state(tmp_path)["protagonist_state"] == original

    def test_replay_leaves_protagonist_state_empty(self, tmp_path):
        """裸 replay 不产出 protagonist_state —— 镜像分支不可达的事实记录。"""
        _seed(tmp_path, {"name": "陈升", "entity_id": "chen_sheng", "power": {"realm": "凡人"}})
        publish_event(tmp_path, "state_changed", {
            "entity_id": "chen_sheng", "entity_name": "陈升",
            "field": "power.realm", "new": "练气五层",
        }, chapter=1)

        assert rebuild_state_json(tmp_path)["protagonist_state"] == {}, (
            "回放开始产出 protagonist_state 了——_empty_state 的前提已变，"
            "请重新核实镜像分支的形状与可达性"
        )

    def test_no_flat_dotted_keys_ever_appear(self, tmp_path):
        """无论走哪条路，state.json 里都不允许出现 'x.y' 这样的扁平键。"""
        _seed(tmp_path, {"name": "陈升", "entity_id": "chen_sheng", "power": {"realm": "凡人"}})
        publish_event(tmp_path, "state_changed", {
            "entity_id": "chen_sheng", "entity_name": "陈升",
            "field": "power.realm", "new": "练气五层",
        }, chapter=1)
        publish_event(tmp_path, "power_breakthrough", {
            "entity_id": "chen_sheng", "entity_name": "陈升", "new_realm": "筑基",
        }, chapter=2)

        rebuild_projections(tmp_path)
        ps = _state(tmp_path)["protagonist_state"]
        flat = [k for k in ps if "." in k]
        assert not flat, f"protagonist_state 出现扁平点键: {flat}"
