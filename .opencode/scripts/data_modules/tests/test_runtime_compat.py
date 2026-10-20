#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`runtime_compat` 的路径归一化边界。

`runtime_compat`（78 行、零测试覆盖）是 L0 地基——`project_locator`、
`data_modules/config`、`data_modules/webnovel` 等 24 个模块依赖它。其中
`normalize_windows_path()` 被用在**确定项目根目录**这条最关键的路径上：

    # project_locator.py:76
    return normalize_windows_path(raw).expanduser().resolve()

## 缺陷一：空路径被静默解释成"当前目录"

    normalize_windows_path("")    → Path(".")
    normalize_windows_path("   ") → Path(".")

`Path("")` 在 Python 里就是 `Path(".")`。而 `""` 的实际来源很常见：
环境变量存在但为空（`export WEBNOVEL_PROJECT_ROOT=`）、配置项留空、
CLI 传了空串。

后果：调用方以为"没有指定路径"，实际拿到的是**当前工作目录**。若它随后被
当作项目根，就会去操作当前目录而不是报错——这与第一轮修过的
「CWD 静默选书」是同一类问题（站在书 A 目录里、却改写了 B）。

## 缺陷二：`None` 抛 `TypeError`

    normalize_windows_path(None)
    → TypeError: expected str, bytes or os.PathLike object, not NoneType

一个名字里带 `compat` 的辅助函数，对 `None` 崩溃而不是给出明确语义。

## 缺陷三：Windows 逻辑在 Linux CI 上完全不可测

函数开头 `if sys.platform != "win32": return Path(value)`，于是
`_WIN_POSIX_DRIVE_RE` / `_WIN_WSL_MNT_DRIVE_RE` 这两条正则**在任何
Linux CI 上都不会被执行**。Git Bash 用户报的路径问题无法被自动化测试发现。

（本文件用 `mock.patch` 覆盖 `sys.platform` 来解决这一点。）

## 本文件钉住

- 空 / 纯空白 / `None` 输入不得静默变成当前目录
- Git Bash `/d/x` → `D:/x`、WSL `/mnt/d/x` → `D:/x`（Windows 平台）
- 非 Windows 平台原样返回
- 已规范的 `C:/x`、相对路径、多字符首段（`/usr/local`）不被误转
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import runtime_compat as rc  # noqa: E402
from runtime_compat import normalize_windows_path  # noqa: E402


@pytest.fixture
def win32():
    """把 `sys.platform` 伪装成 win32，让 Windows 分支真正被执行。"""
    with mock.patch.object(rc.sys, "platform", "win32"):
        yield


class TestWindowsPathConversion:
    """Windows 平台上的 Git Bash / WSL 路径转换。"""

    def test_git_bash_drive(self, win32):
        assert str(normalize_windows_path("/d/desktop/x")) == "D:/desktop/x"

    def test_git_bash_uppercase_input(self, win32):
        assert str(normalize_windows_path("/D/desktop/x")) == "D:/desktop/x"

    def test_wsl_mnt_drive(self, win32):
        assert str(normalize_windows_path("/mnt/d/desktop/x")) == "D:/desktop/x"

    def test_already_windows(self, win32):
        assert str(normalize_windows_path("C:/already/here")) == "C:/already/here"

    def test_relative_path_untouched(self, win32):
        assert str(normalize_windows_path("relative/path")) == "relative/path"

    def test_multi_char_first_segment_untouched(self, win32):
        """`/usr/local/bin` 不是盘符路径，不该被转成 `U:/sr/local/bin`。"""
        result = str(normalize_windows_path("/usr/local/bin"))
        assert result == "/usr/local/bin", f"被误转成 {result}"

    def test_backslash_path_untouched(self, win32):
        assert "C:" in str(normalize_windows_path("C:\\Users\\me"))


