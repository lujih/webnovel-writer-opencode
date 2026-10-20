#!/usr/bin/env python3
"""
Chapter file path helpers.

This project has seen multiple chapter filename conventions:
1) Legacy flat layout: 正文/第0007章.md
2) Volume layout:    正文/第1卷/第007章-章节标题.md

To keep scripts robust, always resolve chapter files via these helpers instead of hardcoding a format.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional


_CHAPTER_NUM_RE = re.compile(r"第(?P<num>\d+)章")
_OUTLINE_HEADING_RE = re.compile(r"^#{1,6}\s*第\s*(?P<num>\d+)\s*章[：:]\s*(?P<title>.+?)\s*$", re.MULTILINE)
_SPLIT_OUTLINE_FILENAME_RE = re.compile(r"^第0*(?P<num>\d+)章[-—_ ]+(?P<title>.+?)\.md$")


def volume_num_for_chapter(chapter_num: int, *, chapters_per_volume: int = 50) -> int:
    if chapter_num <= 0:
        raise ValueError("chapter_num must be >= 1")
    return (chapter_num - 1) // chapters_per_volume + 1


def extract_chapter_num_from_filename(filename: str) -> Optional[int]:
    m = _CHAPTER_NUM_RE.search(filename)
    if not m:
        return None
    try:
        return int(m.group("num"))
    except ValueError:
        return None


def _safe_title_for_filename(title: str) -> str:
    cleaned = title.strip()
    if not cleaned:
        return ""

    try:
        from security_utils import sanitize_filename
    except ImportError:  # pragma: no cover
        from scripts.security_utils import sanitize_filename

    safe_title = sanitize_filename(cleaned, max_length=60)
    return "" if safe_title == "unnamed_entity" else safe_title


def _extract_title_from_outline_text(outline_text: str, chapter_num: int) -> str:
    for match in _OUTLINE_HEADING_RE.finditer(outline_text):
        if int(match.group("num")) != chapter_num:
            continue
        return _safe_title_for_filename(match.group("title"))
    return ""


def _extract_title_from_split_outline_filename(outline_dir: Path, chapter_num: int) -> str:
    patterns = [
        f"第{chapter_num}章*.md",
        f"第{chapter_num:02d}章*.md",
        f"第{chapter_num:03d}章*.md",
        f"第{chapter_num:04d}章*.md",
    ]
    for pattern in patterns:
        for path in sorted(outline_dir.glob(pattern)):
            match = _SPLIT_OUTLINE_FILENAME_RE.match(path.name)
            if not match:
                continue
            if int(match.group("num")) != chapter_num:
                continue
            title = _safe_title_for_filename(match.group("title"))
            if title:
                return title
    return ""


def extract_chapter_title(project_root: Path, chapter_num: int) -> str:
    """从详细大纲提取章节标题，用于生成更直观的章节文件名。"""
    try:
        from chapter_outline_loader import load_chapter_outline
    except ImportError:  # pragma: no cover
        from scripts.chapter_outline_loader import load_chapter_outline

    try:
        outline_text = load_chapter_outline(project_root, chapter_num, max_chars=None)
    except FileNotFoundError:
        outline_text = ""
    if outline_text and not outline_text.startswith("⚠️"):
        title = _extract_title_from_outline_text(outline_text, chapter_num)
        if title:
            return title

    outline_dir = project_root / "大纲"
    if outline_dir.exists():
        return _extract_title_from_split_outline_filename(outline_dir, chapter_num)
    return ""


def _build_chapter_filename(project_root: Path, chapter_num: int, *, use_volume_layout: bool) -> str:
    padded = f"{chapter_num:03d}" if use_volume_layout else f"{chapter_num:04d}"
    title = extract_chapter_title(project_root, chapter_num)
    if title:
        return f"第{padded}章-{title}.md"
    return f"第{padded}章.md"


def find_chapter_file(project_root: Path, chapter_num: int) -> Optional[Path]:
    """
    Find an existing chapter file for chapter_num under project_root/正文.
    Returns the first match (stable sorted order) or None if not found.
    """
    chapters_dir = project_root / "正文"
    if not chapters_dir.exists():
        return None

    legacy = chapters_dir / f"第{chapter_num:04d}章.md"
    if legacy.exists():
        return legacy

    vol_dir = chapters_dir / f"第{volume_num_for_chapter(chapter_num)}卷"
    if vol_dir.exists():
        for c in (sorted(vol_dir.glob(f"第{chapter_num:04d}章*.md"))
                  + sorted(vol_dir.glob(f"第{chapter_num:03d}章*.md"))
                  + sorted(vol_dir.glob(f"第{chapter_num}章*.md"))):
            if c.is_file():
                return c

    # Fallback: search anywhere under 正文/ (supports custom layouts)
    #
    # 三组 glob 按**规范程度**排序：4 位（项目标准，`default_chapter_draft_path`
    # 与 `chapter_rename` 都以此为目标）→ 3 位 → 1-2 位旧格式。
    #
    # 此前顺序是 3 位在前，于是同一章同时存在 `第007章.md` 与 `第0007章.md`
    # 时选中的是前者——与"4 位是标准"矛盾。
    #
    # 1-2 位那一组覆盖旧格式文件（如 `第7章-旧格式.md`）：此前完全漏掉，
    # 而 `chapter_rename` 需要用户主动跑，在那之前这些章节对依赖本章定位的
    # 功能（字数统计、CED 计算）而言就是"定位不到"。
    for c in (sorted(chapters_dir.rglob(f"第{chapter_num:04d}章*.md"))
              + sorted(chapters_dir.rglob(f"第{chapter_num:03d}章*.md"))
              + sorted(chapters_dir.rglob(f"第{chapter_num}章*.md"))):
        if c.is_file():
            return c

    return None


def default_chapter_draft_path(project_root: Path, chapter_num: int, *, use_volume_layout: bool = False) -> Path:
    """
    Preferred draft path when creating a new chapter file.

    Args:
        project_root: 项目根目录
        chapter_num: 章节号
        use_volume_layout: True 使用卷布局 (正文/第N卷/第NNN章-章节标题.md)，False 使用平坦布局 (正文/第NNNN章-章节标题.md)

    Default is flat layout. If the detailed outline already has a chapter title,
    append it to the filename for better discoverability.
    """
    if use_volume_layout:
        vol_dir = project_root / "正文" / f"第{volume_num_for_chapter(chapter_num)}卷"
        return vol_dir / _build_chapter_filename(project_root, chapter_num, use_volume_layout=True)
    else:
        return project_root / "正文" / _build_chapter_filename(project_root, chapter_num, use_volume_layout=False)


def parse_chapter_range(spec: str) -> list[int]:
    """把 `'5-12'` / `'5,7,9-12'` 解析成升序去重的章节号列表。

    服务两个入口，其中 `delete-chapters` 是**破坏性操作**，所以这里的报错
    质量比"能跑"重要：

    - `chapter_delete_service.cmd_delete_chapters()` —— 删章节
    - `orchestrate.cmd_orchestrate()` —— 批量写作

    畸形输入一律抛 `ValueError`（带正确格式提示），**不返回空列表**。
    此前 `'5-2'`（倒序）与 `'5--12'` 都静默变成 `[]`，调用方只看到
    "no chapters specified"，用户却以为自己指定了范围——对删除操作来说，
    "我以为删了 5 到 12 章"和"什么都没删"之间的误解很危险。

    Raises:
        ValueError: 格式非法、范围倒序、或章节号 < 1
    """
    if not spec or not spec.strip():
        raise ValueError("章节范围不能为空。正确格式：'5'、'5-12' 或 '5,7,9-12'")

    chapters: list[int] = []
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError(
                f"章节范围 {spec!r} 里有空项。正确格式：'5'、'5-12' 或 '5,7,9-12'"
            )

        if "-" in part:
            # 用 split 得到恰好两段；三段及以上（如 '5--12'、'-3--1'）都算非法。
            pieces = part.split("-")
            if len(pieces) != 2:
                raise ValueError(
                    f"无法解析章节范围 {part!r}（来自 {spec!r}）。"
                    f"范围必须形如 '5-12'，且两端都是正整数"
                )
            a_raw, b_raw = pieces
            try:
                a, b = int(a_raw), int(b_raw)
            except ValueError:
                raise ValueError(
                    f"无法解析章节范围 {part!r}（来自 {spec!r}）。"
                    f"范围两端必须是正整数，如 '5-12'"
                ) from None
            if a < 1 or b < 1:
                raise ValueError(
                    f"章节号必须 ≥ 1，收到 {part!r}（来自 {spec!r}）"
                )
            if a > b:
                raise ValueError(
                    f"章节范围倒序：{part!r}（来自 {spec!r}）。应写成 '{b}-{a}'"
                )
            chapters.extend(range(a, b + 1))
        else:
            try:
                num = int(part)
            except ValueError:
                raise ValueError(
                    f"无法解析章节号 {part!r}（来自 {spec!r}）。"
                    f"必须是正整数，或形如 '5-12' 的范围"
                ) from None
            if num < 1:
                raise ValueError(f"章节号必须 ≥ 1，收到 {num}（来自 {spec!r}）")
            chapters.append(num)

    return sorted(set(chapters))


