"""rebuild 字段保留回归测试（覆盖第4轮审查报出的 P0）。

与 test_ssot_rebuild_preserve.py 的分工：那个文件守「已列入 _NON_EVENT_FIELDS
的顶层字段不能被冲掉」；本文件守**白名单机制本身的漏洞**——

1. progress 的非事件子字段（total_words/current_volume/volumes_*）没有任何事件
   类型承载，而 _empty_state() 只播种 3 个键，且 progress 既不在
   _NON_EVENT_FIELDS 也不在 _DICT_MERGE_FIELDS ⇒ 回放后被整体覆盖。
2. progress.chapter_status 里「增量写手已推进、事件日志还没有」的章节同理丢失。
3. plot_threads 在回放中已被创建 ⇒ `k not in merged` 恒假 ⇒ active_threads
   永远不会被回填（该机制的证伪用例：新增顶层字段无法靠白名单活下来）。
4. relationships 被 _empty_state 播种成 list，而真实项目是 dict（update_state.py
   按角色名写 dict）⇒ rebuild 造成类型翻转，status_reporter 的
   `relationships.get("allies")` 随之 AttributeError。
"""
import json

import pytest

from data_modules.ssot_enforcer import publish_event, rebuild_projections


def _read_state(root):
    return json.loads((root / ".webnovel" / "state.json").read_text(encoding="utf-8"))


@pytest.fixture
def project(tmp_path):
    """事件日志只推进到第 1 章，state.json 却带着更完整的增量投影。"""
    publish_event(tmp_path, "chapter_status_changed", {"status": "committed"}, chapter=1)
    existing = {
        "progress": {
            "current_chapter": 1,
            "chapter_status": {"1": "chapter_committed", "2": "chapter_committed"},
            "last_updated": "",
            # 下面四个字段没有任何事件类型承载，rebuild 后必须仍在
            "total_words": 97793,
            "current_volume": 3,
            "volumes_completed": [1, 2],
            "volumes_planned": 12,
        },
        "plot_threads": {
            "foreshadowing": [{"content": "三年之约", "status": "open"}],
            # active_threads 没有事件类型承载，rebuild 后必须仍在
            "active_threads": [{"id": "t1", "title": "主线：复仇", "status": "active"}],
        },
        # 真实项目里 relationships 是 dict（按角色名分组），rebuild 不得翻转成 list
        "relationships": {
            "chen_sheng": {"allies": ["su_wan"], "enemies": ["wang_bo"], "neutral": []}
        },
    }
    (tmp_path / ".webnovel").mkdir(exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    return tmp_path


def test_rebuild_preserves_progress_non_event_subfields(project):
    """P0：total_words/current_volume/volumes_* 无事件承载，rebuild 不得擦除。"""
    rebuild_projections(project)
    progress = _read_state(project)["progress"]

    assert progress["total_words"] == 97793
    assert progress["current_volume"] == 3
    assert progress["volumes_completed"] == [1, 2]
    assert progress["volumes_planned"] == 12


def test_rebuild_merges_progress_chapter_status(project):
    """事件日志推进到第 1 章，但增量写手已记到第 2 章——回放不得丢掉第 2 章。

    这是 rebuild 的既定语义（既有测试断言 current_chapter 由事件日志决定），
    但 chapter_status 是逐章合并而非整体覆盖。
    """
    rebuild_projections(project)
    status = _read_state(project)["progress"]["chapter_status"]

    assert status.get("1") == "chapter_committed"
    assert status.get("2") == "chapter_committed", "增量已提交的章节被 rebuild 抹掉"


def test_rebuild_preserves_plot_threads_active_threads(project):
    """P0：plot_threads 在回放中已存在，故回填被跳过，active_threads 被蒸发。"""
    rebuild_projections(project)
    plot_threads = _read_state(project)["plot_threads"]

    assert plot_threads.get("active_threads"), "plot_threads.active_threads 被 rebuild 抹掉"
    assert plot_threads["active_threads"][0]["id"] == "t1"


def test_rebuild_preserves_event_driven_plot_threads_foreshadowing(project):
    """保留 active_threads 的同时，foreshadowing 仍须完全由事件驱动。

    防「修保留逻辑时把回放也一并短路」这一类回归：项目 fixture 里预置的
    "三年之约" 没有任何 open_loop_* 事件支撑，按既定语义应当被回放结果取代。
    """
    publish_event(project, "open_loop_created",
                  {"content": "血色荒漠的钥匙", "tier": "核心", "target_chapter": 40},
                  chapter=1)
    rebuild_projections(project)
    plot_threads = _read_state(project)["plot_threads"]
    contents = [row["content"] for row in plot_threads["foreshadowing"]]

    assert contents == ["血色荒漠的钥匙"], "foreshadowing 应完全由事件日志决定"
    # 同一字段里的 active_threads 仍须从既有 state.json 回填
    assert plot_threads["active_threads"][0]["id"] == "t1"


def test_rebuild_keeps_relationships_as_dict(project):
    """P0：rebuild 曾把 dict 形状的 relationships 翻成 list，消费者随之崩溃。"""
    rebuild_projections(project)
    relationships = _read_state(project)["relationships"]

    assert isinstance(relationships, dict), (
        f"relationships 被翻成 {type(relationships).__name__}，"
        "status_reporter 的 relationships.get('allies') 会 AttributeError"
    )
    assert relationships["chen_sheng"]["allies"] == ["su_wan"]
