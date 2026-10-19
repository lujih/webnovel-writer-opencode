#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`chapter_paths.parse_chapter_range` 的输入校验测试。

这个函数解析 `--chapters` 参数（如 `"5-12"`、`"5,7,9-12"`），服务两个
**破坏性/批量**入口：

- `chapter_delete_service.cmd_delete_chapters()` —— 删章节
- `orchestrate.cmd_orchestrate()` —— 批量写作

当前实现对畸形输入的表现（实测）：

    parse_chapter_range("abc")    → ValueError: invalid literal for int() ...
    parse_chapter_range("-5")     → ValueError: invalid literal for int() ...
    parse_chapter_range("5-")     → ValueError: invalid literal for int() ...
    parse_chapter_range("5--12")  → []                      ← 静默变成"空选择"
    parse_chapter_range("5-2")    → []                      ← 同上
    parse_chapter_range("0")      → [0]                     ← 第 0 章不存在

问题有三层：

1. **未捕获的 ValueError** 从 CLI 直接冒成 Python traceback。用户看到的是
   栈帧而不是"你参数写错了"。`delete-chapters` 是破坏性操作，本该在动手
   之前就把错误说清楚。
2. **静默空选择**。`"5--12"` 与 `"5-2"`（倒序）都返回 `[]`，调用方只看到
   "no chapters specified"，用户却以为自己指定了范围。对删除操作而言，
   "我以为删了 5 到 12 章"和"什么都没删"之间的误解很危险——反过来如果
   将来有人把空列表当成"全部"，就是灾难。
3. **接受 0 与负数**。章节号从 1 开始（`volume_num_for_chapter` 明确
   `raise ValueError("chapter_num must be >= 1")`），`[0]` 却能通过。

本文件钉住：畸形输入给出明确的中文错误，倒序范围明确报错而不是静默空。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from chapter_paths import parse_chapter_range  # noqa: E402


class TestValidInputStillWorks:
    """先把正确行为锁住，避免"修坏输入"把好输入也改了。"""

    @pytest.mark.parametrize("spec,expected", [
        ("5", [5]),
        ("5-12", [5, 6, 7, 8, 9, 10, 11, 12]),
        ("5,7,9-12", [5, 7, 9, 10, 11, 12]),
        ("1,1,2", [1, 2]),                # 去重
        ("12,5", [5, 12]),                 # 排序
        (" 5 - 7 ", [5, 6, 7]),            # 容忍空白
        ("3-3", [3]),                      # 单元素范围
    ])
    def test_valid(self, spec, expected):
        assert parse_chapter_range(spec) == expected

    def test_large_range(self):
        result = parse_chapter_range("1-100")
        assert len(result) == 100
        assert result[0] == 1 and result[-1] == 100


class TestMalformedInputRaisesClearError:
    """畸形输入要给出可读错误，不能是裸 ValueError 或静默空列表。"""

    @pytest.mark.parametrize("spec", [
        "abc",
        "5-",
        "-5",
        "5--12",
        "5,a",
        "",
        "   ",
        ",5",
        "5,",
        "5-2",            # 倒序：不是"空选择"，是写错了
        "0",              # 章节号从 1 开始
        "-3--1",
    ])
    def test_raises_value_error_with_chinese_hint(self, spec):
        with pytest.raises(ValueError) as excinfo:
            parse_chapter_range(spec)
        msg = str(excinfo.value)
        assert msg, "错误消息不能为空"
        # 必须包含足够定位问题的信息：要么回显输入，要么说明正确格式
        assert spec in msg or "格式" in msg or "范围" in msg, (
            f"{spec!r} 的错误消息缺少定位信息: {msg!r}"
        )

    def test_does_not_return_silent_empty_for_reversed_range(self):
        """倒序范围是最危险的静默失败：用户以为选了范围，实际什么都没选。"""
        with pytest.raises(ValueError):
            parse_chapter_range("5-2")

    def test_chapter_zero_rejected(self):
        with pytest.raises(ValueError):
            parse_chapter_range("0")

    def test_negative_rejected(self):
        with pytest.raises(ValueError):
            parse_chapter_range("-3")


class TestNoBareTracebackAtCli:
    """破坏性 CLI 不能把 Python traceback 甩给用户。"""

    @staticmethod
    def _fake_project(tmp_path: Path) -> Path:
        (tmp_path / ".webnovel").mkdir(parents=True)
        (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
        return tmp_path

    @pytest.mark.parametrize("bad", ["abc", "-5", "5--12", "5-2"])
    def test_delete_chapters_reports_instead_of_crashing(self, tmp_path, bad):
        import subprocess
        import sys as _sys

        project = self._fake_project(tmp_path)
        r = subprocess.run(
            [_sys.executable, str(SCRIPTS / "webnovel.py"),
             "delete-chapters", bad, "--project-root", str(project), "--dry-run"],
            capture_output=True, text=True, timeout=120,
            cwd=str(SCRIPTS.parent),
        )
        combined = r.stdout + r.stderr
        assert "Traceback (most recent call last)" not in combined, (
            f"delete-chapters {bad!r} 抛了裸 traceback：\n{combined[-1500:]}"
        )
