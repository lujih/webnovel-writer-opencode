#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .story_runtime_sources import load_runtime_sources


_CHAPTER_FILE_RE = re.compile(r"chapter_(\d{3,4})")


def _extract_chapter_from_name(path: Path) -> int:
    match = _CHAPTER_FILE_RE.search(path.name)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def _latest_story_system_chapter(project_root: Path) -> int:
    story_root = project_root / ".story-system"
    if not story_root.is_dir():
        return 0

    candidates = []
    for pattern in (
        "chapters/chapter_*.json",
        "reviews/chapter_*.review.json",
        "commits/chapter_*.commit.json",
    ):
        for path in story_root.glob(pattern):
            candidates.append(_extract_chapter_from_name(path))
    return max(candidates or [0])


def _resolve_chapter(project_root: Path, chapter: int | None) -> int:
    if chapter is not None:
        try:
            return max(0, int(chapter))
        except (TypeError, ValueError):
            return 0

    latest_story_system_chapter = _latest_story_system_chapter(project_root)
    state_path = project_root / ".webnovel" / "state.json"
    if not state_path.is_file():
        return latest_story_system_chapter

    # P2 修复：直读 state.json 在并发 os.replace（writer 原子写）中途可能看到
    # 0 字节 / 部分写入。统一走 read_json_safe（容错 + 失败降级 default），
    # 与 state_manager / memory store 的读取约定一致。
    try:
        from security_utils import read_json_safe
    except ImportError:  # pragma: no cover
        from scripts.security_utils import read_json_safe
    state = read_json_safe(state_path, default={})

    try:
        state_chapter = max(0, int(((state.get("progress") or {}).get("current_chapter") or 0)))
    except (TypeError, ValueError):
        state_chapter = 0
    return max(state_chapter, latest_story_system_chapter)


def build_story_runtime_health(project_root: Path, chapter: int | None = None) -> dict[str, Any]:
    project_root = Path(project_root)
    current_chapter = _resolve_chapter(project_root, chapter)
    if current_chapter <= 0:
        return {
            "chapter": 0,
            "mainline_ready": False,
            "fallback_sources": ["chapter_unspecified"],
            "latest_commit_status": "missing",
            "primary_write_source": "chapter_commit",
        }

    snapshot = load_runtime_sources(project_root, current_chapter)
    latest_commit = snapshot.latest_commit or {}
    try:
        from chapter_paths import volume_num_for_chapter
    except ImportError:  # pragma: no cover
        from scripts.chapter_paths import volume_num_for_chapter
    volume_num = max(1, volume_num_for_chapter(current_chapter))
    status_text = (latest_commit.get("meta") or {}).get("status", "missing")
    result = {
        "chapter": current_chapter,
        "mainline_ready": not snapshot.fallback_sources,
        "fallback_sources": list(snapshot.fallback_sources),
        "latest_commit_status": status_text,
        "primary_write_source": snapshot.primary_write_source,
        "display_text": (
            f"主合同链: 第{current_chapter}章 "
            f"(MASTER_SETTING -> volume_{volume_num:03d} -> chapter_{current_chapter:03d}), "
            f"提交: {status_text}"
        ),
    }
    return result
