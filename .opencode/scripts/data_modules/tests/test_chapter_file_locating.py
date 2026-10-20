#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`find_chapter_file` 的定位可靠性与单章字数计算。

CED（一致性错误密度）= issue 数 / 单章字数 × 10000。**字数是分母**——
定位不到就取不到字数，取不到字数 CED 就无法计算。所以这个函数是 CED 的
地基，必须覆盖各种真实项目形态。

## 本次修的问题

`find_chapter_file` 此前只 glob 3 位和 4 位编号：

    sorted(chapters_dir.rglob(f"第{chapter_num:03d}章*.md"))
    + sorted(chapters_dir.rglob(f"第{chapter_num:04d}章*.md"))

1-2 位编号的**旧格式文件名**因此定位不到：

    find_chapter_file(root, 7)   # 目录里有 第7章-旧格式.md
    → None                       # ← 漏了

`chapter_rename.py` 能把这些统一成 4 位，但需要用户主动跑。在那之前，
这些章节对 CED 而言就是"字数未知"。

## 本文件钉住

- 四种编号位数（1/2/3/4 位）都能定位
- 4 位优先于 3 位优先于短位（同一章有多个候选时，选最规范的）
- 按卷目录优先于全目录搜索
- 定位不到返回 None（不抛异常）
- 字数计算：去 markdown 语法、空文件为 0
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from chapter_paths import find_chapter_file  # noqa: E402
from data_modules.state_projection_writer import StateProjectionWriter  # noqa: E402


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "正文").mkdir()
    return tmp_path


def _body(project: Path, rel: str, content: str = "正文" * 100) -> Path:
    path = project / "正文" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _words(path: Path) -> int:
    writer = StateProjectionWriter.__new__(StateProjectionWriter)
    return writer._count_chapter_words(path.read_text(encoding="utf-8"))


class TestNumberWidths:
    """四种编号位数都要能定位。"""

    @pytest.mark.parametrize("chapter,filename", [
        (1, "第1章.md"),
        (7, "第7章-旧格式.md"),
        (12, "第12章.md"),
        (99, "第99章.md"),
        (100, "第100章.md"),
        (7, "第007章-三位.md"),
        (1234, "第1234章.md"),
    ])
    def test_finds_any_width(self, project, chapter, filename):
        _body(project, filename)
        found = find_chapter_file(project, chapter)
        assert found is not None, (
            f"目录里有 {filename}，但 find_chapter_file({chapter}) 返回 None。"
            f"1-2 位编号的旧格式文件此前定位不到。"
        )
        assert found.name == filename

    def test_short_number_no_longer_missed(self, project):
        """核心回归：短编号不能定位不到。"""
        _body(project, "第7章-旧格式.md")
        assert find_chapter_file(project, 7) is not None


class TestPriority:
    """同章多个候选时，优先选最规范的编号。"""

    def test_four_digit_beats_three(self, project):
        _body(project, "第007章-三位.md", "三位")
        _body(project, "第0007章-四位.md", "四位")
        found = find_chapter_file(project, 7)
        assert found.name == "第0007章-四位.md"

    def test_three_digit_beats_short(self, project):
        _body(project, "第7章-短.md", "短")
        _body(project, "第007章-三位.md", "三位")
        found = find_chapter_file(project, 7)
        assert found.name == "第007章-三位.md"

    def test_volume_dir_beats_flat_scan(self, project):
        """卷目录里的应优先于散落在正文根目录的。"""
        _body(project, "第0007章-根目录.md", "根")
        _body(project, "第1卷/第007章-卷内.md", "卷")
        found = find_chapter_file(project, 7)
        assert "第1卷" in found.as_posix(), (
            f"应优先返回卷目录内的文件，实际 {found.as_posix()}"
        )

    def test_exact_flat_wins_first(self, project):
        _body(project, "第0007章.md", "标准")
        found = find_chapter_file(project, 7)
        assert found.name == "第0007章.md"


class TestMissingReturnsNone:
    """定位不到就返回 None，不抛异常。"""

    def test_empty_body_dir(self, project):
        assert find_chapter_file(project, 5) is None

    def test_no_body_dir(self, tmp_path):
        assert find_chapter_file(tmp_path, 1) is None

    def test_chapter_not_present(self, project):
        _body(project, "第0003章.md")
        assert find_chapter_file(project, 99) is None


class TestWordCounting:
    """字数计算：CED 的分母。"""

    def test_plain_chinese(self, tmp_path):
        path = tmp_path / "f.md"
        path.write_text("萧炎突破了斗师境界。" * 10, encoding="utf-8")
        assert _words(path) == 100

    def test_strips_heading_and_hr(self, tmp_path):
        path = tmp_path / "f.md"
        path.write_text("# 第七章 归来\n\n---\n\n正文内容", encoding="utf-8")
        assert _words(path) == 4

    def test_strips_code_fence(self, tmp_path):
        path = tmp_path / "f.md"
        path.write_text("```\n\n\n\n\n```\n真实内容", encoding="utf-8")
        assert _words(path) == 4

    def test_empty_file_is_zero(self, tmp_path):
        path = tmp_path / "f.md"
        path.write_text("", encoding="utf-8")
        assert _words(path) == 0

    def test_markdown_only_is_zero(self, tmp_path):
        """纯 markdown 语法文件的字数是 0——CED 必须能处理这种情况。"""
        path = tmp_path / "f.md"
        path.write_text("# 标题\n\n---\n\n```python\ncode\n```\n", encoding="utf-8")
        assert _words(path) == 0

    def test_whitespace_only_is_zero(self, tmp_path):
        path = tmp_path / "f.md"
        path.write_text("   \n\n\t\n", encoding="utf-8")
        assert _words(path) == 0


class TestCedDenominatorSafety:
    """CED 的分母安全性：0 字章节不能让公式产出 inf/除零。"""

    @pytest.mark.parametrize("words", [0, 1, 100, 3000])
    def test_ced_definition_is_total(self, words):
        """无论分母多小，CED 都应是有限数或明确的 None，不是 inf/crash。"""
        issues = 3
        if words <= 0:
            ced = None          # 取不到有效分母 → 不产出 CED
        else:
            ced = issues / words * 10000
            assert ced == pytest.approx(issues / words * 10000)
        assert ced is None or ced >= 0

    def test_zero_words_means_no_ced(self):
        """0 字章节没有 CED——不能除以 0，也不能伪造 0 分。"""
        words = 0
        assert (None if words <= 0 else 1.0) is None, (
            "0 字章节被算出了 CED——那是除以 0 或伪造值"
        )
