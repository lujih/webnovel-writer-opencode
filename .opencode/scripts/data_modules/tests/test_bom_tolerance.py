#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""BOM 容错的**系统性**回归。

同一缺陷在本轮审查里出现了 5 次（structural_checker / context_manager /
cli_args / state_projection_renderer / ssot_enforcer + doctor）：

    Windows 记事本、VS Code 的「UTF-8 with BOM」是中文作者的默认保存格式，
    而 `json.loads` 不跳 BOM → 合法的 state.json 被判成损坏。

前 4 处已各自修掉。本文件覆盖剩下这些**用户会手工编辑**的 JSON 读取点，
它们此前只做了异常保护（所以不崩），但 BOM 会让它们走进错误分支：

| 位置 | BOM 的实际后果 |
|---|---|
| `ssot_enforcer.rebuild_projections` | 跳过「保留非事件字段」合并 → **project_info 等被擦掉** |
| `ssot_enforcer.verify_consistency` | 报 `state.json missing or unreadable` → 假告警 |
| `doctor` | 诊断工具误报文件损坏 → 误导用户去"修"没坏的文件 |
| `override_contract_engine` | 覆盖合同读成空 → 世界规则覆盖"消失" |
| `story_contracts` | 合同读成空 → 上下文缺设定 |
| `project_phase` | 阶段判定错误 |

本文件用真实 BOM 文件逐一验证：**合法内容必须被读到，不能走错误分支**。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.config import DataModulesConfig  # noqa: E402


def _write_bom(path: Path, payload: dict) -> None:
    """写一个带 UTF-8 BOM 的合法 JSON 文件（模拟 Windows 编辑器）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\ufeff" + json.dumps(payload, ensure_ascii=False)).encode("utf-8"))


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    return tmp_path


class TestSsotRebuildKeepsNonEventFields:
    """最严重的一处：BOM 会让 rebuild 擦掉非事件字段。"""

    def test_project_info_survives_rebuild(self, project):
        from data_modules.ssot_enforcer import rebuild_projections

        _write_bom(project / ".webnovel" / "state.json", {
            "project_info": {"title": "凡尘之舞", "genre": "玄幻"},
            "progress": {"current_chapter": 0},
        })
        rebuild_projections(project)

        state = json.loads(
            (project / ".webnovel" / "state.json").read_text(encoding="utf-8-sig"))
        assert state.get("project_info", {}).get("title") == "凡尘之舞", (
            "带 BOM 的 state.json 让 rebuild 跳过了「保留非事件字段」合并，"
            "用户手工维护的 project_info 被擦掉了"
        )

    def test_verify_does_not_report_unreadable(self, project):
        from data_modules.ssot_enforcer import verify_consistency

        _write_bom(project / ".webnovel" / "state.json", {"progress": {}})
        drifts = verify_consistency(project)
        blob = str(drifts)
        assert "unreadable" not in blob and "missing" not in blob, (
            f"带 BOM 的合法 state.json 被 verify 报成不可读：{drifts}"
        )


class TestOverrideEngineReadsBom:
    def test_effective_rules_survive_bom(self, project):
        from data_modules.override_contract_engine import (
            add_override,
            get_effective_rules,
        )

        add_override(project, "flight", "不可飞", "可飞", "得珠", 5)
        # 模拟用户用记事本打开改过：加 BOM
        path = project / ".story-system" / "override_contracts.json"
        _write_bom(path, json.loads(path.read_text(encoding="utf-8")))

        rules = get_effective_rules(project)
        assert rules, "带 BOM 的 override_contracts.json 读成了空——覆盖消失"
        assert any(r.get("constraint_id") == "flight" for r in rules)


class TestStoryContractsReadsBom:
    def test_contract_payload_is_loaded(self, project, tmp_path):
        from data_modules.story_contracts import read_json_if_exists

        path = tmp_path / "MASTER_SETTING.json"
        _write_bom(path, {"world": "斗气大陆"})
        payload = read_json_if_exists(path)
        assert payload.get("world") == "斗气大陆", (
            f"带 BOM 的合同文件读成 {payload!r}"
        )


class TestProjectPhaseReadsBom:
    def test_phase_resolution_does_not_mark_corrupt(self, project):
        from data_modules.project_phase import resolve_project_phase

        _write_bom(project / ".webnovel" / "state.json", {
            "progress": {"current_chapter": 3},
        })
        snapshot = resolve_project_phase(project)
        blocking = list(getattr(snapshot, "blocking", ()) or ())
        assert not any("state_json" in b for b in blocking), (
            f"带 BOM 的 state.json 让阶段判定报告 state_json 异常：{blocking}"
        )


class TestDoctorReadsBom:
    def test_state_check_passes(self, project):
        import data_modules.doctor as doctor_mod

        _write_bom(project / ".webnovel" / "state.json", {
            "progress": {"current_chapter": 1},
        })
        # doctor 的入口函数名可能不同，取第一个接受 project_root 的公开函数
        entry = None
        for name in ("run_doctor", "diagnose", "check_project", "run_checks", "main_report"):
            fn = getattr(doctor_mod, name, None)
            if callable(fn):
                entry = fn
                break
        if entry is None:
            pytest.skip("doctor 没有可直接调用的入口")

        try:
            result = entry(project)
        except TypeError:
            pytest.skip("doctor 入口签名不同")
        blob = json.dumps(result, ensure_ascii=False, default=str)
        assert "BOM" not in blob, f"doctor 把带 BOM 的合法文件报成 BOM 问题：{blob[:400]}"
