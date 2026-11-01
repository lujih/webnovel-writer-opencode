#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端到端黄金路径：写章 → 审查 → 提交 → CED → 叙事预报 → 跨章 flag 注入 → 仲裁。

## 为什么需要这个文件

v3.0.0 的三项新能力（CED / 叙事预报 / flag-triage）各自有独立单测，但
**没有任何单个测试同时碰三者**（`grep -l ced_raw | xargs grep -l 'review_flags'`
为空）。跨功能耦合点恰好落在单测的缝里：

- forecast 选支后那章的 CED 是否正常记录
- 第 N 章的 `review_flags` 是否真的注入第 N+1 章 context、triage 后是否真的消失
- 章号 off-by-one（`chapter - 1` 取上一章）、顺序依赖（投影 → 计息 → flags）

本文件把**真实生产入口**串成一条链路，逐段断言：

    写正文 → review_pipeline.build_review_artifacts（产出 metrics + review_result）
           → ChapterCommitService.build_commit（flag 分层：语义 critical 不拒章）
           → persist_commit（过 precommit gate）
           → save_review_metrics（CED 落库）
           → generate_candidates / select_candidate（第 2 章预报）
           → ContextManager._load_reader_signal(2)（读到第 1 章的 flag）
           → flag_triage confirm（仲裁）
           → 再读 signal（flag 消失）+ commit status 未变

## 三个数据域互不污染的断言

forecast 写 `.story-system/forecasts/`，CED 写 `review_metrics` 表，
flag 写 commit JSON 的 `review_flags`。三者边界被本文件钉住——否则
其中一处越界就会在另一个视图里制造假数据。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules import flag_triage  # noqa: E402
from data_modules.chapter_commit_service import ChapterCommitService  # noqa: E402
from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.index_manager import IndexManager  # noqa: E402
from data_modules.narrative_forecast import (  # noqa: E402
    forecast_dir,
    generate_candidates,
    select_candidate,
    write_forecast,
)
from data_modules.project_phase import INIT_REQUIRED_DIRS, INIT_REQUIRED_FILES  # noqa: E402
from data_modules.artifact_validator import ARTIFACT_FILES  # noqa: E402
from review_pipeline import (  # noqa: E402
    _build_review_metrics_record,
    build_review_artifacts,
)

CHAPTER = 1
NEXT_CHAPTER = 2


# ---------------------------------------------------------------------------
# 项目脚手架
# ---------------------------------------------------------------------------

def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _review_payload(blocking: bool = True) -> dict:
    """审查输出：一条 setting 维度的 critical（解析成功，不是 malformed）。"""
    return {
        "blocking_count": 1 if blocking else 0,
        "issues": (
            [{
                "severity": "critical",
                "category": "setting",
                "description": "境界跨度与第三卷力量体系冲突",
                "location": "正文第 1 章第 3 段",
                "blocking": True,
            }]
            if blocking else []
        ),
        "summary": "整体可读，但存在一处设定冲突。",
    }


