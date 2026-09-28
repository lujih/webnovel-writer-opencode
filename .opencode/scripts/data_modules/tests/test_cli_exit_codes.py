"""写路径退出码回归测试（第4轮 CLI 域 P0：失败时报成功）。

``_run_data_module`` 此前只要 ``main()`` 正常返回就 ``return 0``。而各模块
普遍用 ``emit_error()``（内部即 ``print_error``）打印后 ``return``，从不
``SystemExit``——于是：

- ``state process-chapter`` 载荷非法 → 什么都没写、退出码 0
- ``index`` 写入路径参数无效（INVALID_RELATIONSHIP_EVENT）→ 未写入、退出码 0
- ``context`` 构建失败被 ``except Exception`` 吞掉 → 拿到空上下文包、退出码 0

skill 的 ``&&`` 链据此继续推进，把未写入的章节当作已提交。修复方式是在
``cli_output`` 记录「本次调用是否报过错」，由 ``_run_data_module`` 据实返回。
"""
import subprocess
import sys
from pathlib import Path

import pytest

from data_modules import cli_output
from data_modules.webnovel import _run_data_module


def test_print_error_sets_flag():
    cli_output.reset_error_state()
    assert not cli_output.has_error()
    cli_output.print_error("SOME_CODE", "坏了")
    assert cli_output.has_error()


def test_print_success_does_not_set_flag():
    cli_output.reset_error_state()
    cli_output.print_success({"a": 1})
    assert not cli_output.has_error()


def test_reset_clears_flag():
    cli_output.print_error("X", "y")
    assert cli_output.has_error()
    cli_output.reset_error_state()
    assert not cli_output.has_error()


def test_run_data_module_propagates_systemexit_code(tmp_path, monkeypatch):
    """载荷缺失/非法时必须非零退出，且不得以 traceback 收场。

    注意 tmp_path 建在仓库内的 .tmp/ 下，从它向上搜索会命中仓库工作区的
    `.opencode/.webnovel-current-project` 指针，从而**意外**解析出一个真实书
    项目。因此这里必须自备 state.json，让解析确定地停在本目录。
    """
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    rc = _run_data_module("state_manager", ["--project-root", str(tmp_path),
                                            "process-chapter", "--chapter", "1",
                                            "--data", "@missing.json"])
    assert rc != 0, "载荷缺失/非法时必须非零退出"


def test_run_data_module_returns_zero_on_success(tmp_path, monkeypatch):
    """正常路径仍须返回 0，不得把成功误报成失败。"""
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    rc = _run_data_module("state_manager", ["--project-root", str(tmp_path), "get-progress"])
    assert rc == 0, "正常查询被误报为失败"


def test_run_data_module_returns_nonzero_on_emit_error(tmp_path):
    """核心用例：模块走 emit_error()（非 SystemExit）时必须返回 1。

    这正是旧实现漏掉的那条路径——get-entity 找不到实体会 emit_error 后直接
    return，argparse 从未介入，故 SystemExit 分支救不了它。
    """
    (tmp_path / ".webnovel").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".webnovel" / "state.json").write_text("{}", encoding="utf-8")
    rc = _run_data_module("state_manager", ["--project-root", str(tmp_path),
                                            "get-entity", "--id", "不存在的人"])
    assert rc == 1, "emit_error 后仍返回 0 ⇒ 失败被当成成功"


REPO_ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("argv", [
    ["--project-root", "{root}", "state", "process-chapter",
     "--chapter", "1", "--data", "@nope.json"],
])
def test_cli_process_exits_nonzero_on_bad_input(argv, tmp_path):
    """端到端：经 webnovel.py 入口执行，坏输入必须让进程非零退出。"""
    args = [a.format(root=str(tmp_path)) for a in argv]
    proc = subprocess.run(
        [sys.executable, "-X", "utf8", str(REPO_ROOT / ".opencode" / "scripts" / "webnovel.py"),
         *args],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert proc.returncode != 0, (
        f"坏输入却退出 0，skill 的 && 链会继续。stdout={proc.stdout[:400]}"
    )
