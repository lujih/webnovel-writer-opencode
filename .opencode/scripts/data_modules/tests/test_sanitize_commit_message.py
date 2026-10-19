#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`security_utils.sanitize_commit_message` 的行为测试。

这个函数被两处用于把**用户输入的章节标题**拼进 `git commit -m`：

- `backup_manager.py:284` → `["commit", "-m", commit_message]`
- `init_project.py:694`  → `["git", "commit", "-m", f"初始化网文项目：{safe_title}"]`

两处都是 **list 形式的 subprocess 调用**，不经过 shell，所以
`--author` / `--amend` 之类**不可能**构成命令注入（CWE-77 不成立）。
而当前实现按「防注入」的思路删掉所有 `--xxx`，代价是会吃掉用户标题里
合法的双破折号：

    >>> sanitize_commit_message("重生--归来")
    '重生'                    # 「--归来」被当成 git 标志删掉了

中文标题里 `--` 作为破折号相当常见。函数注释里还有一处自相矛盾：
docstring 示例声称 `sanitize_commit_message("--amend Chapter 1")`
返回 `'amend Chapter 1'`，而 `_run_self_tests()` 断言它返回
`'Chapter 1'`——同一个函数两套预期，说明这段从没被真正校对过。

本文件钉住：用户标题的内容必须原样保留（只做提交消息层面的必要清理：
换行、长度、空白），不能因为「长得像命令行参数」就删除。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from security_utils import sanitize_commit_message  # noqa: E402


class TestUserTitleIsPreserved:
    """标题内容不能被静默吞掉。"""

    @pytest.mark.parametrize("title", [
        "重生--归来",
        "罗峰--最强的男人",
        "第5章：他来了--然后呢",
        "A--B",
        "测试---三连",
    ])
    def test_double_dash_survives(self, title):
        result = sanitize_commit_message(title)
        assert "--" in result, (
            f"标题 {title!r} 里的 '--' 被删成了 {result!r}。"
            f"调用方用的是 list 形式 subprocess（无 shell），'--' 不构成注入。"
        )
        assert result == title.strip(), f"{title!r} 不应被修改成 {result!r}"

    @pytest.mark.parametrize("title", [
        "风起云涌",
        "第0001章-山雨欲来",
        "第一章：觉醒",
    ])
    def test_plain_titles_unchanged(self, title):
        assert sanitize_commit_message(title) == title

    def test_chinese_em_dash_untouched(self):
        """中文破折号是 U+2014，与 git 的 '--' 无关，必须原样保留。"""
        assert sanitize_commit_message("她说——再见") == "她说——再见"

    def test_leading_dashes_not_stripped_from_content(self):
        """`--amend` 作为标题内容时也要保留（它是文字，不是参数）。"""
        assert sanitize_commit_message("--amend") != ""
        assert "amend" in sanitize_commit_message("--amend")


class TestCommitMessageHygiene:
    """提交消息本身该做的清理仍然要做。"""

    def test_newlines_become_spaces(self):
        """换行会让 `-m` 之外的参数解析错位，且破坏 one-line summary。"""
        result = sanitize_commit_message("第一行\n第二行")
        assert "\n" not in result
        assert "\r" not in result
        assert "第一行" in result and "第二行" in result

    def test_control_characters_removed(self):
        result = sanitize_commit_message("标题\x00\x07正文")
        assert "\x00" not in result
        assert "\x07" not in result

    def test_length_capped(self):
        long_title = "风" * 500
        assert len(sanitize_commit_message(long_title, max_length=200)) == 200

    def test_length_cap_respects_custom_value(self):
        assert len(sanitize_commit_message("风" * 100, max_length=10)) == 10

    def test_whitespace_collapsed(self):
        assert sanitize_commit_message("  标题   带   空格  ") == "标题 带 空格"

    def test_empty_falls_back(self):
        assert sanitize_commit_message("") == "Untitled commit"
        assert sanitize_commit_message("   ") == "Untitled commit"


class TestDocstringMatchesSelfTest:
    """docstring 示例与 `_run_self_tests()` 断言不能互相矛盾。

    当前 docstring 说 `--amend Chapter 1` → `'amend Chapter 1'`，
    而自检断言 → `'Chapter 1'`。两者不可能同时成立；更正后必须一致。
    """

    def test_docstring_examples_are_real(self):
        """docstring 里的 `>>> ` 示例必须真的成立。

        此前 docstring 说 `--amend Chapter 1` → `'amend Chapter 1'`，而
        `_run_self_tests()` 断言 `'Chapter 1'`——同一个函数两套预期。
        这条用例把 docstring 变成可执行的契约。

        用标准库 `doctest` 而不是手写 eval：它按规范处理示例里的转义
        （如 `"第一行\\n第二行"`），手写 eval 会在那里 SyntaxError。
        """
        import doctest

        parser = doctest.DocTestParser()
        test = parser.get_doctest(
            sanitize_commit_message.__doc__ or "",
            {"sanitize_commit_message": sanitize_commit_message},
            "sanitize_commit_message",
            None,
            0,
        )
        assert test.examples, "docstring 里应有 >>> 示例"
        runner = doctest.DocTestRunner(verbose=False)
        runner.run(test)
        assert not runner.failures, (
            f"docstring 有 {runner.failures} 个示例与代码实际行为不符"
        )


class TestNoShellInCallers:
    """前提校验：两处调用方确实不经 shell。

    如果将来有人改成 `shell=True`，那么 `--` 就有真实风险，本文件的
    「保留原样」结论也就不再成立——这条断言会先响。
    """

    @pytest.mark.parametrize("rel", [
        "backup_manager.py",
        "init_project.py",
    ])
    def test_caller_uses_list_form_subprocess(self, rel):
        text = (SCRIPTS / rel).read_text(encoding="utf-8")
        assert "shell=True" not in text, (
            f"{rel} 出现了 shell=True——sanitize_commit_message 的"
            f"「双破折号安全」前提被打破，需重新评估清理策略"
        )


class TestSelfTestActuallyPasses:
    """`security_utils.py` 自带 `_run_self_tests()`，但**不在 CI 里跑**。

    后果：里面的断言可以长期与现实不符而无人察觉。本次审查实测——
    - `sanitize_commit_message` 的断言与 docstring 互相矛盾（两套预期）
    - `sanitize_filename("C:\\\\Windows\\\\System32")` 断言 `'System32'`，
      而 POSIX 上实际返回 `'C_Windows_System32'`（basename 不认反斜杠）
    两条都只在 Windows 上成立，而 test.yml 跑的是 Ubuntu，所以永远绿。

    这条用例把自检变成 CI 的一部分。
    """

    def test_run_self_tests_exits_clean(self):
        import subprocess
        import sys as _sys

        r = subprocess.run(
            [_sys.executable, str(SCRIPTS / "security_utils.py")],
            capture_output=True, text=True, timeout=120,
        )
        assert r.returncode == 0, (
            f"security_utils 自检失败：\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}"
        )
        assert "所有安全工具函数测试通过" in r.stdout
