#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`style_sampler` 的样本选择：数量契约与排序确定性。

`style_sampler.py`（436 行）决定注入给模型的文风样本——作者积累的优秀片段
经它挑选后进入写作上下文。

## 缺陷一：`select_samples_for_chapter` 请求 N 条却给不足 N 条

    per_type = max(1, max_samples // len(target_types))
    for scene_type in target_types:
        type_samples = self.get_samples_by_type(scene_type, limit=per_type, ...)
        samples.extend(type_samples)
    return samples[:max_samples]

`max_samples // len(target_types)` 是**向下取整**，两类时 `3 // 2 = 1`，
每类只取 1 条，合计 2 条 → 请求 3 条得到 2 条。实测：

    大纲「主角与反派战斗，随后有重要对话」→ 推断出 ['战斗', '对话']
    max_samples=1 → 1 条
    max_samples=2 → 2 条
    max_samples=3 → 2 条   ❌ 请求 3 给 2
    max_samples=4 → 4 条

`max_samples` 是调用方为 token 预算设的上限，"最多 N 条"与"至少尽量给满 N 条"
不矛盾——有余量时应当补足。

## 缺陷二：`ORDER BY score DESC` 无次级键，同分样本永远只取前几条

`get_best_samples()` / `get_samples_by_type()` 都是：

    ORDER BY score DESC LIMIT ?

同分时顺序**由 SQL 标准不保证**（当前 SQLite 恰好按 rowid 返回，属于实现细节）。
实测 20 条同分样本、取 5 条，**每次都是同样那 5 条**（样本00-04），
后 15 条永远选不到——作者积累的 20 个优秀片段实际只用了 5 个。

修复取向：加确定的次级排序键（`score DESC, chapter ASC, id ASC`），
让行为可预测、可测试。至于"要不要轮换以增加多样性"，那是产品决策，
本次只保证**确定性**。

## 本文件钉住

- `select_samples_for_chapter` 在有余量时应尽量给满 `max_samples`
- 同分样本的选取顺序确定（同输入同输出）
- 空库 / 无匹配类型时返回空列表，不崩
- `min_score` 过滤仍然生效
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.style_sampler import StyleSample, StyleSampler  # noqa: E402


@pytest.fixture
def sampler(tmp_path: Path) -> StyleSampler:
    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    return StyleSampler(cfg)


def _add(sampler: StyleSampler, scene_type: str, n: int,
         score: float = 0.9, prefix: str = "") -> None:
    for i in range(n):
        sampler.add_sample(StyleSample(
            id=None, chapter=i + 1, scene_type=scene_type,
            content=f"{prefix or scene_type}{i}", score=score, tags=[],
            created_at="2026-01-01",
        ))


class TestMaxSamplesContract:
    """核心缺陷一：请求 N 条应尽量给满 N 条。"""

    def test_two_types_three_requested(self, sampler):
        _add(sampler, "战斗", 5)
        _add(sampler, "对话", 5)
        outline = "主角与反派战斗，随后有重要对话"
        assert set(sampler._infer_scene_types(outline)) == {"战斗", "对话"}

        picked = sampler.select_samples_for_chapter(outline, max_samples=3)
        assert len(picked) == 3, (
            f"请求 3 条只给了 {len(picked)} 条。"
            f"`max_samples // len(target_types)` 向下取整（3//2=1），"
            f"每类只取 1 条后截断不足。"
        )

    @pytest.mark.parametrize("max_samples", [1, 2, 3, 4, 5])
    def test_various_max_samples(self, sampler, max_samples):
        _add(sampler, "战斗", 10)
        _add(sampler, "对话", 10)
        outline = "战斗与对话"
        picked = sampler.select_samples_for_chapter(outline, max_samples=max_samples)
        assert len(picked) == max_samples, (
            f"max_samples={max_samples} 只给了 {len(picked)} 条"
        )

    def test_never_exceeds_max(self, sampler):
        _add(sampler, "战斗", 10)
        _add(sampler, "对话", 10)
        _add(sampler, "情感", 10)
        outline = "战斗、对话、情感"
        picked = sampler.select_samples_for_chapter(outline, max_samples=3)
        assert len(picked) <= 3

    def test_insufficient_samples_returns_what_exists(self, sampler):
        """库里总共只有 2 条，请求 5 条时给 2 条即可。"""
        _add(sampler, "战斗", 1)
        _add(sampler, "对话", 1)
        picked = sampler.select_samples_for_chapter("战斗与对话", max_samples=5)
        assert len(picked) == 2

    def test_balances_across_types(self, sampler):
        """两类都有样本时，结果应覆盖两类（不能全给第一类）。"""
        _add(sampler, "战斗", 10)
        _add(sampler, "对话", 10)
        picked = sampler.select_samples_for_chapter("战斗与对话", max_samples=4)
        types = {x.scene_type for x in picked}
        assert len(types) == 2, f"只覆盖了 {types}，应覆盖两类"


class TestDeterministicOrdering:
    """核心缺陷二：同分样本的选取必须确定。"""

    def test_same_score_stable_across_calls(self, sampler):
        _add(sampler, "战斗", 20, score=0.8)
        runs = [[x.content for x in sampler.get_best_samples(limit=5)]
                for _ in range(3)]
        assert runs[0] == runs[1] == runs[2], (
            f"同样输入三次调用结果不同：{runs}。ORDER BY score DESC 无次级键，"
            f"同分时顺序由 SQL 实现细节决定。"
        )

    def test_explicit_tiebreak_in_sql(self):
        """静态检查：两个查询都应带确定的次级排序键。

        只看**真正的 SQL 语句行**——SQL 注释（`-- ...`）里引用
        `ORDER BY score DESC` 是说明性的，不该被算作缺陷。
        """
        import inspect

        for method in ("get_best_samples", "get_samples_by_type"):
            src = inspect.getsource(getattr(StyleSampler, method))
            order_lines = [
                line.strip() for line in src.splitlines()
                if line.strip().upper().startswith("ORDER BY")
            ]
            assert order_lines, f"{method} 里没找到 ORDER BY"
            clause = order_lines[0]
            assert clause.count(",") >= 1, (
                f"{method} 的 ORDER BY 只有 score 一个键：{clause!r}。"
                f"同分时顺序不确定，应加 chapter/id 作为次级键。"
            )

    def test_higher_score_wins(self, sampler):
        sampler.add_sample(StyleSample(id=None, chapter=1, scene_type="战斗",
                                       content="低分", score=0.5, tags=[],
                                       created_at="x"))
        sampler.add_sample(StyleSample(id=None, chapter=2, scene_type="战斗",
                                       content="高分", score=0.99, tags=[],
                                       created_at="x"))
        best = sampler.get_best_samples(limit=1)
        assert best[0].content == "高分"


class TestMinScoreFilter:
    """`min_score` 过滤仍生效。"""

    def test_filters_low_scores(self, sampler):
        _add(sampler, "战斗", 3, score=0.5, prefix="低")
        _add(sampler, "战斗", 2, score=0.95, prefix="高")
        picked = sampler.get_samples_by_type("战斗", limit=10, min_score=0.8)
        assert len(picked) == 2
        assert all(x.content.startswith("高") for x in picked)


class TestEdgeCases:
    """空库 / 未知类型不崩。"""

    def test_empty_db(self, sampler):
        assert sampler.get_best_samples(limit=5) == []
        assert sampler.get_samples_by_type("战斗", limit=5) == []

    def test_unknown_scene_type(self, sampler):
        _add(sampler, "战斗", 3)
        assert sampler.get_samples_by_type("不存在的类型", limit=5) == []

    def test_select_on_empty_db(self, sampler):
        assert sampler.select_samples_for_chapter("战斗", max_samples=3) == []

    def test_infer_types_defaults_to_description(self, sampler):
        """无关键词时回落到描述类。"""
        from data_modules.style_sampler import SceneType
        assert sampler._infer_scene_types("毫无关键词的大纲") == [SceneType.DESCRIPTION.value]
