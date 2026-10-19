#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`state_projection_renderer` 读取 state.json 的失败语义。

这是本仓库里**同一个缺陷模式第 4 次出现**：

| 模块 | 坏 JSON | BOM |
|---|---|---|
| `structural_checker._load_state` | 抛 JSONDecodeError | 误判损坏 |
| `context_manager._load_state` | 抛 JSONDecodeError | 误判损坏 |
| `cli_args.load_json_arg` | 抛（可接受，输入本来就是 JSON） | 误判损坏 |
| `state_projection_renderer.render_all_projections` | 抛 JSONDecodeError | 误判损坏 |

（前三个已修。）

`render_all_projections()` 直接：

    state = json.loads(state_path.read_text(encoding="utf-8"))

它在两条重要路径上被调用：
- `webnovel.py state render`（CLI，用户直接执行）
- `state_projection_renderer.py` 的 `main()`（独立脚本）

两条都没有异常保护，于是坏 state.json 直接冒成 traceback。

而 `chapter_commit_service.apply_projections()` 与 `ssot rebuild` 里的调用
是包在 `try/except: logger.warning` 里的——**那边更糟**：投影静默不生成，
用户只看到一条 warning，`story/` 下的 5 个 markdown 文件停在旧版本。

## 关于"应该怎么处理"的分歧

这里有个真实的设计张力，值得写清楚：

- **`context_manager`** 选了 fail-closed 抛异常——因为模型不能在没有状态的
  情况下写作。
- **`renderer`** 是**派生输出**（markdown 投影），它的失败不影响 SSOT 真相。
  抛异常会打断 `apply_projections`（外层虽有 try，但会跳过后续的
  projection_log 记录）。

所以渲染器的正解不是"抛"，也不是"静默返回空 dict"，而是：
**明确报告读不到 + 返回空结果让调用方继续**，并且把原因交给调用方展示。
CLI 需要据此非零退出并打印可读原因。

本文件钉住：
1. 坏 JSON / BOM 都不抛异常（不打断提交流程）
2. 返回空结果，且原因可查（不能静默）
3. CLI 不输出 traceback 且非零退出
4. 正常路径不变
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.state_projection_renderer import render_all_projections  # noqa: E402

RENDERER = SCRIPTS / "data_modules" / "state_projection_renderer.py"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / ".webnovel").mkdir(parents=True)
    return tmp_path


def _state(project: Path, payload: dict) -> None:
    (project / ".webnovel" / "state.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TestNormalPath:
    def test_renders_all_five(self, project):
        _state(project, {"progress": {"current_chapter": 1}})
        results = render_all_projections(project)
        assert len(results) == 5, f"应渲染 5 个投影，实际 {sorted(results)}"
        for path in results.values():
            assert path.is_file()

    def test_missing_state_returns_empty(self, project):
        assert render_all_projections(project) == {}

    def test_empty_object_is_valid(self, project):
        _state(project, {})
        assert len(render_all_projections(project)) == 5

    def test_bom_state_is_readable(self, project):
        (project / ".webnovel" / "state.json").write_bytes(
            '\ufeff{"progress": {"current_chapter": 2}}'.encode("utf-8"))
        results = render_all_projections(project)
        assert len(results) == 5, (
            "带 BOM 的合法 state.json 被判成损坏，5 个投影一个都没生成"
        )


class TestCorruptStateDoesNotCrash:
    """渲染器是派生输出，失败不该打断提交流程。"""

    def _corrupt(self, project: Path) -> None:
        (project / ".webnovel" / "state.json").write_text("{ bad", encoding="utf-8")

    def test_does_not_raise(self, project):
        self._corrupt(project)
        try:
            render_all_projections(project)
        except Exception as exc:
            pytest.fail(
                f"坏 state.json 时渲染器抛了 {type(exc).__name__}: {exc}。"
                f"它在 apply_projections 里被调用，抛异常会打断后续的"
                f"projection_log 记录。"
            )

    def test_returns_empty(self, project):
        self._corrupt(project)
        assert render_all_projections(project) == {}

    def test_reason_is_available(self, project):
        """不能静默——调用方要能知道「为什么没生成」。"""
        self._corrupt(project)
        from data_modules.state_projection_renderer import render_error_detail

        render_all_projections(project)
        diag = render_error_detail(project)
        assert diag, (
            "坏 state.json 时渲染器静默返回 {}，没有任何可查的原因。"
            "CLI 与 apply_projections 都无从告知用户。"
        )
        assert "state.json" in str(diag) or "解析" in str(diag) or "读取" in str(diag)


class TestErrorDetailDoesNotLeakBetweenCalls:
    """`render_error_detail` 不能受前一次调用影响（不能用模块级全局）。"""

    def test_healthy_after_corrupt(self, project):
        from data_modules.state_projection_renderer import render_error_detail

        (project / ".webnovel" / "state.json").write_text("{ bad", encoding="utf-8")
        render_all_projections(project)
        assert render_error_detail(project) is not None

        # 修好之后必须立即恢复正常——若用全局变量，这里可能残留旧错误
        _state(project, {"progress": {}})
        render_all_projections(project)
        assert render_error_detail(project) is None, (
            "state.json 修好之后仍报旧错误——错误状态被全局变量串扰了"
        )

    def test_corrupt_after_healthy(self, project):
        from data_modules.state_projection_renderer import render_error_detail

        _state(project, {"progress": {}})
        render_all_projections(project)
        assert render_error_detail(project) is None

        (project / ".webnovel" / "state.json").write_text("{ bad", encoding="utf-8")
        assert render_error_detail(project) is not None


class TestCliDoesNotCrash:
    """CLI 层面：坏 state.json 不能输出 traceback。"""

    def _run(self, project: Path):
        import subprocess

        return subprocess.run(
            [sys.executable, str(RENDERER), "--project-root", str(project)],
            capture_output=True, text=True, timeout=120,
        )

    def test_no_traceback(self, project):
        (project / ".webnovel" / "state.json").write_text("{ bad", encoding="utf-8")
        r = self._run(project)
        combined = r.stdout + r.stderr
        assert "Traceback (most recent call last)" not in combined, (
            f"渲染器 CLI 输出 traceback：\n{combined[-1000:]}"
        )

    def test_nonzero_exit(self, project):
        (project / ".webnovel" / "state.json").write_text("{ bad", encoding="utf-8")
        r = self._run(project)
        assert r.returncode != 0, "渲染失败却返回 0，脚本链会以为成功"

    def test_error_message_is_readable(self, project):
        (project / ".webnovel" / "state.json").write_text("{ bad", encoding="utf-8")
        r = self._run(project)
        combined = r.stdout + r.stderr
        assert "state.json" in combined, f"错误信息没指明 state.json：{combined[-600:]}"

    def test_success_exit_zero(self, project):
        _state(project, {"progress": {}})
        r = self._run(project)
        assert r.returncode == 0, f"正常渲染却失败：{r.stdout}{r.stderr}"


class TestWebnovelStateRenderDoesNotCrash:
    """`webnovel.py state render` 是用户最可能直接敲的命令。"""

    def test_no_traceback(self, project):
        import subprocess

        (project / ".webnovel" / "state.json").write_text("{ bad", encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(SCRIPTS / "webnovel.py"), "state", "render",
             "--project-root", str(project)],
            capture_output=True, text=True, timeout=180,
            cwd=str(SCRIPTS.parent),
        )
        combined = r.stdout + r.stderr
        assert "Traceback (most recent call last)" not in combined, (
            f"state render 输出 traceback：\n{combined[-1000:]}"
        )
