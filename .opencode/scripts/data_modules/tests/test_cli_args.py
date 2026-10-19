#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`cli_args` 的输入健壮性测试。

`cli_args` 被 10+ 个模块依赖（入度 8），其中 `load_json_arg` 服务
**20 多个 JSON 写入命令**（`index_manager` / `sql_state_manager` /
`state_manager` / `rag_adapter` / `style_sampler` / `memory.store` …）。
它一出问题，整条写入链路都受影响。

两个实测缺陷：

**1. 带 BOM 的 JSON 文件无法加载。**
Windows 记事本、VS Code「UTF-8 with BOM」、Excel 导出的 CSV 转 JSON，
全都带 BOM。而本项目的目标用户是中文网文作者，主要在 Windows 上工作。

    $ echo '{"chapter":5}' > payload.json     # 记事本保存
    $ webnovel.py ... --data @payload.json
    JSONDecodeError: Unexpected UTF-8 BOM (decode using utf-8-sig)

仓库里已有 7 处用 `encoding="utf-8-sig"` 处理这个（`chapter_commit.py:20`、
`review_pipeline.py:210`、`skill_runner.py:191`…），说明这是**既有约定**，
`cli_args.load_json_arg` 只是漏了。

**2. 悬空的 `--project-root` 会吞掉后面的参数。**

    normalize_global_project_root(["cmd", "--project-root", "--chapter", "5"])
      → ["--project-root", "--chapter", "cmd", "5"]   ← 把 --chapter 当成路径了

`--project-root` 后面跟的是另一个 `--flag` 时，它不是值，是缺失值。
原实现无条件取 `argv[i+1]`，于是 `--chapter` 被当成路径、`cmd` 与 `5`
被重排到后面，argparse 报出的错误与真实原因（少写了路径）完全对不上。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.cli_args import load_json_arg, normalize_global_project_root  # noqa: E402


class TestLoadJsonArgReadsFiles:
    """基本功能不变。"""

    def test_plain_json_file(self, tmp_path):
        p = tmp_path / "payload.json"
        p.write_text('{"chapter": 5}', encoding="utf-8")
        assert load_json_arg("@" + str(p)) == {"chapter": 5}

    def test_inline_json_string(self):
        assert load_json_arg('{"a": 1}') == {"a": 1}

    def test_inline_json_array(self):
        assert load_json_arg('[1, 2, 3]') == [1, 2, 3]

    def test_stdin_via_dash(self, monkeypatch):
        import io
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"from": "stdin"}'))
        assert load_json_arg("@-") == {"from": "stdin"}

    def test_at_without_path_raises(self):
        with pytest.raises(ValueError, match="without path"):
            load_json_arg("@")

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_json_arg("@" + str(tmp_path / "nope.json"))

    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            load_json_arg("{not json")


class TestBOMIsTolerated:
    """Windows 编辑器默认写法必须能用。"""

    def test_utf8_bom_file(self, tmp_path):
        p = tmp_path / "bom.json"
        p.write_bytes(b'\xef\xbb\xbf{"chapter": 5}')
        assert load_json_arg("@" + str(p)) == {"chapter": 5}, (
            "带 BOM 的 JSON 文件（Windows 记事本/VS Code 默认保存格式）"
            "无法加载。仓库已有 7 处用 utf-8-sig 处理，此处漏了。"
        )

    def test_utf8_bom_inline_string(self):
        assert load_json_arg('\ufeff{"a": 1}') == {"a": 1}

    def test_bom_with_chinese_content(self, tmp_path):
        p = tmp_path / "bom_cn.json"
        p.write_bytes('\ufeff{"标题": "第一章"}'.encode("utf-8"))
        assert load_json_arg("@" + str(p)) == {"标题": "第一章"}

    def test_no_bom_still_works(self, tmp_path):
        p = tmp_path / "plain.json"
        p.write_bytes('{"a": 1}'.encode("utf-8"))
        assert load_json_arg("@" + str(p)) == {"a": 1}


class TestDanglingProjectRootIsNotASwallowedValue:
    """`--project-root` 后面跟的是另一个 flag 时，那是缺失值。"""

    def test_flag_value_that_is_another_flag(self):
        result = normalize_global_project_root(
            ["cmd", "--project-root", "--chapter", "5"]
        )
        # 不能把 '--chapter' 当成 project-root 的值
        assert "--chapter" not in result[0:2], (
            f"'--chapter' 被当成了 --project-root 的路径值：{result}"
        )

    def test_order_preserved_when_dangling(self):
        result = normalize_global_project_root(
            ["cmd", "--project-root", "--chapter", "5"]
        )
        assert result == ["cmd", "--project-root", "--chapter", "5"], (
            f"缺失值时应原样返回，交给 argparse 报错，而不是重排：{result}"
        )


class TestNormalizePreservesMeaning:
    """现有的正常路径不能改坏。"""

    @pytest.mark.parametrize("argv,expected", [
        (["get", "--project-root", "/x"], ["--project-root", "/x", "get"]),
        (["--project-root", "/x", "get"], ["--project-root", "/x", "get"]),
        (["--project-root=/x", "cmd"], ["--project-root", "/x", "cmd"]),
        (["cmd", "--chapter", "5"], ["cmd", "--chapter", "5"]),  # 无该 flag
    ])
    def test_common_forms(self, argv, expected):
        assert normalize_global_project_root(argv) == expected

    def test_arguments_after_are_preserved(self):
        result = normalize_global_project_root(
            ["cmd", "--project-root", "/x", "--chapter", "5", "--flag"]
        )
        assert result == ["--project-root", "/x", "cmd", "--chapter", "5", "--flag"]

    def test_last_occurrence_wins(self):
        result = normalize_global_project_root(
            ["cmd", "--project-root", "/a", "--project-root", "/b"]
        )
        assert result == ["--project-root", "/b", "cmd"]

    def test_path_value_starting_with_dash_is_unusual_but_ambiguous(self):
        """以 '-' 开头的路径很罕见；宁可按「缺值」处理，避免吞掉真实 flag。

        （POSIX 下合法路径很少以 '-' 开头；真需要时用户可写
        `--project-root=./-weird`。）
        """
        result = normalize_global_project_root(["cmd", "--project-root", "-x"])
        assert result == ["cmd", "--project-root", "-x"]
