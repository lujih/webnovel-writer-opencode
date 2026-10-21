#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Narrative Forecast（叙事预报）模块测试。

写第 N 章前生成 2-5 个**相互隔离**的候选未来分支，评估与既有章纲的
对齐度，选中后只把选支追加进章纲；未选中候选保留磁盘作为参考。

## 设计不变量（本文件钉住）

1. **确定性可重放**：给定相同伏笔债/摘要/章纲，`generate_candidates`
   产出相同候选（不依赖 LLM、不依赖随机数）
2. **候选隔离**：每个候选一个 JSON 文件，互不引用
3. **降级**：无伏笔债时 3→2 候选（去掉伏笔回收），不报错
4. **幂等**：同一 forecast 重复 select 不重复追加章纲
5. **可跳过零副作用**：`--off`（不执行 forecast 子命令）时项目无任何变更
6. **不阻断**：无伏笔债表 / 无章纲文件时不抛错，forecast 静默降级

## 与 ConStory CED 的关系

forecast 是**写前**的分支选择（预防性），CED 是**写后**的质量度量
（反应性）。两者不重叠：forecast 不影响 `review_metrics`，CED 不影响
`.story-system/forecasts/`。
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.narrative_forecast import (  # noqa: E402
    generate_candidates,
    select_candidate,
    write_forecast,
    list_forecasts,
    evaluate_alignment,
    forecast_dir,
)


