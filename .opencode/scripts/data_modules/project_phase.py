#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    from chapter_outline_loader import volume_num_for_chapter_from_state
    from chapter_paths import find_chapter_file, volume_num_for_chapter
except ImportError:  # pragma: no cover
    from scripts.chapter_outline_loader import volume_num_for_chapter_from_state
    from scripts.chapter_paths import find_chapter_file, volume_num_for_chapter

from .projection_log import latest_projection_run, projection_status_from_run


PHASE_NO_PROJECT = "no_project"
PHASE_UNKNOWN = "unknown"
PHASE_INIT_SCAFFOLDED = "init_scaffolded"
PHASE_INIT_READY = "init_ready"
PHASE_PLAN_IN_PROGRESS = "plan_in_progress"
PHASE_CHAPTER_CONTRACT_READY = "chapter_contract_ready"
PHASE_DRAFT_IN_PROGRESS = "draft_in_progress"
PHASE_READY_TO_COMMIT = "ready_to_commit"
PHASE_CHAPTER_COMMITTED = "chapter_committed"
PHASE_PROJECTION_FAILED = "projection_failed"

PHASES = (
    PHASE_NO_PROJECT,
    PHASE_UNKNOWN,
    PHASE_INIT_SCAFFOLDED,
    PHASE_INIT_READY,
    PHASE_PLAN_IN_PROGRESS,
    PHASE_CHAPTER_CONTRACT_READY,
    PHASE_DRAFT_IN_PROGRESS,
    PHASE_READY_TO_COMMIT,
    PHASE_CHAPTER_COMMITTED,
    PHASE_PROJECTION_FAILED,
)

INIT_REQUIRED_DIRS = (
    ".webnovel",
    ".webnovel/backups",
    ".webnovel/archive",
    ".webnovel/summaries",
    "设定集",
    "大纲",
    "正文",
    "审查报告",
)

INIT_REQUIRED_FILES = (
    ".webnovel/state.json",
    "设定集/世界观.md",
    "设定集/力量体系.md",
    "设定集/主角卡.md",
    "设定集/反派设计.md",
    "大纲/总纲.md",
    ".env.example",
)

COMMIT_ARTIFACT_FILES = (
    ".webnovel/tmp/review_results.json",
    ".webnovel/tmp/fulfillment_result.json",
    ".webnovel/tmp/disambiguation_result.json",
    ".webnovel/tmp/extraction_result.json",
)

_CHAPTER_FILE_RE = re.compile(r"chapter_(\d{3,4})")


@dataclass(frozen=True)
class ChapterCommitInfo:
    chapter: int
    status: str
    path: str
    projection_status: dict[str, str] = field(default_factory=dict)
    projection_source: str = "commit"

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter": self.chapter,
            "status": self.status,
            "path": self.path,
            "projection_status": dict(self.projection_status),
            "projection_source": self.projection_source,
        }


@dataclass(frozen=True)
class ProjectPhaseSnapshot:
    project_root: str
    phase: str
    target_chapter: int
    latest_accepted_chapter: int
    latest_commit: ChapterCommitInfo | None = None
    state_current_chapter: int = 0
    missing_init_files: tuple[str, ...] = ()
    missing_init_dirs: tuple[str, ...] = ()
    missing_contract_files: tuple[str, ...] = ()
    missing_commit_artifacts: tuple[str, ...] = ()
    draft_file: str = ""
    blocking: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "phase": self.phase,
            "target_chapter": self.target_chapter,
            "latest_accepted_chapter": self.latest_accepted_chapter,
            "latest_commit": self.latest_commit.to_dict() if self.latest_commit else None,
            "state_current_chapter": self.state_current_chapter,
            "missing_init_files": list(self.missing_init_files),
            "missing_init_dirs": list(self.missing_init_dirs),
            "missing_contract_files": list(self.missing_contract_files),
            "missing_commit_artifacts": list(self.missing_commit_artifacts),
            "draft_file": self.draft_file,
            "blocking": list(self.blocking),
            "warnings": list(self.warnings),
        }


def _read_json_object(path: Path) -> tuple[dict[str, Any], str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}, "missing"
    except json.JSONDecodeError as exc:
        return {}, f"invalid_json:{exc}"
    except OSError as exc:
        return {}, f"read_error:{exc}"
    if not isinstance(payload, dict):
        return {}, "not_object"
    return payload, ""


def _chapter_from_path(path: Path) -> int:
    match = _CHAPTER_FILE_RE.search(path.name)
    if not match:
        return 0
    try:
        return int(match.group(1))
    except ValueError:
        return 0


