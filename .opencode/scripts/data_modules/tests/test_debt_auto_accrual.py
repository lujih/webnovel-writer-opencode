#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""追读债利息应随提交自动推进。

`accrue_interest()` 已修成「按章节数复利，与调用频率无关」，但它**仍然只
由一条手工 CLI 触发**（`index_manager.py` 的 `accrue-interest`）。全仓 grep
确认它从未接入 `chapter-commit`：

    grep -rn "accrue_interest" --include=*.py . | grep -v tests
    → 只有 index_manager.py:1353（CLI 分发）

后果：作者不主动跑命令，追读债就永远不产生利息，`get_overdue_debts()`
永远是空的，dashboard 的伏笔面板看不到任何逾期。这套「追读债」机制在
默认使用路径上等于不存在。

修复后计息本身是对的（`test_index_debt_mixin.py` 已钉住），所以这次要钉的
是**接线**：提交一章后，该章的利息自动记入 `chase_debt`。

## 接线位置的选择

放在 `apply_projections()` 的投影循环**之后**：
- 此时该章的事件已全部写入 SSOT（`publish_event` 已完成）
- 五个投影 writer 已跑完，`chase_debt` 的建债/销债（`_sync_foreshadowing`）
  也已落库——计息看到的是本章最终的债务集合
- 计息失败不能阻断提交（提交本身已成功），只记 warning

## 幂等性

`accrue_interest()` 内部按 `debt_events` 的 `MAX(chapter)` 判断已计到哪一章，
所以重复提交同一章不会重复计息。本文件也钉住这一点。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.artifact_validator import ARTIFACT_FILES  # noqa: E402
from data_modules.chapter_commit_service import ChapterCommitService  # noqa: E402
from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.index_manager import ChaseDebtMeta, IndexManager  # noqa: E402
from data_modules.project_phase import INIT_REQUIRED_DIRS, INIT_REQUIRED_FILES  # noqa: E402


def _min_artifact(path: Path) -> None:
    import json

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "planned_nodes": [], "covered_nodes": [], "missed_nodes": [], "extra_nodes": [],
        "pending": [], "blocking_count": 0, "issues": [],
        "state_deltas": [], "entity_deltas": [], "accepted_events": [],
    }, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """一个可通过 precommit gate 的项目（gate 已接入，见 708d8cf）。"""
    for rel in INIT_REQUIRED_DIRS:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)
    for rel in INIT_REQUIRED_FILES:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(
                '{"progress": {"current_chapter": 0}}' if rel.endswith(".json")
                else "placeholder\n", encoding="utf-8")
    for filename in ARTIFACT_FILES.values():
        _min_artifact(tmp_path / ".webnovel" / "tmp" / filename)
    chapters = tmp_path / "正文"
    chapters.mkdir(parents=True, exist_ok=True)
    for n in (1, 2, 3, 4, 5):
        (chapters / f"第{n:04d}章.md").write_text("正文占位。" * 40, encoding="utf-8")
    return tmp_path


def _add_debt(project: Path, *, source_chapter: int = 1, due: int = 3,
              amount: float = 100.0, rate: float = 0.1) -> int:
    cfg = DataModulesConfig.from_project_root(project)
    im = IndexManager(config=cfg)
    return im.create_debt(ChaseDebtMeta(
        id=None, debt_type="foreshadowing",
        original_amount=amount, current_amount=amount,
        interest_rate=rate, source_chapter=source_chapter, due_chapter=due,
        override_contract_id=None, status="active",
    ))


def _balance(project: Path) -> float:
    return IndexManager(config=DataModulesConfig.from_project_root(project)).get_total_debt_balance()


def _commit(project: Path, chapter: int) -> dict:
    service = ChapterCommitService(project)
    payload = service.build_commit(
        chapter=chapter,
        review_result={"blocking_count": 0, "issues": []},
        fulfillment_result={"planned_nodes": [], "covered_nodes": [],
                            "missed_nodes": [], "extra_nodes": []},
        disambiguation_result={"pending": []},
        extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
    )
    return service.apply_projections(payload)


