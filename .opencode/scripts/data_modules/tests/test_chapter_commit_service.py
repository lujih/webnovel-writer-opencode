#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import sys
from pathlib import Path

import pytest

from data_modules.artifact_validator import ARTIFACT_FILES
from data_modules.chapter_commit_service import ChapterCommitService
from data_modules.config import DataModulesConfig
from data_modules.index_manager import IndexManager
from data_modules.project_phase import INIT_REQUIRED_DIRS, INIT_REQUIRED_FILES


def _write_min_artifact(path: Path) -> None:
    """写一份能过 schema 的最小产物。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": [],
        "pending": [],
        "blocking_count": 0, "issues": [],
        "state_deltas": [], "entity_deltas": [], "accepted_events": [],
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def project_root(tmp_path):
    """建出**真的可提交**的项目根。

    此前这里只建 `.webnovel/state.json`，因为 precommit gate 从未接入生产
    代码——apply_projections 根本不做阶段/产物检查，残缺项目照样提交。
    门禁接上之后，`init_scaffolded` 阶段与缺失的 4 个 commit 产物都会阻断，
    所以 fixture 必须补齐。这不是"为了过测试而放宽"，而是把 fixture 从
    "只要能跑"改成"符合提交前提"。
    """
    for rel in INIT_REQUIRED_DIRS:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    for rel in INIT_REQUIRED_FILES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(
                json.dumps({"schema_version": "5.1", "progress": {}})
                if rel.endswith(".json") else "placeholder\n",
                encoding="utf-8",
            )
    for filename in ARTIFACT_FILES.values():
        _write_min_artifact(tmp_path / ".webnovel" / "tmp" / filename)
    # precommit 还会要求目标章节文件存在且非空（≥100 字符）。
    # 这些用例提交第 3 章，故备好第 3 章的正文。
    chapters_dir = tmp_path / "正文"
    chapters_dir.mkdir(parents=True, exist_ok=True)
    (chapters_dir / "第0003章.md").write_text("正文占位。" * 40, encoding="utf-8")
    return tmp_path


def test_commit_service_rejects_when_missed_nodes_exist(project_root):
    service = ChapterCommitService(project_root)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": ["发现陷阱"], "missed_nodes": ["发现陷阱"]},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "rejected"


def test_commit_service_accepts_when_all_checks_pass(project_root):
    service = ChapterCommitService(project_root)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": ["发现陷阱"], "covered_nodes": ["发现陷阱"], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "accepted"
    assert "MASTER_SETTING.json" in payload["contract_refs"]["master"]
    assert "volume_001.json" in payload["contract_refs"]["volume"]
    assert "chapter_003.json" in payload["contract_refs"]["chapter"]
    assert payload["outline_snapshot"]["covered_nodes"] == ["发现陷阱"]


def test_commit_service_includes_volume_ref_and_write_fact_provenance(project_root):
    service = ChapterCommitService(project_root)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": ["发现陷阱"], "covered_nodes": ["发现陷阱"], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )

    assert "volume_001.json" in payload["contract_refs"]["volume"]
    assert payload["provenance"]["write_fact_role"] == "chapter_commit"
    assert payload["provenance"]["projection_role"] == "derived_read_models"


def test_chapter_commit_cli_builds_and_persists_commit(project_root, monkeypatch):
    review_path = project_root / "review.json"
    fulfillment_path = project_root / "fulfillment.json"
    disambiguation_path = project_root / "disambiguation.json"
    extraction_path = project_root / "extraction.json"
    review_path.write_text('{"blocking_count": 0}', encoding="utf-8")
    fulfillment_path.write_text(
        '{"planned_nodes": ["发现陷阱"], "covered_nodes": ["发现陷阱"], "missed_nodes": [], "extra_nodes": []}',
        encoding="utf-8",
    )
    disambiguation_path.write_text('{"pending": []}', encoding="utf-8")
    extraction_path.write_text('{"state_deltas": [], "entity_deltas": [], "accepted_events": []}', encoding="utf-8")

    scripts_dir = Path(__file__).resolve().parents[2]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from chapter_commit import main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "chapter_commit",
            "--project-root",
            str(project_root),
            "--chapter",
            "3",
            "--review-result",
            str(review_path),
            "--fulfillment-result",
            str(fulfillment_path),
            "--disambiguation-result",
            str(disambiguation_path),
            "--extraction-result",
            str(extraction_path),
        ],
    )
    main()

    assert (project_root / ".story-system" / "commits" / "chapter_003.commit.json").is_file()


def test_apply_projections_writes_events_and_amend_proposals(project_root):
    service = ChapterCommitService(project_root)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": ["发现陷阱"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={
            "state_deltas": [],
            "entity_deltas": [],
            "summary_text": "",
            "accepted_events": [
                {
                    "event_id": "evt-001",
                    "chapter": 3,
                    "event_type": "world_rule_broken",
                    "subject": "金手指",
                    "payload": {
                        "field": "world_rule",
                        "base_value": "每日一次",
                        "proposed_value": "短时失控突破",
                    },
                }
            ],
        },
    )

    service.apply_projections(payload)

    assert (project_root / ".story-system" / "events" / "chapter_003.events.json").is_file()
    manager = IndexManager(DataModulesConfig.from_project_root(project_root))
    with manager._get_conn() as conn:
        row = conn.execute(
            """
            SELECT record_type, field, override_value, status
            FROM override_contracts
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()

    assert row["record_type"] == "amend_proposal"
    assert row["field"] == "world_rule"
    assert row["override_value"] == "短时失控突破"
    assert row["status"] == "pending"


