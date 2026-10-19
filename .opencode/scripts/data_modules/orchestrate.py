"""Batch orchestration: preflight → review → commit → index for chapter ranges.

Modes:
  write    Sequential chapter pipeline (review → commit → index)
  heal     Fix bad chapters (re-review → re-commit → rebuild index)
  nightly  Health check only (preflight + status scan)
"""

import subprocess
import sys
from pathlib import Path

from chapter_paths import parse_chapter_range

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
_MODES_REVIEW_COMMIT = {"write", "heal"}
_MODES_INDEX = {"write", "heal", "nightly"}


def _run(argv: list[str], timeout: int = 300) -> tuple[int, str]:
    entry = _SCRIPTS_DIR / "webnovel.py"
    cmd = [sys.executable, "-X", "utf8", str(entry)] + argv
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return 1, f"Timeout after {timeout}s: {' '.join(argv)}"
    return proc.returncode, proc.stdout + "\n" + proc.stderr


def cmd_orchestrate(args) -> int:
    project_root = args.project_root
    if not project_root:
        print("ERROR: --project-root is required for orchestrate", file=sys.stderr)
        return 1

    root_flag = ["--project-root", project_root]
    try:
        chapters = parse_chapter_range(args.chapters)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    mode = args.mode

    if not chapters:
        print("ERROR: no chapters to process", file=sys.stderr)
        return 1

    print(f"Orchestrate: {mode} mode, chapters={chapters}")

    rc, out = _run(root_flag + ["preflight"])
    if rc != 0:
        print("Preflight failed. Fix issues before running orchestrate.")
        print(out[-2000:])
        return rc

    failures = []
    for ch in chapters:
        chapter_flag = ["--chapter", str(ch)]

        if mode in _MODES_REVIEW_COMMIT:
            rc, _ = _run(root_flag + ["review-pipeline"] + chapter_flag)
            if rc != 0:
                failures.append((ch, "review"))
                continue

            rc, _ = _run(root_flag + ["chapter-commit"] + chapter_flag)
            if rc != 0:
                failures.append((ch, "commit"))
                continue

        if mode in _MODES_INDEX:
            rc, _ = _run(root_flag + ["index", "process-chapter"] + chapter_flag, timeout=120)
            if rc != 0:
                failures.append((ch, "index"))
                continue

        print(f"  Chapter {ch}: OK")

    if failures:
        print(f"\nFailures ({len(failures)}/{len(chapters)}):")
        for ch, step in failures:
            print(f"  Chapter {ch}: {step} failed")
        return 1

    print(f"\nAll {len(chapters)} chapters completed successfully.")
    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Batch chapter orchestration")
    ap.add_argument("mode", choices=["write", "heal", "nightly"],
                    help="write=full pipeline, heal=re-review+recommit, nightly=health check")
    ap.add_argument("chapters", help="Chapter range, e.g. '5-12' or '5,7,9-12'")
    ap.add_argument("--project-root", required=True, help="Book project root")
    args = ap.parse_args()
    raise SystemExit(cmd_orchestrate(args))