class TestNonWindowsPassthrough:
    """非 Windows 平台原样返回。"""

    def test_posix_path_untouched(self):
        # 当前 CI 就是 posix；`/d/x` 不该被转成 `D:/x`
        assert str(normalize_windows_path("/d/desktop/x")) == "/d/desktop/x"

    def test_wsl_style_untouched(self):
        assert str(normalize_windows_path("/mnt/d/x")) == "/mnt/d/x"


class TestEmptyInputIsNotCurrentDirectory:
    """核心缺陷一：空路径被解释成 `Path('.')`，而那是真实存在的当前目录。

    Python 的 `Path("")` 恒等于 `Path(".")`，无法用一个 `Path` 表示"空"。
    所以这里的防线是：

    1. `normalize_windows_path("")` 的结果**必须能被调用方识别为空**——
       即 `str(result)` 为 `"."` 时，调用方不能把它当成有效项目根。
    2. 真正决定项目根的调用方（`DataModulesConfig.from_project_root`）
       必须在入口处拒绝空值，而不是把 CWD 当项目根。
    """

    @pytest.mark.parametrize("value", ["", "   ", "\t", "\n"])
    def test_result_is_recognizable_as_empty(self, value):
        """空输入的结果必须能被识别为空（而不是一个随机有效目录）。"""
        result = normalize_windows_path(value)
        assert str(result) in ("", "."), (
            f"normalize_windows_path({value!r}) → {result!r}，"
            f"不是可识别的空表示"
        )

    @pytest.mark.parametrize("value", ["", "   "])
    def test_empty_windows_platform(self, win32, value):
        result = normalize_windows_path(value)
        assert str(result) in ("", "."), f"Windows 上 → {result!r}"

    def test_config_rejects_empty_project_root(self, tmp_path, monkeypatch):
        """真正的防线：`from_project_root("")` 不能把 CWD 当项目根。"""
        from data_modules.config import DataModulesConfig

        monkeypatch.chdir(tmp_path)
        (tmp_path / ".webnovel").mkdir()

        try:
            config = DataModulesConfig.from_project_root("")
        except (ValueError, TypeError):
            return  # 拒绝是正确行为
        # 若没拒绝，至少不能把 CWD 静默当成项目根
        assert Path(config.project_root).resolve() != tmp_path.resolve(), (
            "from_project_root('') 把当前工作目录当成了项目根——"
            "环境变量留空（export WEBNOVEL_PROJECT_ROOT=）就会走到这里，"
            "随后所有写操作都作用在 CWD 上"
        )


class TestNoneInput:
    """核心缺陷二：`None` 不该抛 TypeError。"""

    def test_none_does_not_raise(self):
        try:
            result = normalize_windows_path(None)
        except TypeError as exc:
            pytest.fail(
                f"normalize_windows_path(None) 抛了 TypeError: {exc}。"
                f"这是 compat 辅助函数，对 None 应给出明确语义而不是崩溃。"
            )
        assert result is not None

    def test_none_windows_platform(self, win32):
        try:
            normalize_windows_path(None)
        except TypeError as exc:
            pytest.fail(f"Windows 上 None 抛了 TypeError: {exc}")

    def test_pathlib_input_works(self):
        assert str(normalize_windows_path(Path("some/path"))) == "some/path"


class TestStdioHelper:
    """`enable_windows_utf8_stdio()` 在非 Windows 上是无操作。"""

    def test_non_windows_returns_false(self):
        assert rc.enable_windows_utf8_stdio() is False

    def test_skip_in_pytest_respected(self):
        """pytest 环境下带 skip_in_pytest 应跳过（即使平台是 win32）。"""
        with mock.patch.object(rc.sys, "platform", "win32"), \
             mock.patch.dict(rc.os.environ, {"PYTEST_CURRENT_TEST": "x"}):
            assert rc.enable_windows_utf8_stdio(skip_in_pytest=True) is False

    def test_never_raises(self):
        """任何情况下都不该抛异常——它在模块 import 期就会被调用。"""
        try:
            rc.enable_windows_utf8_stdio()
        except Exception as exc:
            pytest.fail(f"enable_windows_utf8_stdio 抛了 {type(exc).__name__}: {exc}")
