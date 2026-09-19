"""Safe chapter deletion with projection cleanup.

Deletes chapter markdown files, then cleans up:
  - state.json progress.chapter_status
  - memory scratchpad entries

Supports dry-run mode (--dry-run) to preview changes without applying.
"""

import json
import sys
from pathlib import Path

from chapter_paths import extract_chapter_num_from_filename, parse_chapter_range

try:
    from data_modules.ssot_enforcer import publish_event
except ImportError:  # pragma: no cover
    try:
        from ssot_enforcer import publish_event
    except ImportError:
        publish_event = None


def _find_chapter_files(project_root: Path, chapter_nums: list[int]) -> dict[int, Path]:
    text_dir = project_root / "正文"
    if not text_dir.is_dir():
        return {}

    found: dict[int, Path] = {}
    for f in text_dir.rglob("第*章*.md"):
        num = extract_chapter_num_from_filename(f.name)
        if num is not None and num in chapter_nums:
            found[num] = f
    return found


def _clean_state_json(project_root: Path, chapters: list[int], dry_run: bool) -> list[str]:
    state_path = project_root / ".webnovel" / "state.json"

    if dry_run:
        return [f"Would remove chapters {chapters} from state.json progress.chapter_status"]

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["state.json not found or unreadable — skipped"]

    chapter_status = (state.get("progress") or {}).get("chapter_status") or {}
    removed = [str(ch) for ch in chapters if str(ch) in chapter_status]
    for key in removed:
        del chapter_status[key]

    if removed:
        state.setdefault("progress", {})["chapter_status"] = chapter_status
        try:
            from security_utils import atomic_write_json
            atomic_write_json(state_path, state, use_lock=True, backup=True)
        except OSError as e:
            return [f"Failed to write state.json: {e}"]
        return [f"Removed {len(removed)} entries from state.json: {removed}"]
    return ["No matching entries in state.json"]


def _clean_memory(project_root: Path, chapters: list[int], dry_run: bool) -> list[str]:
    scratchpad = project_root / ".webnovel" / "memory_scratchpad.json"

    if dry_run:
        return [f"Would clean memory entries sourced from chapters {chapters}"]

    try:
        data = json.loads(scratchpad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["memory_scratchpad.json not found or unreadable — skipped"]

    if not isinstance(data, list):
        return ["memory_scratchpad.json is not a list — skipped"]

    original = len(data)
    keep = [item for item in data
            if not (isinstance(item, dict)
                    and (item.get("source_chapter") in chapters
                         or item.get("planted_chapter") in chapters))]
    removed = original - len(keep)

    if removed > 0:
        try:
            from security_utils import atomic_write_json
            atomic_write_json(scratchpad, keep, use_lock=True, backup=True)
        except OSError as e:
            return [f"Failed to write memory: {e}"]
        return [f"Removed {removed} memory entries ({original} → {len(keep)})"]
    return ["No matching memory entries"]


def cmd_delete_chapters(args) -> int:
    project_root = Path(args.project_root).expanduser().resolve()
    if not (project_root / ".webnovel" / "state.json").is_file():
        print("ERROR: Not a webnovel project root.", file=sys.stderr)
        return 1

    chapters = parse_chapter_range(args.chapters)
    if not chapters:
        print("ERROR: no chapters specified", file=sys.stderr)
        return 1

    dry_run = args.dry_run
    label = "[DRY RUN] " if dry_run else ""

    found = _find_chapter_files(project_root, chapters)
    missing = [c for c in chapters if c not in found]

    print(f"{label}Chapters to delete: {chapters}")
    print(f"{label}  Found: {list(found.keys())}")
    if missing:
        print(f"{label}  Missing (no file): {missing}")
    if dry_run:
        print()

    for num, path in found.items():
        if dry_run:
            print(f"{label}  Would delete: {path.relative_to(project_root)}")
        else:
            try:
                path.unlink()
                print(f"  Deleted: {path.relative_to(project_root)}")
            except OSError as e:
                print(f"  FAILED: {path.relative_to(project_root)} — {e}")

    if not dry_run:
        print()

    for msg in _clean_state_json(project_root, chapters, dry_run):
        print(f"{label}  {msg}")

    for msg in _clean_memory(project_root, chapters, dry_run):
        print(f"{label}  {msg}")

    if not dry_run and publish_event is not None:
        try:
            publish_event(project_root, "chapter_deleted", {"chapters": list(chapters)})
        except Exception:
            pass

    if dry_run:
        print(f"\nDry run complete. Run without --dry-run to apply.")
    else:
        print(f"\nDeleted {len(found)} chapter(s). index.db cleanup requires manual rebuild: webnovel index process-chapter")
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Safe chapter deletion with projection cleanup")
    ap.add_argument("chapters", help="Chapter range, e.g. '5-12' or '5,7,9-12'")
    ap.add_argument("--project-root", required=True, help="Book project root")
    ap.add_argument("--dry-run", action="store_true", help="Preview without deleting")
    args = ap.parse_args()
    raise SystemExit(cmd_delete_chapters(args))