def _state_current_chapter(project_root: Path) -> tuple[int, str]:
    state_path = project_root / ".webnovel" / "state.json"
    state, error = _read_json_object(state_path)
    if error:
        return 0, error
    progress = state.get("progress") if isinstance(state, dict) else {}
    if not isinstance(progress, dict):
        return 0, ""
    try:
        return max(0, int(progress.get("current_chapter") or 0)), ""
    except (TypeError, ValueError):
        return 0, ""


def _scan_commits(project_root: Path) -> list[ChapterCommitInfo]:
    commits_dir = project_root / ".story-system" / "commits"
    if not commits_dir.is_dir():
        return []

    commits: list[ChapterCommitInfo] = []
    for path in sorted(commits_dir.glob("chapter_*.commit.json")):
        chapter = _chapter_from_path(path)
        if chapter <= 0:
            continue
        payload, error = _read_json_object(path)
        meta = payload.get("meta") if isinstance(payload, dict) else {}
        if error:
            status = "invalid"
        elif isinstance(meta, dict):
            status = str(meta.get("status") or "missing").strip() or "missing"
        else:
            status = "missing"
        raw_projection = payload.get("projection_status") if isinstance(payload, dict) else {}
        projection_status = {
            str(key): str(value)
            for key, value in (raw_projection or {}).items()
            if isinstance(raw_projection, dict)
        }
        projection_source = "commit"
        try:
            latest_run = latest_projection_run(project_root, chapter=chapter)
            logged_projection_status = projection_status_from_run(latest_run)
        except Exception:
            logged_projection_status = {}
        if logged_projection_status:
            projection_status = logged_projection_status
            projection_source = "projection_log"
        commits.append(
            ChapterCommitInfo(
                chapter=chapter,
                status=status,
                path=str(path),
                projection_status=projection_status,
                projection_source=projection_source,
            )
        )
    return commits


def _latest_story_system_chapter(project_root: Path) -> int:
    story_root = project_root / ".story-system"
    if not story_root.is_dir():
        return 0
    chapters: list[int] = []
    for pattern in (
        "chapters/chapter_*.json",
        "reviews/chapter_*.review.json",
        "commits/chapter_*.commit.json",
    ):
        chapters.extend(_chapter_from_path(path) for path in story_root.glob(pattern))
    return max(chapters or [0])


def _latest_draft_chapter(project_root: Path) -> int:
    chapters_dir = project_root / "正文"
    if not chapters_dir.is_dir():
        return 0
    chapters: list[int] = []
    for path in chapters_dir.rglob("第*章*.md"):
        match = re.search(r"第0*(\d+)章", path.name)
        if not match:
            continue
        try:
            chapters.append(int(match.group(1)))
        except ValueError:
            continue
    return max(chapters or [0])


def _target_chapter(
    project_root: Path,
    chapter: int | None,
    *,
    latest_accepted_chapter: int,
) -> int:
    if chapter is not None:
        try:
            return max(0, int(chapter))
        except (TypeError, ValueError):
            return 0
    latest_runtime = max(
        _latest_story_system_chapter(project_root),
        _latest_draft_chapter(project_root),
    )
    if latest_runtime > latest_accepted_chapter:
        return latest_runtime
    return latest_accepted_chapter + 1 if latest_accepted_chapter >= 0 else 0


def _volume_num(project_root: Path, chapter: int) -> int:
    if chapter <= 0:
        return 1
    try:
        return volume_num_for_chapter_from_state(project_root, chapter) or volume_num_for_chapter(chapter)
    except Exception:
        return volume_num_for_chapter(chapter)


def contract_files_for_chapter(project_root: Path, chapter: int) -> dict[str, Path]:
    volume = _volume_num(project_root, chapter)
    story_root = project_root / ".story-system"
    return {
        "master": story_root / "MASTER_SETTING.json",
        "volume": story_root / "volumes" / f"volume_{volume:03d}.json",
        "chapter": story_root / "chapters" / f"chapter_{chapter:03d}.json",
        "review": story_root / "reviews" / f"chapter_{chapter:03d}.review.json",
    }


def missing_contract_files(project_root: Path, chapter: int) -> tuple[str, ...]:
    if chapter <= 0:
        return tuple(str(path.relative_to(project_root)) for path in contract_files_for_chapter(project_root, 1).values())
    missing: list[str] = []
    for path in contract_files_for_chapter(project_root, chapter).values():
        if not path.is_file():
            missing.append(str(path.relative_to(project_root)))
    return tuple(missing)


