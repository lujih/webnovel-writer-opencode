"""`entities_v3` 形状契约：权威形状是嵌套，读取方对两种形状都要能用。

## 缺陷背景

`state.json` 里的 `entities_v3` 有两种历史形状：

    嵌套（权威）: {"角色": {"xiaoyan": {...}}, "地点": {...}, ...}
    扁平（缺陷）: {"xiaoyan": {...}, "wutan": {...}, ...}

嵌套是权威的——`StateManager.add_entity()`、`sql_state_manager.
export_to_entities_v3_format()`、`migrate_state_to_sqlite.py` 三处生产/消费
方都按 `entities_v3[entity_type][entity_id]` 读写，`ENTITY_TYPES` 那五个中文
类型名就是第一层键。

扁平是 `ssot_enforcer.rebuild_state_json()` 早前 `state.setdefault(
"entities_v3", {})[eid] = {...}` 直接写出来的意外产物。它导致两个问题：

1. **健康项目被判定为数据漂移（假阳性）**。`verify_consistency()` 拿两边的
   顶层键集比较：嵌套态的第一层是类型名、扁平态是实体 id，恒不相等。真书
   `凡尘之舞` 上实测报出 `entities_v3 0 vs 4`。而文档给出的补救办法正是
   `ssot rebuild`，偏偏 rebuild 写出的是另一种形状——用户越修越乱。
2. **迁移会把实体类型写错（真阳性，不可逆）**。`migrate_state_to_sqlite.py`
   原先 `for entity_type, entities in entities_v3.items()`，读到扁平态时会把
   实体 id 当作类型名写进 `index.db`。

## 本文件的契约

- rebuild 写出**嵌套**形状（且对既有扁平/混合输入先归一）；
- `verify_consistency()` 跨形状比较**实体 id 集合**，且 state 侧缺失该字段
  （v5.1 起实体存 index.db，`save_state()` 会 pop 掉它）不算漂移；
- 所有读取方（renderer / observer_settler / migrate）对两种形状都能正确工作。
"""
from __future__ import annotations

import json

import pytest

from data_modules.entity_index_utils import (
    ENTITY_TYPES,
    detect_shape,
    flatten_entities,
    normalize_entities,
)
from data_modules.ssot_enforcer import (
    publish_event,
    rebuild_state_json,
    verify_consistency,
)