def _build_project(tmp_path: Path, *, with_debt: bool = True) -> Path:
    """建出**真的可提交**的项目根（含 CED / forecast / flag 三条链路所需的素材）。"""
    root = tmp_path
    for rel in INIT_REQUIRED_DIRS:
        (root / rel).mkdir(parents=True, exist_ok=True)
    for rel in INIT_REQUIRED_FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(
                json.dumps({"schema_version": "5.1", "progress": {}})
                if rel.endswith(".json") else "placeholder\n",
                encoding="utf-8",
            )

    # 正文：precommit gate 要求目标章存在且非空（≥100 字符）
    chapters = root / "正文"
    chapters.mkdir(parents=True, exist_ok=True)
    (chapters / f"第{CHAPTER:04d}章.md").write_text(
        "主角踏入山门，发现血纹石正在发热。" * 20, encoding="utf-8")

    # 章纲（forecast 选支后要追加到这里）
    outline = root / "大纲"
    outline.mkdir(parents=True, exist_ok=True)
    (outline / f"第{NEXT_CHAPTER}章-山门.md").write_text(
        f"# 第{NEXT_CHAPTER}章 山门\n\n主线：主角应对入门考验。\n", encoding="utf-8")

    # 上一章剧情摘要（forecast 生成候选时读）
    summaries = root / ".webnovel" / "summaries"
    summaries.mkdir(parents=True, exist_ok=True)
    (summaries / f"ch{CHAPTER:04d}.md").write_text(
        "## 剧情摘要\n主角踏入山门，血纹石首次发热。\n", encoding="utf-8")

    # 4 个 commit 产物（precommit gate 的 schema 校验对象）
    review_payload = _review_payload()
    artifacts = {
        "review_result": review_payload,
        "fulfillment_result": {
            "planned_nodes": ["踏入山门"],
            "covered_nodes": ["踏入山门"],
            "missed_nodes": [],
            "extra_nodes": [],
        },
        "disambiguation_result": {"pending": []},
        "extraction_result": {
            "accepted_events": [],
            "state_deltas": [],
            "entity_deltas": [],
        },
    }
    for artifact, filename in ARTIFACT_FILES.items():
        _write_json(root / ".webnovel" / "tmp" / filename, artifacts[artifact])

    # 审查结果原文件（build_review_artifacts 读它）
    review_file = root / ".webnovel" / "tmp" / "reviewer_output.md"
    review_file.write_text(
        "```json\n" + json.dumps(review_payload, ensure_ascii=False) + "\n```\n",
        encoding="utf-8")

    # index.db + 伏笔债（forecast 的 payoff_retrieval 候选依赖它）。
    # 走真实 API `create_simple_debt`：note 落在 debt_events.note，
    # 手搓 `INSERT INTO chase_debt (.., note, ..)` 会建出与生产不符的假 schema。
    config = DataModulesConfig.from_project_root(root)
    config.ensure_dirs()
    manager = IndexManager(config)          # 构造即建表
    if with_debt:
        manager.create_simple_debt(
            debt_type="foreshadowing",
            source_chapter=CHAPTER,
            due_chapter=12,
            note="血纹石为何发热",
            subject="血纹石",
        )

    return root


