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


def _latest_committed(chapter_status: dict) -> int:
    """剩余章节中最大的已提交章号；没有则返回 0。

    chapter_status 的值有带前缀（chapter_committed）与不带（committed）两种
    历史形状，这里都认。
    """
    nums = []
    for key, value in (chapter_status or {}).items():
        if str(value).replace("chapter_", "") == "committed":
            try:
                nums.append(int(key))
            except (TypeError, ValueError):
                continue
    return max(nums) if nums else 0


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
        progress = state.setdefault("progress", {})
        progress["chapter_status"] = chapter_status
        # 删掉的若是当前章，current_chapter 会继续指向一个已不存在的章节
        # （状态面板会显示「第 N 章」而正文里没有）。重算为剩余已提交章的最大值。
        progress["current_chapter"] = _latest_committed(chapter_status)
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

    # 破坏性操作：参数错了要在动手之前说清楚，不能把 Python traceback
    # 甩给用户——那样他既不知道错在哪，也不知道有没有已经删掉什么。
    try:
        chapters = parse_chapter_range(args.chapters)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
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

    if dry_run:
        for num, path in found.items():
            print(f"{label}  Would delete: {path.relative_to(project_root)}")
        # dry-run 也要展示 state/memory 会被怎么改
        for msg in _clean_state_json(project_root, chapters, True):
            print(f"{label}  {msg}")
        for msg in _clean_memory(project_root, chapters, True):
            print(f"{label}  {msg}")
        print("\nDry run complete. Run without --dry-run to apply.")
        return 0

    # ── 顺序说明（别改回去）──────────────────────────────────────
    # 事件必须**先**写成功，state.json 才能改。
    #
    # 旧顺序是「改 state → 发事件（失败就 pass 吞掉）」。事件写失败时
    # state.json 已经删掉了章节，而事件日志里没有 chapter_deleted——
    # 下一次 `ssot rebuild` 按事件重放，会把已删除的章节**复活**。
    # 更糟的是那个 `except: pass`：命令照样打印 "Deleted N chapter(s)"
    # 并返回 0，调用它的 skill 以为删干净了。
    #
    # 事件日志是 SSOT 的事实来源，所以让它先落盘：失败就中止，state 不动，
    # 章节文件也不删（宁可不删，也不要删成两套说法不一致的状态）。
    if publish_event is not None:
        try:
            publish_event(project_root, "chapter_deleted", {"chapters": list(chapters)})
        except Exception as exc:
            print(
                f"ERROR: 无法写入 chapter_deleted 事件（{exc}）。\n"
                f"       已中止，未删除任何章节、未修改 state.json。\n"
                f"       原因多为 .story-system/events 不可写或事件锁超时。",
                file=sys.stderr,
            )
            return 1

    for num, path in found.items():
        try:
            path.unlink()
            print(f"  Deleted: {path.relative_to(project_root)}")
        except OSError as e:
            print(f"  FAILED: {path.relative_to(project_root)} — {e}")

    print()

    for msg in _clean_state_json(project_root, chapters, False):
        print(f"  {msg}")

    for msg in _clean_memory(project_root, chapters, False):
        print(f"  {msg}")

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
