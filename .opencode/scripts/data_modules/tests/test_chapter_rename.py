#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`chapter_rename` 的目录定位与安全边界。

这个工具会**重命名用户的正文文件**，140 行、零测试覆盖。它的 docstring 写着：

    将 `第060章-标题.md` 或 `第060章.md` 统一为 `第0060章-标题.md`
    python chapter_rename.py --project-root /path/to/project --dry-run

## 缺陷一（致命）：扫描 `story/`，而正文在 `正文/`

`main()` 里：

    story_dir = root / "story"
    story_renames = scan_chapters(story_dir, recursive=args.recursive)

但本项目的正文权威位置是 `正文/`——`chapter_paths.py:117` 明写：

    chapters_dir = project_root / "正文"

`story/` 是 markdown **投影**目录（`state_projection_renderer` 输出
`世界观状态.md` / `伏笔面板.md` / `章节摘要.md` 等），里面根本没有章节正文。

实测：

    scan_chapters(root / "story")     → 0 个
    scan_chapters(root / "正文")      → 1 个（第060章-初入宗门.md）

也就是说这个工具**永远不会处理用户的章节文件**——它对着一个不含章节正文的
目录做"章节编号统一"，用户跑完只看到"无需重命名"。

## 缺陷二：`story/` 里若恰好有匹配的文件，会被误改

反过来更危险：`story/` 里若有形如 `chapter_1.md` 的文件（投影或用户自建），
它会被重命名成 `chapter_0001.md`——**改的是投影目录，不是正文**，而
`state_projection_renderer` 下次渲染不会清理旧文件名，于是留下一份孤儿文件。

## 缺陷三：5 位等畸形编号静默跳过

`_RE_CHINESE = r"^第(\\d{1,3})章(-.+)?(\\.md)$"` 只认 1–3 位。`第12345章.md`
既不在"待改名"里，也不报任何警告。用户以为已经统一了，实际有一个畸形文件
一直没被发现。

## 本文件钉住

- 默认扫描 `正文/`（权威位置），而不是 `story/`
- `story/` 不再被当作章节目录
- 畸形编号（位数 > 4）产生警告，不静默跳过
- 重命名本身的行为（补零、目标已存在时跳过、dry-run 不落地）不变
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.chapter_rename import (  # noqa: E402
    apply_renames,
    scan_chapters,
    scan_story_system,
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "正文").mkdir()
    return tmp_path


def _touch(path: Path, content: str = "正文内容") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class TestScanChaptersUnchanged:
    """既有补零行为不能改坏。"""

    @pytest.mark.parametrize("name,expected", [
        ("第060章-初入宗门.md", "第0060章-初入宗门.md"),
        ("第60章-初入宗门.md", "第0060章-初入宗门.md"),
        ("第7章-开端.md", "第0007章-开端.md"),
        ("第060章.md", "第0060章.md"),
    ])
    def test_pads_to_four(self, project, name, expected):
        _touch(project / "正文" / name)
        renames = scan_chapters(project / "正文")
        assert [(o.name, n.name) for o, n in renames] == [(name, expected)]

    @pytest.mark.parametrize("name", [
        "第0060章-已标准.md",
        "第1000章-已标准.md",
        "第0060章.md",
    ])
    def test_already_padded_untouched(self, project, name):
        _touch(project / "正文" / name)
        assert scan_chapters(project / "正文") == []

    def test_recursive_finds_volume_dirs(self, project):
        _touch(project / "正文" / "第1卷" / "第007章-标题.md")
        assert scan_chapters(project / "正文", recursive=True), (
            "递归模式应能找到 正文/第N卷/ 下的章节"
        )

    def test_missing_dir_returns_empty(self, project):
        assert scan_chapters(project / "不存在的目录") == []


