#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""同名歧义 → `disambiguation_result.pending` 的接线测试。

审计背景：`observer_settler.ambiguous_to_pending()` 早就实现了「不依赖 LLM
判断」的转换通道，但**没有任何生产调用点**——它只在测试里被调用。真实链路是
``settler 写 ambiguous_mentions → data-agent 按 prompt 自评 → disambiguation_result.json``，
LLM 不自觉消费时 `pending` 恒空，`build_commit` 的消歧门禁（pending 非空则
rejected）形同虚设：两个实体同名时 settler 静默取字典序最小者，**错误的实体
绑定被固化为事实**。

本文件钉住：代码侧信号必须无条件并进 pending，且合并是只增不减、可重复执行。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from data_modules.chapter_commit_service import (
    ChapterCommitService,
    merge_code_detected_pending,
)


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text(
        json.dumps({"schema_version": "5.1", "progress": {}}), encoding="utf-8")
    return tmp_path


def _ambiguous(name: str = "萧炎") -> dict:
    return {
        "mention": name,
        "candidates": ["xiaoyan_a", "xiaoyan_b"],
        "entity_type": "character",
    }


class TestMergeCodeDetectedPending:
    def test_adds_detected_entries_to_empty_pending(self):
        result = merge_code_detected_pending(
            {"pending": []},
            {"ambiguous_mentions": [_ambiguous()]},
        )
        assert len(result["pending"]) == 1
        entry = result["pending"][0]
        assert entry["mention"] == "萧炎"
        assert entry["candidates"] == ["xiaoyan_a", "xiaoyan_b"]
        assert entry["source"] == "observer_settler.ambiguous_mentions"

    def test_does_not_drop_llm_pending(self):
        llm_entry = {"mention": "药老", "candidates": ["yaolao"], "confidence": 0.3}
        result = merge_code_detected_pending(
            {"pending": [llm_entry]},
            {"ambiguous_mentions": [_ambiguous()]},
        )
        mentions = [p["mention"] for p in result["pending"]]
        assert mentions == ["药老", "萧炎"], "LLM 已报的条目必须保留（只增不减）"

    def test_is_idempotent(self):
        extraction = {"ambiguous_mentions": [_ambiguous()]}
        once = merge_code_detected_pending({"pending": []}, extraction)
        twice = merge_code_detected_pending(once, extraction)
        assert len(twice["pending"]) == 1, "同一章重复合并不能叠加"

    def test_no_ambiguity_leaves_input_untouched(self):
        original = {"pending": [], "notes": "x"}
        result = merge_code_detected_pending(original, {"ambiguous_mentions": []})
        assert result["pending"] == []
        assert result["notes"] == "x"

    def test_single_candidate_is_not_ambiguous(self):
        """只有一个候选时不是歧义——settler 的 `_collect_ambiguous_mentions` 已过滤，
        这里防的是上游把候选列表误写成单个。"""
        result = merge_code_detected_pending(
            {"pending": []},
            {"ambiguous_mentions": [{"mention": "萧炎", "candidates": ["xiaoyan_a"]}]},
        )
        assert result["pending"] == []

    def test_missing_extraction_result_is_tolerated(self):
        result = merge_code_detected_pending({"pending": []}, None)
        assert result["pending"] == []


class TestBuildCommitConsumesTheSignal:
    def test_ambiguous_mentions_reject_commit(self, tmp_path):
        service = ChapterCommitService(_project(tmp_path))
        payload = service.build_commit(
            chapter=3,
            review_result={"blocking_count": 0, "issues": []},
            fulfillment_result={"planned_nodes": [], "covered_nodes": [], "missed_nodes": []},
            disambiguation_result={"pending": []},      # LLM 没报任何歧义
            extraction_result={
                "state_deltas": [], "entity_deltas": [], "accepted_events": [],
                "ambiguous_mentions": [_ambiguous()],
            },
        )
        assert payload["meta"]["status"] == "rejected", (
            "代码检出的同名歧义必须让本章 rejected——这正是「先定事实再写作」"
            "的底线，此前只有 LLM 自觉时这条门禁可以永久不触发"
        )
        assert payload["disambiguation_result"]["pending"][0]["mention"] == "萧炎"

    def test_clean_extraction_keeps_commit_accepted(self, tmp_path):
        service = ChapterCommitService(_project(tmp_path))
        payload = service.build_commit(
            chapter=3,
            review_result={"blocking_count": 0, "issues": []},
            fulfillment_result={"planned_nodes": [], "covered_nodes": [], "missed_nodes": []},
            disambiguation_result={"pending": []},
            extraction_result={
                "state_deltas": [], "entity_deltas": [], "accepted_events": [],
                "ambiguous_mentions": [],
            },
        )
        assert payload["meta"]["status"] == "accepted"
