#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`semver_bump` 的版本推算规则。

## 为什么它值得单测

发版逻辑此前**只存在于 CLAUDE.md 的表格里**（`manifest.yml` 不做 semver
运算、不打 tag），于是「`feat!:` 会发成 v3.0.0」这条承诺从来没被验证过。
把规则落成纯函数之后，每条规则都要有测试钉住——写错的代价是发错版本号，
而 tag 一旦推出就很难收回。

## 一个自动化无法覆盖的边界（写在这里免得被误解）

自动化只能读到 commit message。v3.0.0 的「门禁分层」是**破坏性行为变更**，
但当时的 commit 写的是 `feat:` 而非 `feat!:`——按本模块的规则，那次只会
被算成 MINOR（v2.10.0），MAJOR 是人手动决定的。所以：

**真正的破坏性变更必须在 commit 里标 `feat!:` / `type(scope)!:` / 正文
`BREAKING CHANGE:`**，否则自动化无从得知。已在 CLAUDE.md 写明。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.semver_bump import (  # noqa: E402
    classify,
    format_tag,
    highest_bump,
    main,
    next_version,
    parse_tag,
    split_commits,
)

MAJOR, MINOR, PATCH, NONE = 3, 2, 1, 0


class TestParseTag:
    def test_plain(self):
        assert parse_tag("v3.0.0") == (3, 0, 0)
        assert parse_tag("3.4.2") == (3, 4, 2)

    def test_strips_prerelease_and_build(self):
        assert parse_tag("v3.0.0-20261021.0828") == (3, 0, 0)
        assert parse_tag("v1.2.3-rc.1+build5") == (1, 2, 3)

    @pytest.mark.parametrize("raw", ["", None, "nightly", "v", "vX.Y.Z"])
    def test_non_semver_falls_back_to_zero(self, raw):
        assert parse_tag(raw) == (0, 0, 0)

    def test_partial_version_pads(self):
        assert parse_tag("v5") == (5, 0, 0)
        assert parse_tag("v5.2") == (5, 2, 0)

    def test_rejects_absurd_numbers(self):
        assert parse_tag("v999999999999.0.0") == (0, 0, 0)


class TestFormatTag:
    def test_roundtrip(self):
        assert format_tag((3, 1, 4)) == "v3.1.4"
        assert parse_tag(format_tag((3, 1, 4))) == (3, 1, 4)


class TestClassify:
    def test_feat_is_minor(self):
        assert classify("feat: 新增叙事预报") == MINOR

    def test_feat_with_scope(self):
        assert classify("feat(dashboard): 加面板") == MINOR

    @pytest.mark.parametrize("subject", [
        "fix: 修 null 判断",
        "fix(review): 修得分计算",
        "refactor: 拆函数",
        "perf: 减少一次全表扫描",
    ])
    def test_patch_types(self, subject):
        assert classify(subject) == PATCH

    @pytest.mark.parametrize("subject", [
        "docs: 更新 README",
        "ci: 换 node 版本",
        "chore: bump deps",
        "test: 补用例",
        "simplify: 清理死代码",
        "style: 格式化",
        "build: 调整打包",
    ])
    def test_non_bumping_types(self, subject):
        assert classify(subject) == NONE

    @pytest.mark.parametrize("subject", [
        "feat!: 移除旧 CLI",
        "fix!: 改配置键名",
        "refactor(core)!: 拆包",
    ])
    def test_bang_is_major(self, subject):
        assert classify(subject) == MAJOR

    def test_breaking_change_in_body_is_major(self):
        assert classify("fix: 小改\n\nBREAKING CHANGE: 配置键改名") == MAJOR
        assert classify("chore: 杂项\n\nBREAKING-CHANGE: 同上") == MAJOR

    def test_body_breaking_beats_minor_subject(self):
        assert classify("feat: 新功能\n\nBREAKING CHANGE: 不兼容") == MAJOR

    @pytest.mark.parametrize("subject", [
        "chore: update manifest to v3.0.0 [skip ci]",
        "feat: 临时功能 [no release]",
        "Merge branch 'master' of github.com:x/y",
        "随便一句没有 type 的话",
        "",
    ])
    def test_ignored(self, subject):
        assert classify(subject) == NONE

    def test_skip_ci_detection_is_case_insensitive(self):
        assert classify("chore: x [SKIP CI]") == NONE

    def test_unknown_type_is_none_not_guess(self):
        assert classify("wip: 做到一半") == NONE


class TestHighestBump:
    def test_takes_max(self):
        assert highest_bump(["docs: a", "fix: b", "feat: c"]) == MINOR
        assert highest_bump(["feat: a", "feat!: b", "fix: c"]) == MAJOR
        assert highest_bump(["docs: a", "fix: b"]) == PATCH
        assert highest_bump(["docs: a", "ci: b"]) == NONE
        assert highest_bump([]) == NONE