class TestMalformedNumbersAreReported:
    """缺陷三：位数超出范围时不能静默。"""

    def test_five_digit_is_flagged(self, project):
        from data_modules import chapter_rename as mod

        _touch(project / "正文" / "第12345章-标题.md")
        renames = mod.scan_chapters(project / "正文")
        assert renames or mod.last_warnings, (
            "第12345章-标题.md 既不在待改名列表里、也没有任何警告——"
            "用户以为编号已统一，实际有一个畸形文件一直没被发现"
        )

    def test_warning_mentions_filename(self, project):
        from data_modules import chapter_rename as mod

        _touch(project / "正文" / "第12345章-标题.md")
        mod.scan_chapters(project / "正文")
        assert any("第12345章" in w for w in mod.last_warnings), (
            f"警告里没指明具体文件：{mod.last_warnings}"
        )

    def test_warnings_cleared_between_scans(self, project):
        """连续两次扫描不能累积上一次的警告。"""
        from data_modules import chapter_rename as mod

        _touch(project / "正文" / "第12345章-标题.md")
        mod.scan_chapters(project / "正文")
        assert mod.last_warnings

        other = project / "另一本书" / "正文"
        other.mkdir(parents=True)
        mod.scan_chapters(other)
        assert mod.last_warnings == [], "上一次扫描的警告残留到了下一次"

    def test_well_formed_has_no_warning(self, project):
        from data_modules import chapter_rename as mod

        _touch(project / "正文" / "第060章-标题.md")
        mod.scan_chapters(project / "正文")
        assert mod.last_warnings == []


class TestMainScansBodyDirNotStoryDir:
    """缺陷一/二：CLI 必须扫 正文/，不能扫 story/。"""

    def _run(self, project: Path, *extra: str):
        import subprocess

        return subprocess.run(
            [sys.executable, str(SCRIPTS / "data_modules" / "chapter_rename.py"),
             "--project-root", str(project), *extra],
            capture_output=True, text=True, timeout=120,
        )

    def test_cli_finds_chapters_in_body_dir(self, project):
        _touch(project / "正文" / "第060章-初入宗门.md")
        r = self._run(project, "--dry-run")
        combined = r.stdout + r.stderr
        assert "第0060章" in combined, (
            f"CLI 没有处理 正文/ 下的章节文件。输出：\n{combined[-800:]}\n"
            f"它扫的是 story/（投影目录），而正文在 正文/。"
        )

    def test_cli_does_not_touch_story_dir(self, project):
        """story/ 是投影目录，里面的文件不该被当章节重命名。"""
        (project / "story").mkdir()
        _touch(project / "story" / "chapter_1.md", "这是投影，不是正文")
        r = self._run(project)
        assert not (project / "story" / "chapter_0001.md").exists(), (
            "story/ 下的文件被当成章节重命名了——那是 markdown 投影目录，"
            "不是正文位置"
        )
        assert (project / "story" / "chapter_1.md").exists(), "原文件被改名了"

    def test_cli_reports_nothing_when_body_empty(self, project):
        r = self._run(project, "--dry-run")
        combined = r.stdout + r.stderr
        assert "Traceback" not in combined


class TestApplyRenamesSafety:
    """重命名的既有安全边界不变。"""

    def test_dry_run_does_not_touch_disk(self, project):
        old = _touch(project / "正文" / "第060章-x.md")
        new = old.parent / "第0060章-x.md"
        apply_renames([(old, new)], dry_run=True)
        assert old.exists() and not new.exists()

    def test_skips_when_target_exists(self, project):
        old = _touch(project / "正文" / "第060章-x.md", "旧")
        new = _touch(project / "正文" / "第0060章-x.md", "新")
        count = apply_renames([(old, new)], dry_run=False)
        assert count == 0, "目标已存在时应跳过，不能覆盖"
        assert new.read_text(encoding="utf-8") == "新", "目标文件被覆盖了"
        assert old.exists(), "源文件应保留"

    def test_renames_when_clear(self, project):
        old = _touch(project / "正文" / "第060章-x.md", "内容")
        new = old.parent / "第0060章-x.md"
        count = apply_renames([(old, new)], dry_run=False)
        assert count == 1
        assert new.exists() and not old.exists()
        assert new.read_text(encoding="utf-8") == "内容"


class TestStorySystemScanUnchanged:
    """.story-system/chapters/ 的扫描行为不变。"""

    def test_pads_json_names(self, project):
        d = project / ".story-system" / "chapters"
        _touch(d / "chapter_60.json", "{}")
        renames = scan_story_system(project / ".story-system")
        assert [(o.name, n.name) for o, n in renames] == [
            ("chapter_60.json", "chapter_0060.json")]

    def test_missing_dir(self, project):
        assert scan_story_system(project / ".story-system") == []