# ---------------------------------------------------------------------------
# 黄金路径（跑一次，多个测试断言不同侧面）
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def golden(tmp_path_factory):
    """完整跑一遍黄金路径，返回各阶段的中间产物供断言。"""
    root = _build_project(tmp_path_factory.mktemp("golden"))
    config = DataModulesConfig.from_project_root(root)

    # --- 1. 审查 ---------------------------------------------------------
    artifacts = build_review_artifacts(
        root, CHAPTER, root / ".webnovel" / "tmp" / "reviewer_output.md")
    metrics = artifacts["metrics"]

    # --- 2. 提交 ---------------------------------------------------------
    service = ChapterCommitService(root)
    commit = service.build_commit(
        chapter=CHAPTER,
        review_result=artifacts["review_result"],
        fulfillment_result={"planned_nodes": ["踏入山门"], "covered_nodes": ["踏入山门"],
                            "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    commit_path = service.persist_commit(commit)

    # --- 3. CED 落库 -----------------------------------------------------
    manager = IndexManager(config)
    manager.save_review_metrics(_build_review_metrics_record(metrics))

    # --- 4. 叙事预报（第 2 章）-------------------------------------------
    candidates = generate_candidates(root, NEXT_CHAPTER)
    write_forecast(root, NEXT_CHAPTER, candidates)
    select_candidate(root, NEXT_CHAPTER, "payoff_retrieval")

    # --- 5. 跨章 flag 注入 -----------------------------------------------
    from data_modules.context_manager import ContextManager
    context = ContextManager(config)
    signal_before_triage = context._load_reader_signal(NEXT_CHAPTER)

    return {
        "root": root,
        "config": config,
        "artifacts": artifacts,
        "metrics": metrics,
        "commit": commit,
        "commit_path": commit_path,
        "candidates": candidates,
        "signal_before_triage": signal_before_triage,
        "manager": manager,
        "context": context,
    }


class TestCommitStage:
    """第 1 段：语义 critical 不拒章，降级为 flag。"""

    def test_commit_accepted_despite_semantic_critical(self, golden):
        assert golden["commit"]["meta"]["status"] == "accepted", (
            "门禁分层要求：解析成功的 LLM 语义 critical 不得拒章"
        )

    def test_critical_became_review_flag(self, golden):
        flags = golden["commit"].get("review_flags") or []
        assert len(flags) == 1, f"critical 必须降级为 1 条 flag，实际 {len(flags)}"
        assert flags[0]["severity"] == "critical"
        assert flags[0]["category"] == "setting"
        assert flags[0]["triage"] is None, "新 flag 默认待仲裁"

    def test_commit_file_persisted(self, golden):
        assert golden["commit_path"].is_file()
        saved = json.loads(golden["commit_path"].read_text(encoding="utf-8"))
        assert saved["meta"]["status"] == "accepted"
        assert len(saved.get("review_flags") or []) == 1, (
            "flags 必须随 commit 落盘——否则下游 context 注入读不到"
        )


class TestCedStage:
    """第 2 段：CED 三口径计算正确并落库。"""

    def test_chapter_words_positive(self, golden):
        words = golden["metrics"]["chapter_words"]
        assert words > 0, "取不到正文字数 → CED 分母为 0，整条链路失效"

    def test_three_scales_computed(self, golden):
        m = golden["metrics"]
        for key in ("ced_raw", "ced_weighted", "ced_blocking"):
            assert m[key] is not None, f"{key} 为 None——分母有效时必须算出值"
        # 单条 critical：raw=1/w*1e4，weighted=4/w*1e4，blocking=1/w*1e4
        # compute_ced 内部 round(...,4)，故用 abs 容差而非 rel
        w = m["chapter_words"]
        assert m["ced_raw"] == pytest.approx(1 / w * 10000, abs=1e-4)
        assert m["ced_weighted"] == pytest.approx(4 / w * 10000, abs=1e-4)
        assert m["ced_blocking"] == pytest.approx(1 / w * 10000, abs=1e-4)

    def test_metrics_persisted_and_read_back(self, golden):
        rows = golden["manager"].get_recent_review_metrics(5)
        assert len(rows) == 1
        row = rows[0]
        assert row["end_chapter"] == CHAPTER
        assert row["ced_raw"] == pytest.approx(golden["metrics"]["ced_raw"], abs=1e-4)
        assert row["review_parseable"] == 1


class TestForecastStage:
    """第 3 段：预报生成 + 选支 + 幂等追加。"""

    def test_three_candidates_with_debt(self, golden):
        ids = [c.id for c in golden["candidates"]]
        assert ids == ["conflict_escalation", "payoff_retrieval", "calm_transition"], (
            f"有伏笔债时应产出 3 个候选，实际 {ids}"
        )

    def test_selected_branch_appended_to_outline(self, golden):
        outline = golden["root"] / "大纲" / f"第{NEXT_CHAPTER}章-山门.md"
        text = outline.read_text(encoding="utf-8")
        assert f"<!-- narrative-forecast chapter {NEXT_CHAPTER} -->" in text
        assert "伏笔回收" in text, "选中的是 payoff_retrieval，章纲应记「伏笔回收」"

    def test_select_is_idempotent(self, golden):
        outline = golden["root"] / "大纲" / f"第{NEXT_CHAPTER}章-山门.md"
        select_candidate(golden["root"], NEXT_CHAPTER, "payoff_retrieval")
        text = outline.read_text(encoding="utf-8")
        opens = text.count(f"<!-- narrative-forecast chapter {NEXT_CHAPTER} -->")
        assert opens == 1, f"重复选支不得累积 forecast 块（实际 {opens} 个）"

    def test_selected_marker_written(self, golden):
        selected = json.loads(
            (forecast_dir(golden["root"], NEXT_CHAPTER) / "selected.json")
            .read_text(encoding="utf-8"))
        assert selected["candidate_id"] == "payoff_retrieval"


class TestCrossChapterFlagInjection:
    """第 4 段：第 1 章的未仲裁 flag 注入第 2 章 reader signal。"""

    def test_signal_carries_previous_chapter_flag(self, golden):
        flags = golden["signal_before_triage"].get("pending_review_flags") or []
        assert len(flags) == 1, (
            f"第 1 章的未仲裁 flag 应注入第 2 章 signal，实际 {len(flags)} 条"
        )
        assert flags[0]["chapter"] == CHAPTER, "章号必须是上一章（chapter-1），不是当前章"
        assert flags[0]["severity"] == "critical"
        assert "力量体系冲突" in flags[0]["description"]


class TestTriageStage:
    """第 5 段：仲裁后 flag 不再注入，commit status 不变。"""

    def test_confirm_clears_injection(self, golden):
        root = golden["root"]
        sys.argv = ["flag_triage.py", "--project-root", str(root),
                    "confirm", "--chapter", str(CHAPTER), "--index", "0"]
        with pytest.raises(SystemExit) as exc:
            flag_triage.main()
        assert exc.value.code == 0

        saved = json.loads(golden["commit_path"].read_text(encoding="utf-8"))
        assert saved["review_flags"][0]["triage"] == "confirmed"
        # 门禁分层的底线：仲裁只留痕，不改 commit status
        assert saved["meta"]["status"] == "accepted", (
            "confirm 不得把 accepted 改成别的 status——仲裁不是审批"
        )

        signal_after = golden["context"]._load_reader_signal(NEXT_CHAPTER)
        assert not (signal_after.get("pending_review_flags") or []), (
            "已仲裁的 flag 不得再注入警戒段，否则同一问题会反复出现"
        )


class TestDomainIsolation:
    """三个数据域互不越界。"""

    def test_forecast_does_not_touch_review_metrics(self, golden):
        rows = golden["manager"].get_recent_review_metrics(50)
        chapters = {r["end_chapter"] for r in rows}
        assert NEXT_CHAPTER not in chapters, (
            "forecast 是写前的分支选择，不得写 CED/审查指标"
        )

    def test_ced_does_not_touch_forecasts(self, golden):
        d = forecast_dir(golden["root"], CHAPTER)
        assert not d.exists(), (
            "只对第 1 章做了审查+提交、没有做预报，不得凭空生成第 1 章的 forecast 目录"
        )

    def test_flag_does_not_leak_into_forecast_dir(self, golden):
        d = forecast_dir(golden["root"], NEXT_CHAPTER)
        for f in d.glob("*.json"):
            assert "review_flags" not in f.read_text(encoding="utf-8"), (
                f"{f.name} 混进了 review_flags——flag 属于 commit，不属于 forecast"
            )


# ---------------------------------------------------------------------------
# 降级路径（独立项目，避免污染黄金路径）
# ---------------------------------------------------------------------------

class TestDegradationPaths:
    """素材缺失时的降级行为。"""

    def test_no_debt_yields_two_candidates(self, tmp_path):
        root = _build_project(tmp_path, with_debt=False)
        ids = [c.id for c in generate_candidates(root, NEXT_CHAPTER)]
        assert ids == ["conflict_escalation", "calm_transition"], (
            f"无伏笔债应降级为 2 候选，实际 {ids}"
        )

    def test_unparseable_review_is_rejected_not_flagged(self, tmp_path):
        """技术故障（不可解析）仍 fail-closed 拒章，且不得产出语义 flag。"""
        root = _build_project(tmp_path)
        bad = root / ".webnovel" / "tmp" / "bad_review.md"
        bad.write_text("这不是 JSON，模型回了散文。", encoding="utf-8")
        artifacts = build_review_artifacts(root, CHAPTER, bad)
        assert artifacts["metrics"]["review_parseable"] == 0
        assert artifacts["metrics"]["ced_raw"] is None, (
            "解析失败不得产出 CED——否则趋势图出现虚假尖峰"
        )

        commit = ChapterCommitService(root).build_commit(
            chapter=CHAPTER,
            review_result=artifacts["review_result"],
            fulfillment_result={"planned_nodes": [], "covered_nodes": [],
                                "missed_nodes": [], "extra_nodes": []},
            disambiguation_result={"pending": []},
            extraction_result={"state_deltas": [], "entity_deltas": [],
                               "accepted_events": []},
        )
        assert commit["meta"]["status"] == "rejected", "技术故障必须仍拒章"
        assert commit.get("review_flags") == [], (
            "malformed 合成项是技术信号，不得进语义 flags"
        )