# ---------------------------------------------------------------------------
# Malformed 输入拒绝测试
# ---------------------------------------------------------------------------

def test_commit_service_flags_llm_critical_as_review_flag(project_root):
    """门禁分层（2026-10）：LLM 报的 critical（解析成功）不再拒章，
    降级为 accepted + review_flags（观察标记，由 flag-triage 人工裁决）。

    修复前：此用例断言 status == rejected——LLM 一次误判的 critical 就杀死
    一章；且 _shape_issue 把纯解析失败也包成 critical 一并阻断。
    """
    service = ChapterCommitService(project_root)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0, "issues": [{"blocking": True, "severity": "critical", "description": "test"}]},
        fulfillment_result={"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "accepted", (
        "LLM 的 critical 是语义判定，可误判——不应自动拒章（inkOS 路线：语义审查不 gate）。"
    )
    flags = payload.get("review_flags") or []
    assert len(flags) == 1 and flags[0]["description"] == "test", (
        "critical 必须降级进 review_flags 留痕，否则问题没有出口"
    )
    assert flags[0]["severity"] == "critical"
    assert flags[0]["triage"] is None, "新 flag 默认待仲裁"


def test_commit_service_rejects_unparseable_review(project_root):
    """技术故障（审查输出不可解析）仍 fail-closed 拒章。

    unparseable 由 `review_parseable=0` 显式声明（review_pipeline._shape_issue
    路径写入）——「无法判断有没有问题」≠「没有问题」。
    """
    service = ChapterCommitService(project_root)
    payload = service.build_commit(
        chapter=3,
        review_result={
            "blocking_count": 1,
            "issues": [{"blocking": True, "severity": "critical",
                        "category": "review_output_malformed",
                        "description": "审查结果不可解析，已阻断提交。"}],
            "review_parseable": 0,
        },
        fulfillment_result={"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "rejected"
    assert payload.get("review_flags") == [], "技术故障信号不得进语义 flags"


def test_commit_service_flags_exclude_malformed_category(project_root):
    """`_shape_issue` 合成的 issue 可能带 original category（被 __post_init__ 降级），
    故用 description 前缀兜底识别技术信号——它不得进 flags。"""
    service = ChapterCommitService(project_root)
    payload = service.build_commit(
        chapter=3,
        review_result={
            "blocking_count": 1,
            "issues": [{"blocking": True, "severity": "critical",
                        "category": "other",  # 降级后的实际值
                        "description": "审查结果不可解析，已阻断提交。"}],
            "review_parseable": 0,
        },
        fulfillment_result={"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload.get("review_flags") == [], (
        "category 被降级成 other 的技术信号仍不得进 flags——靠 description 前缀识别"
    )


def test_commit_service_rejects_disambiguation_with_pending(project_root):
    """disambiguation_result 包含 pending 项时应 rejected。"""
    service = ChapterCommitService(project_root)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": [{"entity": "test"}]},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "rejected"


def test_commit_service_rejects_cbn_missed_nodes(project_root):
    """遗漏 CBN（核心情节点）时应 rejected。"""
    service = ChapterCommitService(project_root)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["发现陷阱"],
            "covered_nodes": [],
            "missed_nodes": [{"type": "CBN", "name": "发现陷阱"}],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "rejected"


def test_commit_service_accepts_cpn_missed_nodes(project_root):
    """遗漏 CPN（推进节点）时应 accepted（非阻断）。"""
    service = ChapterCommitService(project_root)
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={
            "planned_nodes": ["发现陷阱", "试探信息"],
            "covered_nodes": ["发现陷阱"],
            "missed_nodes": [{"type": "CPN", "name": "试探信息"}],
            "extra_nodes": [],
        },
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    assert payload["meta"]["status"] == "accepted"


def test_commit_service_extraction_result_stored_as_nested(project_root):
    """extraction_result 应存储在嵌套结构中。"""
    service = ChapterCommitService(project_root)
    extraction = {
        "state_deltas": [{"entity_id": "x", "field": "realm", "new": "斗者"}],
        "entity_deltas": [],
        "accepted_events": [{"event_type": "test"}],
        "summary_text": "本章摘要",
    }
    payload = service.build_commit(
        chapter=3,
        review_result={"blocking_count": 0},
        fulfillment_result={"planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result=extraction,
    )
    # 嵌套结构
    assert "extraction_result" in payload
    assert payload["extraction_result"]["summary_text"] == "本章摘要"
    assert payload["extraction_result"]["accepted_events"] == [{"event_type": "test"}]
    # 顶层不再有这些字段
    assert "summary_text" not in payload
    assert "accepted_events" not in payload