class TestNextVersion:
    def test_minor(self):
        assert next_version(["feat: x"], "v3.0.0") == "v3.1.0"

    def test_patch(self):
        assert next_version(["fix: x"], "v3.1.4") == "v3.1.5"

    def test_major_resets_lower(self):
        assert next_version(["feat!: x"], "v3.1.4") == "v4.0.0"

    def test_none_when_nothing_to_release(self):
        assert next_version(["docs: x", "ci: y"], "v3.0.0") is None
        assert next_version([], "v3.0.0") is None

    def test_first_release_without_tag(self):
        assert next_version(["feat: x"], "") == "v0.1.0"
        assert next_version(["fix: x"], "") == "v0.0.1"

    def test_prerelease_tag_is_normalized(self):
        assert next_version(["fix: x"], "v3.0.0-20261021.0828") == "v3.0.1"


class TestSplitCommits:
    def test_nul_separated(self):
        text = "feat: a\0fix: b\0docs: c\0"
        assert split_commits(text) == ["feat: a", "fix: b", "docs: c"]

    def test_nul_keeps_multiline_body(self):
        text = "fix: a\n\nBREAKING CHANGE: x\0feat: b\0"
        commits = split_commits(text)
        assert len(commits) == 2
        assert "BREAKING CHANGE" in commits[0]

    def test_blank_line_fallback(self):
        text = "feat: a\n\nfix: b\n"
        assert split_commits(text) == ["feat: a", "fix: b"]

    def test_empty(self):
        assert split_commits("") == []


class TestCli:
    """CLI 的 stdout 必须只含结果——workflow 用 `$(...)` 直接取值。"""

    def _run(self, capsys, monkeypatch, commit_text, current):
        monkeypatch.setattr("sys.stdin", __import__("io").StringIO(commit_text))
        rc = main(["--current", current, "--commits-file", "-"])
        out = capsys.readouterr().out
        assert rc == 0
        return out.strip()

    def test_prints_version_only(self, capsys, monkeypatch):
        assert self._run(capsys, monkeypatch, "feat: x\0", "v3.0.0") == "v3.1.0"

    def test_prints_empty_when_no_release(self, capsys, monkeypatch):
        assert self._run(capsys, monkeypatch, "docs: x\0", "v3.0.0") == ""

    def test_dry_run_keeps_stdout_clean(self, capsys, monkeypatch):
        import io
        monkeypatch.setattr("sys.stdin", io.StringIO("feat: x\0"))
        main(["--current", "v3.0.0", "--commits-file", "-", "--dry-run"])
        captured = capsys.readouterr()
        assert captured.out.strip() == "v3.1.0", "诊断信息不得混进 stdout"
        assert "最高等级" in captured.err


class TestRunsAsScriptWithoutPythonPath:
    """以 CI 的方式直接执行脚本（**不设 PYTHONPATH**）必须能跑。

    ## 为什么这条测试必须存在

    semver.yml 的调用是：

        python .opencode/scripts/data_modules/semver_bump.py --current ... --commits-file ...

    没有设 `PYTHONPATH`。而脚本底部 `from runtime_compat import ...` 需要
    `scripts/` 在 `sys.path` 上。首次推送时 Semver Tag workflow **10 秒就失败**：

        ModuleNotFoundError: No module named 'runtime_compat'

    本地没发现，是因为本地验证时手加了 `PYTHONPATH=.opencode/scripts`——
    「本地跑得通」不等于「CI 跑得通」。

    单元测试（import 模式）覆盖不到这种差异：import 时测试自己的
    `sys.path` 已被本文件顶部的 bootstrap 改过。只有起子进程、给一个干净的
    环境变量，才能复现 CI。
    """

    def _run(self, stdin_text: str, *extra: str):
        import os
        import subprocess

        script = SCRIPTS / "data_modules" / "semver_bump.py"
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        return subprocess.run(
            [sys.executable, str(script), *extra],
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )

    def test_prints_version_without_pythonpath(self):
        proc = self._run("feat: x\0", "--current", "v3.0.0", "--commits-file", "-")
        assert proc.returncode == 0, (
            f"直接执行失败（CI 就是这种调用方式）：\n{proc.stderr}"
        )
        assert proc.stdout.strip() == "v3.1.0"

    def test_prints_empty_without_pythonpath(self):
        proc = self._run("docs: x\0", "--current", "v3.0.0", "--commits-file", "-")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == ""

    def test_help_works_without_pythonpath(self):
        """`--help` 也要能跑——它走的是同一条 import 路径。"""
        proc = self._run("", "--help")
        assert proc.returncode == 0, proc.stderr
        assert "--current" in proc.stdout