class TestInterestAdvancesWithCommits:
    """核心：提交章节应自动推进利息，不需要手工跑 CLI。"""

    def test_balance_grows_after_commit(self, project):
        _add_debt(project, source_chapter=1, due=3)
        before = _balance(project)
        assert before == pytest.approx(100.0)

        _commit(project, 2)

        after = _balance(project)
        assert after > before, (
            f"提交第 2 章后余额没变（{before} → {after}）。"
            f"accrue_interest 仍未接入 chapter-commit，"
            f"追读债在默认使用路径上等于不存在。"
        )

    def test_accrues_by_chapter_not_by_commit_count(self, project):
        """提交到第 3 章应累计 ch2、ch3 两期。"""
        _add_debt(project, source_chapter=1, due=3)
        _commit(project, 2)
        _commit(project, 3)
        expected = 100.0 * (1.1 ** 2)
        assert _balance(project) == pytest.approx(expected, rel=1e-9), (
            f"期望 {expected:.4f}（2 期），实际 {_balance(project):.4f}"
        )

    def test_overdue_becomes_visible(self, project):
        """提交越过到期章后，get_overdue_debts 应能看到该债。"""
        _add_debt(project, source_chapter=1, due=2)
        _commit(project, 3)

        im = IndexManager(config=DataModulesConfig.from_project_root(project))
        overdue = im.get_overdue_debts(3)
        assert overdue, (
            "提交到第 3 章（due=2）后仍无逾期债——计息没跑，"
            "状态永远不会从 active 转 overdue"
        )

    def test_zero_rate_debt_stays_flat(self, project):
        """零利率债不该因提交而变。"""
        _add_debt(project, source_chapter=1, due=3, rate=0.0)
        _commit(project, 3)
        assert _balance(project) == pytest.approx(100.0, rel=1e-12)


class TestIdempotency:
    """重复提交同一章不能重复计息。"""

    def test_same_chapter_twice(self, project):
        _add_debt(project, source_chapter=1, due=3)
        _commit(project, 2)
        first = _balance(project)

        # 同一章再提交一次（重试场景）
        _commit(project, 2)
        assert _balance(project) == pytest.approx(first, rel=1e-12), (
            f"同一章重复提交导致重复计息：{first} → {_balance(project)}"
        )

    def test_out_of_order_commits_do_not_double_count(self, project):
        """先提交 ch3 再提交 ch2：不应把 ch2 再收一遍。"""
        _add_debt(project, source_chapter=1, due=3)
        _commit(project, 3)
        after3 = _balance(project)
        _commit(project, 2)
        assert _balance(project) == pytest.approx(after3, rel=1e-12)


class TestFailureDoesNotBlockCommit:
    """计息是附带动作，失败不能拖垮提交。"""

    def test_commit_succeeds_when_accrual_raises(self, project, monkeypatch):
        import data_modules.index_debt_mixin as debt_mod

        _add_debt(project, source_chapter=1, due=3)
        monkeypatch.setattr(
            debt_mod.IndexDebtMixin, "accrue_interest",
            lambda self, ch: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        # 不应抛出
        result = _commit(project, 2)
        assert result["meta"]["status"] == "accepted"
        assert result["projection_status"]["state"] in ("done", "skipped")

    def test_no_debts_is_noop(self, project):
        """没有债时提交应正常完成。"""
        result = _commit(project, 2)
        assert result["meta"]["status"] == "accepted"
        assert _balance(project) == pytest.approx(0.0)


class TestRejectedCommitDoesNotAccrue:
    """被拒章节不推进进度，也不该推进利息。"""

    def test_rejected_commit_does_not_accrue(self, project):
        _add_debt(project, source_chapter=1, due=3)
        before = _balance(project)

        service = ChapterCommitService(project)
        # 门禁分层后：拒章的技术原因是「审查输出不可解析」（unparseable），
        # 而非 LLM 语义 critical（后者已降级为 review_flags，不拒章）。
        payload = service.build_commit(
            chapter=2,
            review_result={
                "issues": [{"severity": "critical", "blocking": True,
                            "category": "review_output_malformed",
                            "description": "审查结果不可解析，已阻断提交。"}],
                "review_parseable": 0,
            },
            fulfillment_result={"planned_nodes": [], "covered_nodes": [],
                                "missed_nodes": [], "extra_nodes": []},
            disambiguation_result={"pending": []},
            extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
        )
        assert payload["meta"]["status"] == "rejected"
        service.apply_projections(payload)

        assert _balance(project) == pytest.approx(before, rel=1e-12), (
            "被拒章节推进了利息——它并没有真正『写出去』"
        )

    def test_accepted_commit_with_llm_flags_does_accrue(self, project):
        """门禁分层后：LLM 报 critical 但解析成功 → accepted（带 flags），
        accepted 章节正常推进利息。"""
        _add_debt(project, source_chapter=1, due=5)
        before = _balance(project)

        service = ChapterCommitService(project)
        payload = service.build_commit(
            chapter=2,
            review_result={"issues": [{"severity": "critical", "blocking": True,
                                       "description": "设定矛盾"}]},
            fulfillment_result={"planned_nodes": [], "covered_nodes": [],
                                "missed_nodes": [], "extra_nodes": []},
            disambiguation_result={"pending": []},
            extraction_result={"state_deltas": [], "entity_deltas": [], "accepted_events": []},
        )
        assert payload["meta"]["status"] == "accepted", (
            "LLM critical 不再是拒章原因——它可误判，应降级为 flags 放行"
        )
        assert len(payload.get("review_flags", [])) == 1
        service.apply_projections(payload)

        assert _balance(project) != before, (
            "accepted 章节（即使带 flags）必须正常推进利息——它确实写出去了"
        )
