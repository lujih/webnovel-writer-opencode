#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统一 CLI 入口的**错误呈现**测试。

`webnovel.py` 是 skills/agents 唯一调用的入口。新用户最常踩的第一条路径
是「装好了、还没 init、先跑一条命令看看」：

    $ python .opencode/scripts/webnovel.py status
    Traceback (most recent call last):
      ...
    FileNotFoundError: Unable to locate webnovel project root. ...

退出码是对的（1），但用户看到的是一屏栈帧。真实信息（"找不到书项目，
先运行 /webnovel-init 或传 --project-root"）被埋在最后一行；对不熟悉
Python 的作者（本项目的目标用户）来说，这跟"程序炸了"没区别。

而且 `_resolve_root()` 会连做两次解析（先 CWD，再脚本所在工作区），
所以同一个原因在栈里出现两层，更像崩溃现场。

本文件钉住：预期内的用户错误（找不到项目、参数非法）必须以
`ERROR: <中文可执行提示>` 的形式出现在 stderr，**不带 traceback**，
且退出码非零；同时真正的程序 bug（意外异常）仍然要冒栈，便于排查。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
ENTRY = SCRIPTS / "webnovel.py"
REPO = SCRIPTS.parents[1]

pytestmark = pytest.mark.skipif(
    not ENTRY.is_file(), reason="webnovel.py 不存在"
)


