#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""歧义信号必须真正传到 `disambiguation_result.pending`。

## 断裂的链路

`observer_settler.settle()` 已经能算出同名歧义（`e0b3918` + `61fd6c2`）：

    result["ambiguous_mentions"] = [
        {"mention": "萧炎", "candidates": ["xiaoyan_a", "xiaoyan_b"],
         "entity_type": "角色"},
    ]

它经 CLI 写进 `extraction_result.json`。而 `artifact_validator._policy_issues()`
对 `disambiguation_result` 的判据是：

    pending = payload.get("pending") or []
    if pending:
        issues.append(_issue(ERROR_PENDING_DISAMBIGUATION, "error",
                             f"仍有 {len(pending)} 个待消歧项", ...))

`pending` 非空 → error → **阻断提交**（这是正确的 fail-closed）。

但 `disambiguation_result.json` 由 **data-agent 自己按 LLM 自评置信度**生成
（`data-agent.md:93`：「置信度>0.8 自动采用，0.5-0.8 采用+warning，<0.5 标记
待人工」）。代码侧算出的 `ambiguous_mentions` **从未传给它**。

## 后果

`ambiguous_mentions` 现在是一个**只写不读**的信号：

- `settle()` 算出歧义、写进 extraction_result.json、打一条 warning
- data-agent 不看它，仍按自己的置信度判断
- 两个实体都叫「萧炎」时，settler 静默取字典序最小者，而 `pending` 为空
- 提交照常通过，**错误的实体绑定被固化为事实**

也就是说：修了「检测」，但没修「处置」。

## 本文件钉住

代码侧要提供一条**不依赖 LLM 判断**的通道，把 `ambiguous_mentions` 转成
`disambiguation_result.pending` 条目，让既有的 fail-closed 策略真正生效。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.artifact_validator import validate_artifact_payload  # noqa: E402
from data_modules.observer_settler import settle  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    (tmp_path / ".webnovel" / "state.json").write_text(json.dumps({
        "entities_v3": {"角色": {
            "xiaoyan_a": {"entity_type": "角色", "canonical_name": "萧炎"},
            "xiaoyan_b": {"entity_type": "角色", "canonical_name": "萧炎"},
        }},
    }, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def _settle(project: Path, lines: list[str]) -> dict:
    facts = project / "facts.md"
    facts.write_text("## 角色状态变化\n" + "\n".join(lines) + "\n", encoding="utf-8")
    return settle(facts, project, 1)


class TestAmbiguityIsDetected:
    """前提：settler 确实能算出歧义。"""

    def test_detects_duplicate_name(self, project):
        result = _settle(project, ["- 萧炎（entity_id: 未知）：突破到斗师"])
        assert result.get("ambiguous_mentions"), (
            f"settler 没检出歧义：{result.get('ambiguous_mentions')}"
        )

    def test_no_ambiguity_when_unique(self, project):
        (project / ".webnovel" / "state.json").write_text(json.dumps({
            "entities_v3": {"角色": {
                "xiaoyan": {"entity_type": "角色", "canonical_name": "萧炎"},
            }},
        }, ensure_ascii=False), encoding="utf-8")
        result = _settle(project, ["- 萧炎（entity_id: 未知）：突破到斗师"])
        assert not result.get("ambiguous_mentions")


class TestAmbiguityBecomesPending:
    """核心：歧义必须转成 disambiguation_result.pending。"""

    def test_helper_exists(self):
        """需要一个代码侧的转换函数，不依赖 LLM 判断。"""
        from data_modules import observer_settler as mod

        assert hasattr(mod, "ambiguous_to_pending"), (
            "缺少 ambiguous_to_pending()——settler 算出的歧义没有通往 "
            "disambiguation_result.pending 的代码通道，data-agent 只能靠 "
            "LLM 自评置信度，那个信号等于白算。"
        )

    def test_converts_to_pending_entries(self, project):
        from data_modules.observer_settler import ambiguous_to_pending

        result = _settle(project, ["- 萧炎（entity_id: 未知）：突破到斗师"])
        pending = ambiguous_to_pending(result.get("ambiguous_mentions"))
        assert pending, "歧义没有转成 pending 条目"
        entry = pending[0]
        assert "萧炎" in json.dumps(entry, ensure_ascii=False)
        assert "xiaoyan_a" in json.dumps(entry, ensure_ascii=False)

    def test_pending_blocks_commit(self, project):
        """端到端：歧义转成 pending 后，既有 fail-closed 策略应阻断提交。"""
        from data_modules.observer_settler import ambiguous_to_pending

        result = _settle(project, ["- 萧炎（entity_id: 未知）：突破到斗师"])
        pending = ambiguous_to_pending(result.get("ambiguous_mentions"))

        report = validate_artifact_payload(
            "disambiguation_result", {"pending": pending})
        assert not report["ok"], (
            "同名歧义存在时 disambiguation_result 仍被判为 ok——"
            "错误的实体绑定会被固化为事实"
        )
        assert any("消歧" in e["message"] for e in report["errors"])

    def test_no_pending_when_no_ambiguity(self):
        from data_modules.observer_settler import ambiguous_to_pending

        assert ambiguous_to_pending(None) == []
        assert ambiguous_to_pending([]) == []

    def test_empty_pending_does_not_block(self):
        """没有歧义时 pending 为空，不该阻断。"""
        report = validate_artifact_payload(
            "disambiguation_result", {"pending": []})
        assert report["ok"]


class TestPendingEntryShape:
    """pending 条目要含 agent 复核所需的信息。"""

    def test_has_mention_and_candidates(self, project):
        from data_modules.observer_settler import ambiguous_to_pending

        result = _settle(project, ["- 萧炎（entity_id: 未知）：突破到斗师"])
        entry = ambiguous_to_pending(result.get("ambiguous_mentions"))[0]

        assert entry.get("mention") == "萧炎"
        assert set(entry.get("candidates") or []) == {"xiaoyan_a", "xiaoyan_b"}
        assert entry.get("entity_type") == "角色"

    def test_has_reason(self, project):
        from data_modules.observer_settler import ambiguous_to_pending

        result = _settle(project, ["- 萧炎（entity_id: 未知）：突破到斗师"])
        entry = ambiguous_to_pending(result.get("ambiguous_mentions"))[0]
        blob = json.dumps(entry, ensure_ascii=False)
        assert any(k in blob for k in ("歧义", "多个", "同名", "无法确定")), (
            f"pending 条目没说清为什么待消歧：{blob}"
        )

    def test_confidence_is_low(self, project):
        """歧义项的置信度应低于自动采用阈值（0.8）。"""
        from data_modules.observer_settler import ambiguous_to_pending

        result = _settle(project, ["- 萧炎（entity_id: 未知）：突破到斗师"])
        entry = ambiguous_to_pending(result.get("ambiguous_mentions"))[0]
        if "confidence" in entry:
            assert float(entry["confidence"]) < 0.8, (
                f"歧义项置信度 {entry['confidence']} 仍高于自动采用阈值"
            )
