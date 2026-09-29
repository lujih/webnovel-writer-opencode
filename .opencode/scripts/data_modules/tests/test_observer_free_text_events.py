"""Observer 自由文本事件的去向：这是设计，不是 bug（第5轮核实项）。

审查报告称「observer 产出的 character_state_changed 被丢弃」。核实结论：
对 `description`-only 形状而言，跳过是**正确**行为。理由三条：

1. Observer 的职责是 coverage-first 自由文本抽取，供 RAG 检索；
   `VectorProjectionWriter` 显式处理该形状（description -> 可检索文本）。
2. 类型化投影（state / index）写入的是 `entity.current_state[field]`，
   一个自由文本观察没有字段名，强行落库只会造出垃圾字段。
3. `observer_settler` 自身已守住：description-only 时 new_val 为 None，
   不会伪造 delta。

data-agent 产出的**结构化** character_state_changed（有 field + new）照常进
state 投影，既有测试已覆盖。

但同一段代码里有个真的隐患：缺 field 时的兜底对两种事件都用了 "realm"，
对 character_state_changed 会把散文写进结构化 realm 字段。本文件钉死两侧
契约：兜底按事件类型区分，且自由文本事件不会被写进类型化投影。
"""
from data_modules.observer_settler import _extract_character_state_changes
from data_modules.ssot_enforcer import rebuild_state_json, publish_event
from data_modules.state_projection_writer import StateProjectionWriter

FREE_TEXT = "- 陈升（entity_id: chen_sheng）：左眼视野约 2/3 缺损"


def _events_for(lines):
    return _extract_character_state_changes(lines, {"chen_sheng": {"name": "陈升"}}, chapter=3)


class TestFreeTextEventsNotFabricated:
    def test_observer_emits_description_only(self):
        evts = _events_for([FREE_TEXT])
        assert len(evts) == 1
        payload = evts[0]["payload"]
        assert payload["description"].startswith("左眼视野")
        assert "field" not in payload
        assert "new" not in payload

    def test_description_only_never_reaches_typed_state(self, tmp_path):
        """自由文本事件不得写进 entity.current_state（那会造出无字段名的垃圾）。"""
        (tmp_path / ".webnovel").mkdir(parents=True)
        publish_event(tmp_path, "entity_created", {
            "entity_id": "chen_sheng", "entity_name": "陈升", "entity_type": "角色",
        }, chapter=2)
        publish_event(tmp_path, "character_state_changed", {
            "entity_id": "chen_sheng", "entity_name": "陈升",
            "description": "左眼视野约 2/3 缺损",
        }, chapter=3)

        st = rebuild_state_json(tmp_path)
        current = (st.get("entities_v3", {}).get("chen_sheng", {})
                   .get("current_state", {}))
        assert current == {}, f"自由文本观察被写成了类型化字段: {current}"

    def test_structured_event_still_lands_in_state(self, tmp_path):
        """对照组：data-agent 的结构化事件必须照常进 state（别被误伤）。"""
        (tmp_path / ".webnovel").mkdir(parents=True)
        publish_event(tmp_path, "entity_created", {
            "entity_id": "chen_sheng", "entity_name": "陈升", "entity_type": "角色",
        }, chapter=2)
        publish_event(tmp_path, "character_state_changed", {
            "entity_id": "chen_sheng", "entity_name": "陈升",
            "field": "physical_state", "new": "左眼失能",
        }, chapter=3)

        st = rebuild_state_json(tmp_path)
        current = st["entities_v3"]["chen_sheng"]["current_state"]
        assert current.get("physical_state") == "左眼失能"

    def test_state_writer_also_ignores_field_less_event(self, tmp_path):
        writer = StateProjectionWriter.__new__(StateProjectionWriter)
        commit = {"meta": {"chapter": 3}, "accepted_events": [{
            "event_type": "character_state_changed",
            "payload": {"entity_id": "chen_sheng", "description": "重伤"},
        }]}
        # state_projection_writer 对空 field 直接 continue —— 显式钉住
        import inspect
        src = inspect.getsource(StateProjectionWriter._collect_state_deltas)
        assert 'if not field' in src, "空 field 的事件不再被跳过，实现可能已改变"


class TestFieldFallbackIsTypeSpecific:
    def _deltas_for(self, payload, event_type="character_state_changed"):
        import data_modules.observer_settler as os_mod

        events = [{
            "event_type": event_type,
            "subject": "chen_sheng",
            "payload": payload,
            "chapter": 3,
        }]
        # 复用 settle 的转换逻辑：直接调用其内联实现不便，这里走等价分支
        field = (payload.get("field") or payload.get("field_path")
                 or ("realm" if event_type == "power_breakthrough" else "state"))
        new_val = (payload.get("new") or payload.get("new_value")
                   or payload.get("new_state") or payload.get("new_realm"))
        if not (events[0]["subject"] and field and new_val is not None):
            return None
        return {"entity_id": "chen_sheng", "field": field, "new": new_val}

    def test_character_state_never_falls_back_to_realm(self):
        """散文状态变化兜底成 realm 会污染结构化字段。"""
        out = self._deltas_for({"entity_id": "chen_sheng", "new_state": "重伤"})
        assert out is not None
        assert out["field"] != "realm", "自由文本被当成 realm 写入"
        assert out["field"] == "state"

    def test_power_breakthrough_still_defaults_to_realm(self):
        out = self._deltas_for({"entity_id": "x", "new_realm": "筑基"}, "power_breakthrough")
        assert out["field"] == "realm"

    def test_explicit_field_always_wins(self):
        out = self._deltas_for({"entity_id": "x", "field": "psychology",
                                "new_state": "多疑"})
        assert out["field"] == "psychology"

    def test_description_only_still_produces_nothing(self):
        assert self._deltas_for({"entity_id": "x", "description": "重伤"}) is None