def _run(args: list[str], cwd: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # 隔离：不继承可能存在的项目绑定
    for key in ("WEBNOVEL_PROJECT_ROOT",
                "OPENCODE_PROJECT_DIR", "CLAUDE_PROJECT_DIR"):
        env.pop(key, None)
    # ⚠️ `WEBNOVEL_OPENCODE_HOME` 不能只是 pop 掉——pop 之后用的是**用户级默认位置**，
    # 而 project_locator 的最后一级兜底正是「用户级注册表」。在开发机上注册过书时，
    # 空目录也能解析到那本书，`status` 于是正常退出 0，本类的「找不到项目必须非零
    # 退出」全部被环境污染成失败（CI 上是干净的容器，所以一直绿）。
    # 这里把它指向 cwd 下一个不存在的隔离目录，注册表查询必然落空。
    env["WEBNOVEL_OPENCODE_HOME"] = str(Path(cwd) / ".isolated-webnovel-home")
    if env_extra:
        env.update(env_extra)
    # `status` 默认 `--project-root '.'`，会走入口层的「工作区兜底」；
    # 而 conftest 把 tmp_path 钉在仓库 `.tmp/pytest` 下——也就是说这个
    # "empty_dir" 其实**在工具工作区内**，兜底会解析到工作区里真实的那本书
    # （本机实测指向 webnovel2/凡尘之舞），于是「项目不存在」这个前提失效。
    # CI 容器里没有书，所以一直是绿的。这里显式钉死 --project-root，
    # 让用例真正测「显式指定的目录里没有书项目」。
    if args and args[0] == "status" and "--project-root" not in args:
        args = [*args, "--project-root", str(cwd)]
    return subprocess.run(
        [sys.executable, str(ENTRY), *args],
        # encoding 必须显式指定：Windows 下 text=True 会按 GBK 解码子进程 stdout，
        # 而本项目的中文/emoji 输出（如「🚫」）根本不是 GBK 可解码的——
        # reader 线程抛 UnicodeDecodeError，于是 stdout/stderr 变成空串，
        # 失败原因被伪装成「消息不对」而不是「解码炸了」。
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180, cwd=str(cwd), env=env,
    )


@pytest.fixture
def empty_dir(tmp_path: Path) -> Path:
    """一个既不是书项目、也没有任何祖先书项目的目录。"""
    d = tmp_path / "no-project-here"
    d.mkdir()
    return d


class TestMissingProjectIsAFriendlyError:
    """最常见的用户错误：项目尚未初始化。"""

    @pytest.mark.parametrize("args", [
        ["status"],
        ["ssot", "verify"],
        ["workflow", "status"],
    ])
    def test_no_traceback(self, empty_dir, args):
        r = _run(args, cwd=empty_dir)
        combined = r.stdout + r.stderr
        assert "Traceback (most recent call last)" not in combined, (
            f"`webnovel.py {' '.join(args)}` 在项目外抛了裸 traceback：\n"
            f"{combined[-1500:]}"
        )

    def test_exit_code_nonzero(self, empty_dir):
        r = _run(["status"], cwd=empty_dir)
        assert r.returncode != 0, "找不到项目时必须以非零退出"

    def test_message_is_actionable(self, empty_dir):
        r = _run(["status"], cwd=empty_dir)
        combined = r.stdout + r.stderr
        # 必须告诉用户下一步怎么做
        assert "webnovel-init" in combined or "--project-root" in combined, (
            f"错误信息缺少可执行指引：\n{combined[-1000:]}"
        )

    def test_message_uses_error_prefix(self, empty_dir):
        r = _run(["status"], cwd=empty_dir)
        assert "ERROR" in r.stderr, f"stderr 里应有 ERROR 前缀：\n{r.stderr[-800:]}"

    def test_traceback_repeated_twice_is_gone(self, empty_dir):
        """旧实现在同一原因上叠了两层调用（CWD 一次、脚本工作区一次）。"""
        r = _run(["status"], cwd=empty_dir)
        assert r.stderr.count("FileNotFoundError") <= 1, (
            f"同一个错误重复出现多次：\n{r.stderr[-1200:]}"
        )


class TestInvalidArgumentsAreFriendly:
    """参数错误也属于"预期内的用户错误"。"""

    def test_bad_chapter_range_no_traceback(self, empty_dir):
        r = _run(["delete-chapters", "5-2", "--dry-run"], cwd=empty_dir)
        combined = r.stdout + r.stderr
        assert "Traceback (most recent call last)" not in combined, combined[-1200:]


class TestRealBugsStillRaise:
    """别把 catch-all 加到掩盖真 bug 的程度。

    这条用一个必然内部出错的场景（--project-root 指向一个 state.json
    是坏 JSON 的目录）验证：非预期异常仍应可见，不能全部吞成
    "ERROR: 出错了"——那样排查就断了。
    """

    def test_corrupt_state_surfaces_the_problem(self, tmp_path):
        book = tmp_path / "book"
        (book / ".webnovel").mkdir(parents=True)
        (book / ".webnovel" / "state.json").write_text("{ not json", encoding="utf-8")

        r = _run(["status", "--project-root", str(book)], cwd=tmp_path)
        combined = r.stdout + r.stderr
        assert r.returncode != 0
        # 关键：要么给出可读错误（ERROR / ❌），要么保留栈；绝不能静默成功
        assert combined.strip(), "坏 state.json 时不能什么都不输出"
        readable = "ERROR" in combined or "❌" in combined or "Traceback" in combined
        assert readable, (
            f"坏 state.json 既没报错也没栈，等于静默失败：\n{combined[-1000:]}"
        )


class TestCorruptStateIsFriendly:
    """state.json 损坏是高频的真实场景（手工编辑 / 写入中断）。

    `status` 走 `_run_script` 子进程，`StatusReporter.load_state()` 返回
    bool 表示成败——但此前 `json.load` 没有保护，异常穿透 bool 契约，
    用户看到的是 JSONDecodeError 栈。
    """

    @pytest.fixture
    def corrupt_book(self, tmp_path: Path) -> Path:
        book = tmp_path / "corrupt-book"
        (book / ".webnovel").mkdir(parents=True)
        (book / ".webnovel" / "state.json").write_text("{ not json", encoding="utf-8")
        return book

    def test_no_traceback(self, corrupt_book, tmp_path):
        r = _run(["status", "--project-root", str(corrupt_book)], cwd=tmp_path)
        combined = r.stdout + r.stderr
        assert "Traceback (most recent call last)" not in combined, combined[-1200:]

    def test_exit_code_nonzero(self, corrupt_book, tmp_path):
        r = _run(["status", "--project-root", str(corrupt_book)], cwd=tmp_path)
        assert r.returncode != 0, "状态读不出来时必须非零退出，否则 skill 的 && 链会继续"

    def test_message_names_the_file_and_suggests_repair(self, corrupt_book, tmp_path):
        r = _run(["status", "--project-root", str(corrupt_book)], cwd=tmp_path)
        combined = r.stdout + r.stderr
        assert "state.json" in combined
        # 给出可执行修复方向
        assert any(k in combined for k in ("ssot rebuild", ".bak", "合法 JSON")), (
            f"错误信息缺少修复指引：\n{combined[-1000:]}"
        )
