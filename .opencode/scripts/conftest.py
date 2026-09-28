from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path

import pytest


_ORIGINAL_SQLITE_CONNECT = sqlite3.connect
_ORIGINAL_TEMPORARY_DIRECTORY = tempfile.TemporaryDirectory


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _tmp_root() -> Path:
    root = _repo_root() / ".tmp" / "pytest"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_mkdtemp(suffix: str | None = None, prefix: str | None = None, dir: str | os.PathLike[str] | None = None) -> str:
    """Avoid WindowsApps Python creating inaccessible 0o700 temp dirs."""
    suffix = "" if suffix is None else suffix
    prefix = "tmp" if prefix is None else prefix
    root = Path(dir) if dir is not None else _tmp_root()
    root.mkdir(parents=True, exist_ok=True)

    for _ in range(100):
        path = root / f"{prefix}{uuid.uuid4().hex}{suffix}"
        try:
            path.mkdir()
        except FileExistsError:
            continue
        return str(path.resolve())

    raise FileExistsError(f"Unable to create unique temporary directory under {root}")


def _install_safe_tempfile() -> None:
    root = _tmp_root()
    for name in ("TMP", "TEMP", "TMPDIR"):
        os.environ[name] = str(root)
    os.environ["WEBNOVEL_TEST_RELAX_ATOMIC_REPLACE"] = "1"
    tempfile.tempdir = str(root)
    tempfile.mkdtemp = _safe_mkdtemp
    tempfile.TemporaryDirectory = _SafeTemporaryDirectory


class _SafeTemporaryDirectory(_ORIGINAL_TEMPORARY_DIRECTORY):
    def __init__(self, suffix=None, prefix=None, dir=None, ignore_cleanup_errors=True, *, delete=True):
        super().__init__(
            suffix=suffix,
            prefix=prefix,
            dir=dir,
            ignore_cleanup_errors=ignore_cleanup_errors,
            delete=delete,
        )


def _safe_sqlite_connect(*args, **kwargs):
    conn = _ORIGINAL_SQLITE_CONNECT(*args, **kwargs)
    try:
        conn.execute("PRAGMA journal_mode=MEMORY")
    except sqlite3.DatabaseError:
        pass
    return conn


def _install_safe_sqlite() -> None:
    sqlite3.connect = _safe_sqlite_connect


def _install_isolated_project_locator_home() -> None:
    """把 project_locator 的全局注册表重定向到临时目录。

    写测试时若不隔离：init_project.py 的 write_current_project_pointer() 会向上
    找到仓库自身的 .opencode/，把 .opencode/.webnovel-current-project 指针和用户
    真实的 ~/.opencode|~/.claude/webnovel-writer/workspaces.json 一起改写成测试
    生成的临时书项目——测试跑完即摧毁开发者的真实项目绑定，而写命令随后会按这个
    绑定写进错误的书。tmp_path 又被本文件强制落在仓库内的 .tmp/pytest/，因此这条
    路径必然命中仓库自身而不是一个无关的父目录。
    """
    home = _tmp_root() / "opencode-home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["WEBNOVEL_OPENCODE_HOME"] = str(home)


_POINTER_SNAPSHOT: dict = {}


def _snapshot_workspace_pointer() -> None:
    """备份仓库的 .opencode/.webnovel-current-project 指针。

    WEBNOVEL_OPENCODE_HOME 只隔离全局注册表；工作区指针文件是由
    _find_workspace_root 从临时书项目路径向上找到仓库 .opencode/ 后直接写入的。
    测试期间的 init 必须不影响开发者真实绑定，故在会话开始时快照、会话结束时还原。
    """
    for pointer in _repo_root().glob(".opencode/.webnovel-current-project"):
        try:
            _POINTER_SNAPSHOT[pointer] = pointer.read_text(encoding="utf-8")
        except OSError:
            pass


def _restore_workspace_pointer() -> None:
    for pointer, content in _POINTER_SNAPSHOT.items():
        try:
            pointer.write_text(content, encoding="utf-8")
        except OSError:
            pass
    _POINTER_SNAPSHOT.clear()


def pytest_configure(config: pytest.Config) -> None:
    _install_safe_tempfile()
    _install_isolated_project_locator_home()
    _snapshot_workspace_pointer()
    _install_safe_sqlite()


def pytest_unconfigure(config: pytest.Config) -> None:
    _restore_workspace_pointer()


@pytest.fixture
def tmp_path(request: pytest.FixtureRequest) -> Path:
    safe_name = "".join(ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in request.node.name)
    path = _tmp_root() / f"{safe_name}_{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=False)
    try:
        yield path
    finally:
        if os.environ.get("WEBNOVEL_KEEP_TEST_TMP") != "1":
            shutil.rmtree(path, ignore_errors=True)


_install_safe_tempfile()
_install_isolated_project_locator_home()
_snapshot_workspace_pointer()
_install_safe_sqlite()
