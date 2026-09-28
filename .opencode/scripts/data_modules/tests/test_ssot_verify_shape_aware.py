"""verify_consistency 不得跨形状比较计数（第4轮 SSOT 域 P1 误报）。

真实项目的 ``relationships`` 是按角色/关系名分组的 **dict**（``update_state.py``
按角色名写），而 ``rebuild_state_json`` 按事件产出的是**边 list**。旧代码
``len(actual or [])`` vs ``len(expected or [])`` 于是拿「角色数」去比「边数」，
12 个角色 / 5 条边就会恒定报 drift，且 ``cmd_ssot`` 退出码 1。

危害方向与「失败报成功」相反但同样有害：健康项目被判为漂移，而文档给出的
补救办法正是 ``ssot rebuild`` ——而 rebuild 恰恰是破坏数据的那一步。
"""
import json

from data_modules.ssot_enforcer import publish_event, verify_consistency


def _state(root):
    return json.loads((root / ".webnovel" / "state.json").read_text(encoding="utf-8"))


def _warnings(root):
    return [d for d in verify_consistency(root) if d["severity"] != "info"]


def test_dict_relationships_vs_event_list_is_not_reported_as_drift(tmp_path):
    """state 是 dict、事件回放出 list 时，计数不可比，不得报 drift。"""
    (tmp_path / ".webnovel").mkdir(exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(json.dumps({
        "progress": {"current_chapter": 0, "chapter_status": {}},
        "relationships": {
            "chen_sheng": {"allies": ["su_wan"], "enemies": [], "neutral": []},
            "su_wan": {"allies": [], "enemies": [], "neutral": ["chen_sheng"]},
            "wang_bo": {"allies": [], "enemies": ["chen_sheng"], "neutral": []},
        },
    }, ensure_ascii=False), encoding="utf-8")
    publish_event(tmp_path, "chapter_status_changed", {"status": "committed"}, chapter=1)

    fields = [d.get("field") for d in _warnings(tmp_path)]
    assert "relationships" not in fields, (
        f"dict 形状的 relationships 被拿边数比较，误报 drift: {_warnings(tmp_path)}"
    )


def test_same_shape_mismatch_is_still_reported(tmp_path):
    """两侧都是 list 且数量确实不同时，仍须报漂移（不能把检查一并关掉）。

    注意 rebuild_state_json 对这些字段总是产出 list，所以「同形状」在实践中
    就是 list vs list；state 侧写成 dict 时会被跳过（见上一个用例）。
    """
    (tmp_path / ".webnovel").mkdir(exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(json.dumps({
        "progress": {"current_chapter": 0, "chapter_status": {}},
        "artifacts": [],  # 事件日志会投影出 1 个 → 0 vs 1 是真实漂移
    }, ensure_ascii=False), encoding="utf-8")
    publish_event(tmp_path, "artifact_obtained",
                  {"artifact_id": "sword", "name": "长剑", "owner": "chen_sheng"}, chapter=1)
    publish_event(tmp_path, "chapter_status_changed", {"status": "committed"}, chapter=1)

    fields = [d.get("field") for d in _warnings(tmp_path)]
    assert "artifacts" in fields, "同形状下的真实数量漂移必须仍被检出"


def test_matching_list_counts_reports_nothing(tmp_path):
    """数量一致时不得误报——否则 verify 会永久红。"""
    (tmp_path / ".webnovel").mkdir(exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(json.dumps({
        "progress": {"current_chapter": 1, "chapter_status": {"1": "committed"}},
        "artifacts": [{"artifact_id": "sword"}],
    }, ensure_ascii=False), encoding="utf-8")
    publish_event(tmp_path, "artifact_obtained",
                  {"artifact_id": "sword", "name": "长剑", "owner": "chen_sheng"}, chapter=1)
    publish_event(tmp_path, "chapter_status_changed", {"status": "committed"}, chapter=1)

    assert not _warnings(tmp_path), f"数量一致却报漂移: {_warnings(tmp_path)}"


def test_state_dict_shapes_are_skipped_not_faulted(tmp_path):
    """rebuild 恒产出 list，故 state 侧为 dict 时一律跳过比较（不误报）。

    这是本次修复的目标形态：真实项目的 relationships 就是 dict。
    """
    (tmp_path / ".webnovel").mkdir(exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(json.dumps({
        "progress": {"current_chapter": 0, "chapter_status": {}},
        "artifacts": {"a1": {}, "a2": {}, "a3": {}},
        "world_rules": {"r1": {}},
    }, ensure_ascii=False), encoding="utf-8")
    publish_event(tmp_path, "artifact_obtained",
                  {"artifact_id": "sword", "name": "长剑", "owner": "chen_sheng"}, chapter=1)
    publish_event(tmp_path, "chapter_status_changed", {"status": "committed"}, chapter=1)

    fields = [d.get("field") for d in _warnings(tmp_path)]
    assert "artifacts" not in fields
    assert "world_rules" not in fields
