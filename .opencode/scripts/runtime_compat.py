#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Runtime compatibility helpers.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Union


def enable_windows_utf8_stdio(*, skip_in_pytest: bool = False) -> bool:
    """Enable UTF-8 stdio wrappers on Windows.

    Returns:
        True if wrapping was applied, False otherwise.
    """
    if sys.platform != "win32":
        return False
    if skip_in_pytest and os.environ.get("PYTEST_CURRENT_TEST"):
        return False

    stdout_encoding = str(getattr(sys.stdout, "encoding", "") or "").lower()
    stderr_encoding = str(getattr(sys.stderr, "encoding", "") or "").lower()
    if stdout_encoding == "utf-8" and stderr_encoding == "utf-8":
        return False

    try:
        import io

        if hasattr(sys.stdout, "buffer"):
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        if hasattr(sys.stderr, "buffer"):
            sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")
        return True
    except Exception:
        return False


_WIN_POSIX_DRIVE_RE = re.compile(r"^/(?P<drive>[a-zA-Z])/(?P<rest>.*)$")
_WIN_WSL_MNT_DRIVE_RE = re.compile(r"^/mnt/(?P<drive>[a-zA-Z])/(?P<rest>.*)$")


def normalize_windows_path(value: Union[str, Path, None]) -> Path:
    """将 Windows 上常见的 POSIX 风格路径规范化为 Windows 盘符路径。

    典型来源：

    - Git Bash / MSYS:  ``/d/desktop/...``      → ``D:/desktop/...``
    - WSL:              ``/mnt/d/desktop/...``  → ``D:/desktop/...``

    非 Windows 平台直接返回 ``Path(value)``。

    **空输入返回空 ``Path``，不返回 ``Path('.')``。** ``Path("")`` 在 Python
    里等于 ``Path(".")``——那是**当前工作目录**，一个真实存在、可被读写的目录。
    而空值的实际来源很常见：环境变量留空（``export WEBNOVEL_PROJECT_ROOT=``）、
    配置项留空、CLI 传空串。调用方（``project_locator`` 等）若把它当成项目根，
    就会去操作当前目录而不是报错。

    ``None`` 同样按空处理，不抛 ``TypeError``——这是 compat 辅助函数。
    """
    if value is None:
        return Path("")

    raw = str(value).strip()
    if not raw:
        return Path("")

    if sys.platform != "win32":
        return Path(value)

    m = _WIN_WSL_MNT_DRIVE_RE.match(raw)
    if m:
        drive = m.group("drive").upper()
        rest = m.group("rest")
        return Path(f"{drive}:/{rest}")

    m = _WIN_POSIX_DRIVE_RE.match(raw)
    if m:
        drive = m.group("drive").upper()
        rest = m.group("rest")
        return Path(f"{drive}:/{rest}")

    return Path(value)