def _write_state(root, state):
    (root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (root / ".webnovel" / "state.json").write_text(
        json.dumps(state, ensure_ascii=False), encoding="utf-8"
    )


def _read_state(root):
    return json.loads((root / ".webnovel" / "state.json").read_text(encoding="utf-8"))


def _entity_drifts(root):
    return [d for d in verify_consistency(root) if d.get("field") == "entities_v3"]


# ── 工具模块 ─────────────────────────────────────────────────────────


class TestShapeDetection:
    def test_nested_and_flat_are_distinguished(self):
        assert detect_shape({"角色": {"a": {}}}) == "nested"
        assert detect_shape({"a": {"name": "甲", "entity_type": "角色"}}) == "flat"
        assert detect_shape({}) == "empty"
        assert detect_shape([]) == "invalid"
        assert detect_shape(None) == "invalid"

    def test_mixed_shape_is_treated_as_nested(self):
        """混合态本身就是需要被 rebuild 修掉的脏数据，按嵌套处理以免再降级。"""
        mixed = {"角色": {"a": {"name": "甲"}}, "b": {"name": "乙", "entity_type": "地点"}}
        assert detect_shape(mixed) == "nested"


class TestFlatten:
    def test_nested_is_flattened_with_inferred_type(self):
        flat = flatten_entities({
            "角色": {"xiaoyan": {"name": "萧炎"}},
            "地点": {"wutan": {"name": "乌坦城"}},
        })
        assert set(flat) == {"xiaoyan", "wutan"}
        assert flat["xiaoyan"]["entity_type"] == "角色"
        assert flat["wutan"]["entity_type"] == "地点"

    def test_nested_explicit_type_is_not_overwritten(self):
        flat = flatten_entities({"角色": {"a": {"entity_type": "物品"}}})
        assert flat["a"]["entity_type"] == "物品"

    def test_flat_passes_through(self):
        flat = flatten_entities({"a": {"name": "甲", "entity_type": "地点"}})
        assert flat["a"]["entity_type"] == "地点"

    def test_flat_without_type_defaults_to_character(self):
        assert flatten_entities({"a": {"name": "甲"}})["a"]["entity_type"] == "角色"

    @pytest.mark.parametrize("bad", [None, [], "x", 3])
    def test_non_dict_input_is_empty(self, bad):
        assert flatten_entities(bad) == {}

    def test_non_dict_records_are_skipped(self):
        plain = flatten_entities({"角色": {"a": "not-a-dict"}})
        raw = flatten_entities({"a": "not-a-dict"})
        assert plain == {} and raw == {}


class TestNormalize:
    def test_flat_becomes_nested_and_reports_migration(self):
        nested, migrated = normalize_entities({
            "xiaoyan": {"name": "萧炎", "entity_type": "角色"},
            "wutan": {"name": "乌坦城", "entity_type": "地点"},
        })
        assert migrated is True
        assert "xiaoyan" in nested["角色"]
        assert "wutan" in nested["地点"]

    def test_nested_is_idempotent(self):
        src = {"角色": {"xiaoyan": {"name": "萧炎"}}}
        nested, migrated = normalize_entities(src)
        assert migrated is False
        assert nested["角色"]["xiaoyan"]["name"] == "萧炎"

    def test_always_returns_all_entity_type_buckets(self):
        nested, _ = normalize_entities({})
        assert set(nested) == set(ENTITY_TYPES)


# ── rebuild 写出的形状 ───────────────────────────────────────────────


class TestRebuildWritesNested:
    def test_entity_created_lands_in_type_bucket(self, tmp_path):
        publish_event(tmp_path, "entity_created", {
            "entity_id": "xiaoyan", "entity_name": "萧炎", "entity_type": "角色",
        }, chapter=1)
        state = rebuild_state_json(tmp_path)
        assert "xiaoyan" in state["entities_v3"]["角色"]
        assert state["entities_v3"]["角色"]["xiaoyan"]["name"] == "萧炎"
        # 扁平键不得残留
        assert "xiaoyan" not in state["entities_v3"]

    def test_state_change_and_breakthrough_stay_in_same_bucket(self, tmp_path):
        publish_event(tmp_path, "entity_created", {
            "entity_id": "xiaoyan", "entity_name": "萧炎", "entity_type": "角色",
        }, chapter=1)
        publish_event(tmp_path, "character_state_changed", {
            "entity_id": "xiaoyan", "field": "physical_state", "new": "内伤",
        }, chapter=2)
        publish_event(tmp_path, "power_breakthrough", {
            "entity_id": "xiaoyan", "new_realm": "斗者",
        }, chapter=3)
        state = rebuild_state_json(tmp_path)
        ent = state["entities_v3"]["角色"]["xiaoyan"]
        assert ent["current_state"]["physical_state"] == "内伤"
        assert ent["current_state"]["realm"] == "斗者"

    def test_unknown_type_does_not_create_a_phantom_bucket_key(self, tmp_path):
        """payload 里出现非规范 entity_type 时收敛到已知类型桶。"""
        publish_event(tmp_path, "entity_created", {
            "entity_id": "mystery", "entity_name": "神秘人", "entity_type": "未知类型",
        }, chapter=1)
        state = rebuild_state_json(tmp_path)
        flat = flatten_entities(state["entities_v3"])
        assert "mystery" in flat
        assert set(state["entities_v3"]) <= set(ENTITY_TYPES)

    def test_empty_state_has_all_buckets(self):
        from data_modules.ssot_enforcer import _empty_state
        assert set(_empty_state()["entities_v3"]) == set(ENTITY_TYPES)

    def test_rebuild_over_flat_state_json_normalizes_it(self, tmp_path):
        """既有 state.json 是扁平脏数据时，rebuild 之后必须变成嵌套。

        这条同时钉住 rebuild_projections 的落盘路径：它先 merge 非事件字段
        再写盘，若 merge 把既有扁平形状带回来，state.json 依旧会是脏的。
        """
        from data_modules.ssot_enforcer import rebuild_projections

        _write_state(tmp_path, {
            "progress": {"current_chapter": 1, "chapter_status": {}},
            "entities_v3": {"legacy_flat": {"name": "旧实体", "entity_type": "角色"}},
        })
        publish_event(tmp_path, "entity_created", {
            "entity_id": "xiaoyan", "entity_name": "萧炎", "entity_type": "角色",
        }, chapter=1)
        rebuild_projections(tmp_path)

        saved = _read_state(tmp_path)
        assert detect_shape(saved.get("entities_v3")) == "nested", saved.get("entities_v3")


# ── verify_consistency 跨形状 ────────────────────────────────────────


class TestVerifyIsShapeAware:
    def test_flat_state_does_not_look_like_drift(self, tmp_path):
        """state 侧是旧扁平形状、事件日志一致 —— 不得报漂移。"""
        _write_state(tmp_path, {
            "progress": {"current_chapter": 1, "chapter_status": {"1": "chapter_committed"}},
            "entities_v3": {"xiaoyan": {"name": "萧炎", "entity_type": "角色"}},
        })
        publish_event(tmp_path, "entity_created", {
            "entity_id": "xiaoyan", "entity_name": "萧炎", "entity_type": "角色",
        }, chapter=1)
        publish_event(tmp_path, "chapter_status_changed", {"status": "committed"}, chapter=1)

        assert _entity_drifts(tmp_path) == [], _entity_drifts(tmp_path)

    def test_nested_state_does_not_look_like_drift(self, tmp_path):
        _write_state(tmp_path, {
            "progress": {"current_chapter": 1, "chapter_status": {"1": "chapter_committed"}},
            "entities_v3": {"角色": {"xiaoyan": {"name": "萧炎"}}},
        })
        publish_event(tmp_path, "entity_created", {
            "entity_id": "xiaoyan", "entity_name": "萧炎", "entity_type": "角色",
        }, chapter=1)
        publish_event(tmp_path, "chapter_status_changed", {"status": "committed"}, chapter=1)

        assert _entity_drifts(tmp_path) == [], _entity_drifts(tmp_path)

    def test_absent_field_is_info_not_warning(self, tmp_path):
        """v5.1 起实体存 index.db，save_state 会 pop 掉 entities_v3。

        因此「事件日志有实体、state.json 没有该字段」是设计如此，报 warning
        会让 `ssot verify` 在**每一本正常运行的书**上恒定 rc=1。
        """
        _write_state(tmp_path, {
            "progress": {"current_chapter": 1, "chapter_status": {"1": "chapter_committed"}},
        })
        publish_event(tmp_path, "entity_created", {
            "entity_id": "xiaoyan", "entity_name": "萧炎", "entity_type": "角色",
        }, chapter=1)
        publish_event(tmp_path, "chapter_status_changed", {"status": "committed"}, chapter=1)

        drifts = _entity_drifts(tmp_path)
        assert [d["severity"] for d in drifts] == ["info"], drifts

    def test_genuine_missing_entity_still_warns(self, tmp_path):
        """state 侧确有该字段、却是另一个实体 —— 真漂移不能被静默。"""
        _write_state(tmp_path, {
            "progress": {"current_chapter": 1, "chapter_status": {"1": "chapter_committed"}},
            "entities_v3": {"角色": {"someone_else": {"name": "别人"}}},
        })
        publish_event(tmp_path, "entity_created", {
            "entity_id": "xiaoyan", "entity_name": "萧炎", "entity_type": "角色",
        }, chapter=1)
        publish_event(tmp_path, "chapter_status_changed", {"status": "committed"}, chapter=1)

        drifts = _entity_drifts(tmp_path)
        assert [d["severity"] for d in drifts] == ["warning"], drifts
        assert drifts[0]["missing_entities"] == ["xiaoyan"], drifts
        assert "xiaoyan" in drifts[0]["detail"]


# ── 读取方兼容性 ────────────────────────────────────────────────────


class TestReadersAcceptBothShapes:
    @pytest.mark.parametrize("entities", [
        {"xiaoyan": {"name": "萧炎", "entity_type": "角色",
                     "current_state": {"realm": "斗者"}}},
        {"角色": {"xiaoyan": {"name": "萧炎",
                             "current_state": {"realm": "斗者"}}}},
    ])
    def test_renderer_shows_entity_and_realm(self, tmp_path, entities):
        from data_modules.state_projection_renderer import (
            _render_power_system,
            _render_world_state,
        )

        state = {"entities_v3": entities, "world_rules": [], "protagonist_state": {}}
        world = _render_world_state(state, tmp_path)
        power = _render_power_system(state, tmp_path)
        assert "萧炎" in world, world
        assert "斗者" in power, power

    @pytest.mark.parametrize("entities", [
        {"xiaoyan": {"name": "萧炎", "entity_type": "角色"}},
        {"角色": {"xiaoyan": {"name": "萧炎"}}},
    ])
    def test_settler_resolves_alias_to_id_for_both_shapes(self, tmp_path, entities):
        from data_modules.observer_settler import _load_known_entities

        _write_state(tmp_path, {"entities_v3": entities})
        known = _load_known_entities(tmp_path)
        assert "xiaoyan" in known, known
        assert known["xiaoyan"]["name"] == "萧炎"

    def test_migrate_does_not_treat_entity_id_as_type(self, tmp_path):
        """扁平态迁移进 SQLite 时，实体的 type 必须来自记录本身。"""
        from data_modules.entity_index_utils import flatten_entities as _flat

        flat = _flat({"xiaoyan": {"name": "萧炎", "entity_type": "物品"}})
        assert flat["xiaoyan"]["entity_type"] == "物品"
        assert "xiaoyan" not in ENTITY_TYPES
