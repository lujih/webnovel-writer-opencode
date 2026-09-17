"""ssot rebuild 非事件字段保护测试（P0：rebuild 不得擦除 init 初始化/审查流水线
写入的顶层字段，如 project_info/chapter_meta/review_checkpoints/world_settings/
strand_tracker，否则用户跑 ssot rebuild 即丢项目元数据）。"""
import json
from pathlib import Path

import pytest

from data_modules.ssot_enforcer import publish_event, rebuild_projections, read_events


@pytest.fixture
def project_with_events_and_meta(tmp_path):
    """构造带事件日志 + 既有非事件字段 state.json 的项目。"""
    publish_event(tmp_path, "chapter_status_changed", {"status": "committed"}, chapter=1)
    publish_event(tmp_path, "entity_created",
                  {"entity_id": "xiao_yan", "entity_type": "角色", "entity_name": "萧炎"},
                  chapter=1)
    publish_event(tmp_path, "open_loop_created",
                  {"content": "三年之约", "tier": "核心", "target_chapter": 30},
                  chapter=1)
    existing = {
        "project_info": {"title": "凡尘之舞", "target_chapters": 1050},
        "chapter_meta": {"1": {"hook": {"type": "仇恨钩"}}},
        "review_checkpoints": [{"chapter": 1, "score": 100}],
        "world_settings": {"power_system": ["修仙", "脑机"]},
        "strand_tracker": {"current_dominant": "quest", "history": [
            {"chapter": 1, "dominant": "quest"}]},
        "entities": {"chen_sheng": {"name": "陈升", "type": "主角"}},
        "entity_state": {"chen_sheng": {"realm": "凡人"}},
        "progress": {"current_chapter": 65},
        "protagonist_state": {"name": "陈升", "entity_id": "chen_sheng"},
    }
    (tmp_path / ".webnovel").mkdir(exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp_path


def test_rebuild_preserves_non_event_top_level_fields(project_with_events_and_meta):
    """rebuild 必须保留 project_info/chapter_meta/review_checkpoints/world_settings/
    strand_tracker/entities/entity_state 等非事件产生的顶层字段。"""
    root = project_with_events_and_meta
    rebuild_projections(root)
    state = json.loads((root / ".webnovel" / "state.json").read_text(encoding="utf-8"))

    for field in ("project_info", "chapter_meta", "review_checkpoints",
                  "world_settings", "strand_tracker", "entities", "entity_state",
                  "protagonist_state"):
        assert field in state, f"rebuild 冲掉了非事件字段 {field}"

    assert state["project_info"]["title"] == "凡尘之舞"
    assert state["chapter_meta"]["1"]["hook"]["type"] == "仇恨钩"
    assert state["review_checkpoints"][0]["chapter"] == 1
    assert state["world_settings"]["power_system"] == ["修仙", "脑机"]
    assert state["strand_tracker"]["current_dominant"] == "quest"
    assert state["entities"]["chen_sheng"]["name"] == "陈升"
    assert state["protagonist_state"]["name"] == "陈升"


def test_rebuild_advances_event_fields(project_with_events_and_meta):
    """rebuild 必须把事件日志产生的字段正常推进（保留+推进二者都要）。"""
    root = project_with_events_and_meta
    rebuild_projections(root)
    state = json.loads((root / ".webnovel" / "state.json").read_text(encoding="utf-8"))

    # progress.current_chapter 由事件日志推进（原 state 里是 65，事件日志只有第 1 章）
    assert state["progress"]["current_chapter"] == 1  # 事件日志决定
    # entities_v3 / plot_threads.foreshadowing 由事件日志产生
    assert "xiao_yan" in state["entities_v3"]
    assert state["plot_threads"]["foreshadowing"][0]["content"] == "三年之约"


def test_rebuild_no_existing_state_json_only_from_events(tmp_path):
    """无既有 state.json 时 rebuild 仅从事件日志生成（不影响新初始化项目）。"""
    publish_event(tmp_path, "chapter_status_changed", {"status": "committed"}, chapter=1)
    rebuild_projections(tmp_path)
    state = json.loads((tmp_path / ".webnovel" / "state.json").read_text(encoding="utf-8"))
    assert state["progress"]["current_chapter"] == 1
    assert "project_info" not in state  # 没有既有 state.json 时不应凭空生成


def test_rebuild_preserves_schema_version(project_with_events_and_meta):
    """schema_version 保留既有值或默认 5.1，rebuild 不丢。"""
    root = project_with_events_and_meta
    rebuild_projections(root)
    state = json.loads((root / ".webnovel" / "state.json").read_text(encoding="utf-8"))
    assert state.get("schema_version") in ("5.1", None)
