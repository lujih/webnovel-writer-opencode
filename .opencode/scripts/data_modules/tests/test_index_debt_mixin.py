#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`IndexDebtMixin` 的追读债计算测试。

五个 index mixin 里，`index_entity_mixin` / `index_reading_mixin` /
`index_chapter_mixin` / `index_observability_mixin` **零测试引用**，
`index_debt_mixin` 只有一个文件间接提到。它们是 `index_manager`（1435 行）
的实际逻辑承载者。

本文件锁定追读债的**计算正确性**——这是唯一有数值语义、错了会累积放大
的部分。

## 实测缺陷：跳过章节会丢失全部利息

`accrue_interest(current_chapter)` 用 `current_amount * interest_rate`
计息，并且只在 `debt_events` 里按**传入的那个章节号**去重：

    A: 逐章调用 ch2..ch10  →  余额 235.79
    B: 只调用 ch10          →  余额 110.00     ← 跳过 8 章，只算 1 次

同一本金、同一章节区间，两条路径结果差了 125.79。真实使用里
`accrue-interest` 是**手工 CLI 命令**（`index_manager.py:1353`），
从没接入 `chapter-commit` 自动流程——作者隔几章才跑一次是常态，
每次跑都只计一次息，追读债的"复利威慑"实际不存在。

本文件钉住：利息必须按**经过的章节数**累积，与调用频率无关。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.config import DataModulesConfig  # noqa: E402
from data_modules.index_manager import ChaseDebtMeta, IndexManager  # noqa: E402


@pytest.fixture
def manager(tmp_path: Path) -> IndexManager:
    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    return IndexManager(config=cfg)


def _add_debt(im: IndexManager, *, amount: float = 100.0, rate: float = 0.1,
              source: int = 1, due: int = 5) -> int:
    return im.create_debt(ChaseDebtMeta(
        id=None, debt_type="foreshadowing",
        original_amount=amount, current_amount=amount,
        interest_rate=rate, source_chapter=source, due_chapter=due,
        override_contract_id=None, status="active",
    ))


class TestInterestIsChapterBasedNotCallBased:
    """核心不变式：结果只取决于章节区间，不取决于调用了几次。"""

    def test_skipping_chapters_accrues_the_same(self, tmp_path):
        def build(name: str):
            # 两个项目必须用不同目录，否则共用同一个 index.db，债务叠加。
            cfg = DataModulesConfig.from_project_root(tmp_path / name)
            cfg.ensure_dirs()
            return IndexManager(config=cfg)

        # 逐章
        im_a = build("per-chapter")
        _add_debt(im_a)
        for ch in range(2, 11):
            im_a.accrue_interest(ch)
        balance_逐章 = im_a.get_total_debt_balance()

        # 只跑最后一张
        im_b = build("single-shot")
        _add_debt(im_b)
        im_b.accrue_interest(10)
        balance_跳章 = im_b.get_total_debt_balance()

        assert balance_逐章 == pytest.approx(balance_跳章, rel=1e-9), (
            f"逐章调用得 {balance_逐章:.4f}，只调用 ch10 得 {balance_跳章:.4f}。"
            f"同一章节区间应产生同样的利息——否则追读债的数值取决于"
            f"作者什么时候想起来手工跑 accrue-interest。"
        )

    def test_late_first_call_covers_all_elapsed_chapters(self, manager):
        """建债后第一次计息发生在第 10 章，也要补上 ch2..ch10 的 9 章利息。"""
        _add_debt(manager, amount=100.0, rate=0.1, source=1, due=5)
        manager.accrue_interest(10)
        expected = 100.0 * (1.1 ** 9)
        assert manager.get_total_debt_balance() == pytest.approx(expected, rel=1e-9), (
            f"期望 {expected:.4f}（9 章复利），实际 {manager.get_total_debt_balance():.4f}"
        )


class TestIdempotency:
    """同一章重复调用不能重复计息。"""

    def test_same_chapter_twice(self, manager):
        _add_debt(manager)
        manager.accrue_interest(3)
        first = manager.get_total_debt_balance()
        result = manager.accrue_interest(3)
        assert manager.get_total_debt_balance() == pytest.approx(first, rel=1e-12)
        assert result["skipped_already_processed"] == 1

    def test_repeated_calls_do_not_compound(self, manager):
        _add_debt(manager, rate=0.1, source=1)
        for _ in range(5):
            manager.accrue_interest(3)
        once = 100.0 * (1.1 ** 2)      # ch2, ch3
        assert manager.get_total_debt_balance() == pytest.approx(once, rel=1e-9)

    def test_going_backwards_is_not_double_counted(self, manager):
        """先跑 ch5 再跑 ch3：ch3 的利息不该被重复收。"""
        _add_debt(manager)
        manager.accrue_interest(5)
        after_5 = manager.get_total_debt_balance()
        manager.accrue_interest(3)
        assert manager.get_total_debt_balance() == pytest.approx(after_5, rel=1e-12)


class TestStatusTransitions:
    """逾期判定。"""

    def test_becomes_overdue_after_due_chapter(self, manager):
        _add_debt(manager, source=1, due=5)
        manager.accrue_interest(5)
        assert [d["status"] for d in manager.get_active_debts()] == ["active"]

        manager.accrue_interest(6)
        overdue = manager.get_overdue_debts(6)
        assert len(overdue) == 1
        assert overdue[0]["status"] == "overdue"

    def test_overdue_keeps_accruing(self, manager):
        _add_debt(manager, source=1, due=2)
        manager.accrue_interest(3)
        mid = manager.get_total_debt_balance()
        manager.accrue_interest(4)
        assert manager.get_total_debt_balance() > mid, "逾期债应继续计息"


class TestEdgeCases:
    def test_no_debts_is_noop(self, manager):
        result = manager.accrue_interest(10)
        assert result["debts_processed"] == 0
        assert result["total_interest"] == 0.0
        assert manager.get_total_debt_balance() == 0

    def test_zero_interest_rate(self, manager):
        _add_debt(manager, rate=0.0)
        for ch in range(2, 8):
            manager.accrue_interest(ch)
        assert manager.get_total_debt_balance() == pytest.approx(100.0, rel=1e-12)

    def test_chapter_before_source_accrues_nothing(self, manager):
        _add_debt(manager, source=5, due=10)
        manager.accrue_interest(3)
        assert manager.get_total_debt_balance() == pytest.approx(100.0, rel=1e-12), (
            "建债前的章节不该计息"
        )

    def test_partial_payment_reduces_balance(self, manager):
        debt_id = _add_debt(manager)
        manager.accrue_interest(5)
        before = manager.get_total_debt_balance()
        manager.pay_debt(debt_id, 50.0, 6)
        assert manager.get_total_debt_balance() == pytest.approx(before - 50.0, rel=1e-9)

    def test_full_payment_closes_debt(self, manager):
        debt_id = _add_debt(manager)
        manager.accrue_interest(5)
        total = manager.get_total_debt_balance()
        manager.pay_debt(debt_id, total, 6)
        assert manager.get_active_debts() == []
        assert manager.get_total_debt_balance() == pytest.approx(0.0, abs=1e-9)