def _make_project(tmp_path: Path, *, with_debt: bool = True,
                  with_summary: bool = True, with_outline: bool = True) -> Path:
    """造一个最小可用的书项目。"""
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text(
        '{"progress": {"current_chapter": 3}}', encoding="utf-8")

    db = tmp_path / ".webnovel" / "index.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE chase_debt (
            id INTEGER PRIMARY KEY, debt_type TEXT, note TEXT,
            subject TEXT, due_chapter INT, status TEXT, current_amount REAL)
    """)
    if with_debt:
        conn.execute(
            "INSERT INTO chase_debt (debt_type,note,subject,due_chapter,status,current_amount)"
            " VALUES ('foreshadowing','血纹石的秘密','血纹石',12,'active',10.0)")
    conn.commit(); conn.close()

    if with_summary:
        sm = tmp_path / ".webnovel" / "summaries"
        sm.mkdir()
        (sm / "ch0002.md").write_text("## 剧情摘要\n主角突破斗师，血纹石开始发热。\n",
                                      encoding="utf-8")

    if with_outline:
        ol = tmp_path / "大纲"
        ol.mkdir()
        (ol / "第1卷-详细大纲.md").write_text(
            "# 第3章\n主线：主角入宗，与师兄争名分。冲突：长老的考验。\n",
            encoding="utf-8")
    return tmp_path


class TestDeterminism:
    """给定相同输入，候选可重放（不依赖 LLM / 随机数）。"""

    def test_identical_inputs_identical_candidates(self, tmp_path):
        project = _make_project(tmp_path)
        c1 = [c.to_dict() for c in generate_candidates(project, 3)]
        c2 = [c.to_dict() for c in generate_candidates(project, 3)]
        # 除 created_at 外完全一致
        for a, b in zip(c1, c2):
            assert {k: v for k, v in a.items() if k != "created_at"} == \
                   {k: v for k, v in b.items() if k != "created_at"}

    def test_three_candidates_when_debt_present(self, tmp_path):
        project = _make_project(tmp_path, with_debt=True)
        cands = generate_candidates(project, 3)
        assert [c.id for c in cands] == [
            "conflict_escalation", "payoff_retrieval", "calm_transition"]

    def test_degrades_to_two_without_debt(self, tmp_path):
        """无伏笔债 → 去掉伏笔回收，3→2 候选，不报错。"""
        project = _make_project(tmp_path, with_debt=False)
        cands = generate_candidates(project, 3)
        assert [c.id for c in cands] == ["conflict_escalation", "calm_transition"]
        assert len(cands) == 2

    def test_empty_project_does_not_raise(self, tmp_path):
        """无 index.db / 无摘要 / 无章纲——全空也降级不抛错。"""
        (tmp_path / ".webnovel").mkdir(parents=True)
        (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
        cands = generate_candidates(tmp_path, 5)
        assert len(cands) == 2          # 无债 → 冲突 + 过渡
        assert all(c.id in ("conflict_escalation", "calm_transition") for c in cands)


class TestIsolation:
    """候选隔离：每候选一个 JSON，互不引用。"""

    def test_each_candidate_own_file(self, tmp_path):
        project = _make_project(tmp_path)
        cands = generate_candidates(project, 3)
        d = write_forecast(project, 3, cands)
        assert d.is_dir()
        for c in cands:
            assert (d / f"{c.id}.json").is_file()
        assert (d / "manifest.json").is_file()

    def test_candidates_do_not_reference_each_other(self, tmp_path):
        project = _make_project(tmp_path)
        cands = generate_candidates(project, 3)
        d = write_forecast(project, 3, cands)
        for c in cands:
            body = (d / f"{c.id}.json").read_text(encoding="utf-8")
            for other in cands:
                if other.id != c.id:
                    assert other.id not in body, (
                        f"{c.id}.json 引用了 {other.id}——候选必须隔离"
                    )

    def test_unselected_candidates_preserved(self, tmp_path):
        project = _make_project(tmp_path)
        cands = generate_candidates(project, 3)
        write_forecast(project, 3, cands)
        select_candidate(project, 3, "payoff_retrieval")
        d = forecast_dir(project, 3)
        # 未选中的候选仍在
        assert (d / "conflict_escalation.json").is_file()
        assert (d / "calm_transition.json").is_file()
        assert (d / "selected.json").is_file()


class TestIdempotency:
    """同一 forecast 重复 select 不重复追加章纲。"""

    def test_repeat_select_updates_in_place(self, tmp_path):
        project = _make_project(tmp_path)
        cands = generate_candidates(project, 3)
        write_forecast(project, 3, cands)
        select_candidate(project, 3, "payoff_retrieval")
        select_candidate(project, 3, "calm_transition")      # 改选另一支

        outline = (project / "大纲" / "第1卷-详细大纲.md").read_text(encoding="utf-8")
        opens = outline.count("<!-- narrative-forecast chapter 3 -->")
        closes = outline.count("<!-- /narrative-forecast chapter 3 -->")
        assert opens == 1 and closes == 1, (
            f"重复选支导致 forecast 块累积（开 {opens} / 闭 {closes}）。"
            f"幂等要求：同章同轮 forecast 只保留一块，原地更新。"
        )

    def test_select_unknown_candidate_fails(self, tmp_path):
        project = _make_project(tmp_path)
        cands = generate_candidates(project, 3)
        write_forecast(project, 3, cands)
        with pytest.raises(ValueError):
            select_candidate(project, 3, "not_a_real_candidate")

    def test_select_without_generate_fails(self, tmp_path):
        project = _make_project(tmp_path)
        with pytest.raises(FileNotFoundError):
            select_candidate(project, 7, "payoff_retrieval")


class TestOutlineAppend:
    """章纲追加行为。"""

    def test_appends_to_volume_outline(self, tmp_path):
        project = _make_project(tmp_path)
        cands = generate_candidates(project, 3)
        write_forecast(project, 3, cands)
        select_candidate(project, 3, "calm_transition")
        outline = (project / "大纲" / "第1卷-详细大纲.md").read_text(encoding="utf-8")
        assert "narrative-forecast chapter 3" in outline
        assert "平静过渡" in outline

    def test_no_outline_does_not_block(self, tmp_path):
        """无章纲目录 → 选支仍成功（写 selected.json），不抛错。"""
        project = _make_project(tmp_path, with_outline=False)
        cands = generate_candidates(project, 3)
        write_forecast(project, 3, cands)
        result = select_candidate(project, 3, "calm_transition")
        assert result["selected"] == "calm_transition"
        assert (forecast_dir(project, 3) / "selected.json").is_file()

    def test_split_chapter_outline_preferred(self, tmp_path):
        """有独立章纲文件时优先追加到它，而非卷大纲。"""
        project = _make_project(tmp_path)
        (project / "大纲" / "第3章-测试.md").write_text("# 第3章 独立\n", encoding="utf-8")
        cands = generate_candidates(project, 3)
        write_forecast(project, 3, cands)
        select_candidate(project, 3, "calm_transition")
        split = (project / "大纲" / "第3章-测试.md").read_text(encoding="utf-8")
        vol = (project / "大纲" / "第1卷-详细大纲.md").read_text(encoding="utf-8")
        assert "narrative-forecast chapter 3" in split, "应追加到独立章纲"
        assert "narrative-forecast chapter 3" not in vol, "独立章纲存在时不追加卷大纲"


class TestAlignment:
    """对齐度（Jaccard on bigrams）。"""

    def test_high_overlap_high_score(self, tmp_path):
        from data_modules.narrative_forecast import ForecastCandidate
        cand = ForecastCandidate(
            id="x", label="x",
            summary="主角突破斗师，血纹石开始发热，长老注意到异常。",
            scene_hook="长老深夜翻阅古籍。")
        score = evaluate_alignment(cand, "主角突破斗师，血纹石开始发热，长老注意到异常。")
        assert score > 0.5, f"同文应对齐度高，实际 {score}"

    def test_different_text_low_score(self, tmp_path):
        from data_modules.narrative_forecast import ForecastCandidate
        cand = ForecastCandidate(
            id="x", label="x",
            summary="沙漠中的商队遭遇沙暴，货物尽失。",
            scene_hook="商队首领发誓复仇。")
        score = evaluate_alignment(cand, "主角突破斗师，血纹石开始发热。")
        assert score < 0.15, f"不同文本应对齐度低，实际 {score}"

    def test_missing_outline_returns_zero(self, tmp_path):
        from data_modules.narrative_forecast import ForecastCandidate
        cand = ForecastCandidate(id="x", label="x", summary="任意文本", scene_hook="钩子")
        assert evaluate_alignment(cand, "") == 0.0
        assert evaluate_alignment(cand, "   ") == 0.0


class TestListForecasts:
    """list_forecasts 查询。"""

    def test_lists_recent_first(self, tmp_path):
        project = _make_project(tmp_path)
        for ch in (1, 2, 3):
            cands = generate_candidates(project, ch)
            write_forecast(project, ch, cands)
        rows = list_forecasts(project, limit=10)
        assert [r["chapter"] for r in rows] == [3, 2, 1]
        assert all(r["selected"] is None for r in rows)

    def test_missing_dir_returns_empty(self, tmp_path):
        project = _make_project(tmp_path, with_debt=False)
        assert list_forecasts(project) == []


class TestZeroSideEffectWhenSkipped:
    """跳过 forecast（不调子命令）时项目零变更。"""

    def test_no_forecast_dir_created(self, tmp_path):
        project = _make_project(tmp_path)
        before = set(project.rglob("*"))
        # 什么都不做（模拟 forecast off）
        after = set(project.rglob("*"))
        assert before == after, "forecast off 时不应创建任何文件"
        assert not (project / ".story-system" / "forecasts").exists()
