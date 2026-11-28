#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提交前的仓库自检：本地产物不得进 git，构建产物不得被挡在 git 外。

为什么需要：这两件事都是**静默失败**，而且都真实发生过。

* 前者：`.opencode/.webnovel-current-project` 里存的是构建机的**绝对路径**
  （`E:\\workspace\\webnovel2\\凡尘之舞`）。它曾被 npm 包带出去过——
  `build-bundle.js` 至今保留着针对它的 `LEAK_FILES` 哨兵。
* 后者：`.gitignore` 里一条 `dist/` 加一条**永不命中**的例外路径
  （`webnovel-writer/dashboard/frontend/dist/`，那是早已废弃的旧布局），
  导致整个前端产物被静默排除在 git 之外——CI 的 `git diff --quiet -- dist`
  因此长期无从校验。

两者都不会让任何测试变红，只会让"提交/打包"这件事在某次发布时才炸。
本文件用 `git check-ignore` 把这两条性质钉住。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-c", f"safe.directory={REPO_ROOT.as_posix()}", *args],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=60,
    )


def _git_available() -> bool:
    try:
        return _git("rev-parse", "--git-dir").returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


pytestmark = pytest.mark.skipif(not _git_available(), reason="需要 git 仓库")


def ignored(path: str) -> bool:
    """`git check-ignore` 的退出码：0 被忽略、1 未被忽略、128 出错。"""
    return _git("check-ignore", "-q", path).returncode == 0


class TestLocalArtifactsAreIgnored:
    @pytest.mark.parametrize("path", [
        ".venv/pyvenv.cfg",
        "node_modules/x/index.js",
        ".opencode/dashboard/frontend/node_modules/x.js",
        "__pycache__/x.cpython-312.pyc",
        ".tmp/analysis/BASELINE.md",
        ".pytest_cache/v/cache/lastfailed",
    ])
    def test_ignored(self, path):
        assert ignored(path), f"{path} 没被 .gitignore 挡住——本地产物会进提交"

    def test_pointer_file_with_absolute_path_is_ignored(self):
        """`.webnovel-current-project` 存的是构建机绝对路径，绝不能进 git。"""
        assert ignored(".opencode/.webnovel-current-project")

    def test_pointer_file_not_tracked(self):
        """即使被 ignore，也要确认它从未被 `git add -f` 夹带进来。"""
        result = _git("ls-files", "--error-unmatch", ".opencode/.webnovel-current-project")
        assert result.returncode != 0, "指针文件已被跟踪——它含绝对路径，必须移除"


class TestBuildArtifactsAreNotIgnored:
    """方向相反的另一半：该进 git 的东西不能被挡掉。"""

    def test_frontend_dist_is_tracked_path(self):
        """`dist/` 被忽略，但 `.opencode/dashboard/frontend/dist/` 有例外。

        CI 的 frontend job 最后一步 `git diff --quiet -- dist` 依赖它真的在 git 里。
        """
        assert not ignored(".opencode/dashboard/frontend/dist/index.html"), (
            "前端构建产物被 .gitignore 挡住了——CI 的 dist 无差异校验会形同虚设"
        )

    def test_agents_md_is_not_ignored(self):
        """npm 包与 V2 发现路径都依赖它。"""
        assert not ignored("AGENTS.md")
