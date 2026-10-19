#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`ContextManager` 读取 state.json 的失败语义。

`context_manager` 是**写作流程的上下文组装入口**——`build_context()` 的结果
决定模型这一章能"看见"什么。它内部通过 `_load_state()` 读 state.json：

    def _load_state(self):
        path = self.config.state_file
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

两个既有缺陷（同一处代码）：

1. **坏 JSON 直接抛**。写作流程以 traceback 中断，用户看到的是
   `JSONDecodeError`，而不是"你的 state.json 坏了，先修复再写"。
2. **UTF-8 BOM 被判成损坏**。Windows 记事本 / VS Code 的
   「UTF-8 with BOM」是中文作者的默认保存格式，`json.loads` 不跳 BOM。

仓库里已有 7 处用 `utf-8-sig` 读（`chapter_commit.py` / `review_pipeline.py` /
`skill_runner.py` / `story_system_engine.py` / `value_*` …），本处漏了。
而刚刚修完的 `structural_checker` 是同一个模式。

关于"该怎么处理"，本仓库已有一致的取向：
- `state_manager._read_state_strict()` —— fail-closed，抛 `CorruptStateError`
- `status_reporter.load_state()` —— 返回 False + 打印修复建议
- `context_manager._load_outline()`（就在本文件里！）—— 抛
  `FileNotFoundError("无法加载第 N 章大纲，写作流程已阻断。...")`
  即作者**已经**在这里采用了"带中文原因的可读异常"

所以本模块的正解不是"吞掉返回空 dict"（那会让模型在**没有状态**的情况下
继续写作，产出自相矛盾的章节），而是抛一个带修复指引的明确异常。

本文件钉住：
1. 坏 JSON 必须给出可读的、指明 state.json 的中文错误
2. BOM 不该被判成损坏
3. 正常路径不变
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
from data_modules.context_manager import ContextManager  # noqa: E402


@pytest.fixture
def cm(tmp_path: Path) -> ContextManager:
    cfg = DataModulesConfig.from_project_root(tmp_path)
    cfg.ensure_dirs()
    return ContextManager(cfg)


class TestNormalPath:
    def test_missing_state_returns_empty(self, cm, tmp_path):
        state_file = tmp_path / ".webnovel" / "state.json"
        if state_file.exists():
            state_file.unlink()
        assert cm._load_state() == {}

    def test_valid_state_is_loaded(self, cm, tmp_path):
        (tmp_path / ".webnovel" / "state.json").write_text(
            json.dumps({"progress": {"current_chapter": 3}}), encoding="utf-8")
        assert cm._load_state()["progress"]["current_chapter"] == 3

    def test_empty_object_is_valid(self, cm, tmp_path):
        (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
        assert cm._load_state() == {}


class TestBomIsAccepted:
    """Windows 编辑器的默认格式不该被判成损坏。"""

    def test_bom_state_is_readable(self, cm, tmp_path):
        (tmp_path / ".webnovel" / "state.json").write_bytes(
            '\ufeff{"progress": {"current_chapter": 7}}'.encode("utf-8"))
        assert cm._load_state()["progress"]["current_chapter"] == 7

    def test_bom_with_chinese(self, cm, tmp_path):
        (tmp_path / ".webnovel" / "state.json").write_bytes(
            '\ufeff{"project_info": {"title": "凡尘之舞"}}'.encode("utf-8"))
        assert cm._load_state()["project_info"]["title"] == "凡尘之舞"


class TestCorruptStateGivesReadableError:
    """坏 JSON 要给可读的中文原因，而不是裸 JSONDecodeError。"""

    def _corrupt(self, tmp_path: Path) -> None:
        (tmp_path / ".webnovel" / "state.json").write_text("{ bad json", encoding="utf-8")

    def test_raises_readable_error(self, cm, tmp_path):
        self._corrupt(tmp_path)
        with pytest.raises(Exception) as excinfo:
            cm._load_state()
        msg = str(excinfo.value)
        assert "state.json" in msg, (
            f"错误信息没指明是 state.json：{msg!r}"
        )
        assert not isinstance(excinfo.value, json.JSONDecodeError), (
            "仍是裸 JSONDecodeError——用户看到的是解析器术语，"
            "不知道该怎么办"
        )

    def test_error_suggests_repair(self, cm, tmp_path):
        self._corrupt(tmp_path)
        with pytest.raises(Exception) as excinfo:
            cm._load_state()
        msg = str(excinfo.value)
        assert any(k in msg for k in ("修复", "恢复", "rebuild", ".bak", "授权", "无法")), (
            f"错误信息缺少下一步指引：{msg!r}"
        )

    def test_fail_closed_does_not_return_empty(self, cm, tmp_path):
        """关键：不能静默返回 {} —— 那会让模型在没有状态的���况下写作。"""
        self._corrupt(tmp_path)
        try:
            state = cm._load_state()
        except Exception:
            return  # 抛异常是正确行为
        pytest.fail(
            f"坏 state.json 时返回了 {state!r}——写作流程会在没有状态的情况下继续，"
            f"产出自相矛盾的章节。应 fail-closed 并给出修复指引。"
        )


class TestConsistencyWithSiblingLoader:
    """同文件里的 `_load_outline` 已是"可读异常"风格，两者应一致。"""

    def test_load_outline_error_style(self, cm, tmp_path):
        with pytest.raises(FileNotFoundError) as excinfo:
            cm._load_outline(1)
        assert "写作流程" in str(excinfo.value) or "大纲" in str(excinfo.value)