def missing_commit_artifacts(project_root: Path) -> tuple[str, ...]:
    missing: list[str] = []
    for rel in COMMIT_ARTIFACT_FILES:
        if not (project_root / rel).is_file():
            missing.append(rel)
    return tuple(missing)


def missing_init_dirs(project_root: Path) -> tuple[str, ...]:
    return tuple(rel for rel in INIT_REQUIRED_DIRS if not (project_root / rel).is_dir())


def missing_init_files(project_root: Path) -> tuple[str, ...]:
    return tuple(rel for rel in INIT_REQUIRED_FILES if not (project_root / rel).is_file())


def has_projection_blocker(commit: ChapterCommitInfo | None) -> bool:
    if commit is None:
        return False
    return any(
        str(value).startswith("failed:") or str(value) == "pending"
        for value in commit.projection_status.values()
    )


def resolve_project_phase(project_root: str | Path | None, chapter: int | None = None) -> ProjectPhaseSnapshot:
    if project_root is None:
        return ProjectPhaseSnapshot(
            project_root="",
            phase=PHASE_NO_PROJECT,
            target_chapter=0,
            latest_accepted_chapter=0,
            blocking=("project_root_missing",),
        )

    root = Path(project_root)
    state_path = root / ".webnovel" / "state.json"
    if not state_path.is_file():
        return ProjectPhaseSnapshot(
            project_root=str(root),
            phase=PHASE_NO_PROJECT,
            target_chapter=0,
            latest_accepted_chapter=0,
            blocking=("missing .webnovel/state.json",),
        )

    state_chapter, state_error = _state_current_chapter(root)
    commits = _scan_commits(root)
    latest_commit = max(commits, key=lambda item: item.chapter) if commits else None
    accepted = [item.chapter for item in commits if item.status == "accepted"]
    latest_accepted = max(accepted or [0])
    target = _target_chapter(root, chapter, latest_accepted_chapter=latest_accepted)

    init_dirs_missing = missing_init_dirs(root)
    init_files_missing = missing_init_files(root)
    contract_missing = missing_contract_files(root, target)
    artifacts_missing = missing_commit_artifacts(root)
    draft_path = find_chapter_file(root, target) if target > 0 else None
    draft_file = str(draft_path) if draft_path else ""

    warnings: list[str] = []
    blocking: list[str] = []
    if state_error:
        blocking.append(f"state_json_{state_error}")
    if state_chapter > latest_accepted:
        warnings.append("state_projection_ahead_of_latest_accepted_commit")

    if has_projection_blocker(latest_commit):
        phase = PHASE_PROJECTION_FAILED
        latest_statuses = [str(value) for value in (latest_commit.projection_status or {}).values()]
        blocking.append(
            "latest_commit_projection_incomplete"
            if any(value == "pending" for value in latest_statuses)
            else "latest_commit_projection_failed"
        )
    elif init_dirs_missing or init_files_missing:
        phase = PHASE_INIT_SCAFFOLDED
        blocking.extend([f"missing_init_dir:{rel}" for rel in init_dirs_missing])
        blocking.extend([f"missing_init_file:{rel}" for rel in init_files_missing])
    elif latest_commit and latest_commit.chapter >= target and latest_commit.status in {"accepted", "rejected"}:
        phase = PHASE_CHAPTER_COMMITTED
    elif draft_file and not artifacts_missing:
        phase = PHASE_READY_TO_COMMIT
    elif draft_file:
        phase = PHASE_DRAFT_IN_PROGRESS
    elif not contract_missing:
        phase = PHASE_CHAPTER_CONTRACT_READY
    elif (root / ".story-system" / "MASTER_SETTING.json").is_file() or any((root / "大纲").glob("第*卷*大纲.md")):
        phase = PHASE_PLAN_IN_PROGRESS
    else:
        phase = PHASE_INIT_READY

    return ProjectPhaseSnapshot(
        project_root=str(root),
        phase=phase,
        target_chapter=target,
        latest_accepted_chapter=latest_accepted,
        latest_commit=latest_commit,
        state_current_chapter=state_chapter,
        missing_init_files=init_files_missing,
        missing_init_dirs=init_dirs_missing,
        missing_contract_files=contract_missing,
        missing_commit_artifacts=artifacts_missing,
        draft_file=draft_file,
        blocking=tuple(blocking),
        warnings=tuple(warnings),
    )
