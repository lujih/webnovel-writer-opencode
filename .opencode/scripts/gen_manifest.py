#!/usr/bin/env python3
"""
Generate manifest.json for the webnovel-writer .opencode/ directory.
Lists every file with SHA256 hash and size. Used for incremental updates.

Usage:
  python .opencode/scripts/gen_manifest.py [--version v1.2.0] > manifest.json
  python .opencode/scripts/gen_manifest.py --verify

设计要点（三条都是踩过的坑，别改回去）：

1. **哈希前把 CRLF 归一化为 LF**。此前直接哈希工作树原始字节，于是同一个
   commit 在 Windows（CRLF 检出）和 Linux CI（LF 检出）上算出不同哈希——
   实测 485 个条目里 428 个"不匹配"纯粹是行尾差异，让这份清单彻底失去意义。
   git blob 本身就是 LF，按 LF 归一化后两种检出得到同一串哈希。

2. **文件集合以 git 索引为准**（仓库内时）。此前用 os.walk 遍历工作树，
   于是本地任何未跟踪产物都会混进清单：`.opencode/scripts/.coverage`（二进制）、
   `.opencode/.webnovel-current-project`（开发机绝对路径）、以及一堆
   与本项目无关的本地 skill。非 git 目录（如测试用的临时假仓库）回退到遍历。

3. **`--verify` 让漂移可执行地暴露**。没有它，谁也不会去手 diff 500 多行哈希，
   于是清单可以静默陈旧好几个月（实测 CI 自 2026-10-09 起就没再刷新过）。
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

_SKIP_DIRS = frozenset({'__pycache__', 'node_modules', '.git'})
_SKIP_EXTS = ('.pyc', '.pyo')


def hash_file_normalized(path: Path) -> str:
    """Compute SHA256 of the file's content with CRLF normalized to LF.

    Normalising before hashing is what makes this manifest checkout-independent:
    git stores LF blobs, but a Windows worktree has CRLF, so hashing raw bytes
    produced 428 false mismatches in a 485-entry manifest (measured 2026-10-18).
    """
    data = path.read_bytes()
    if b'\r\n' in data:
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def normalized_size(path: Path) -> int:
    """Size in bytes of the LF-normalized content (matches the hashed bytes)."""
    data = path.read_bytes()
    if b'\r\n' in data:
        data = data.replace(b'\r\n', b'\n')
    return len(data)


def git_tracked_files(opencode_dir: Path) -> list:
    """Return tracked paths under opencode_dir, or [] when not in a git repo.

    The git index is the authoritative answer to "what ships"; walking the
    worktree picks up local scratch files (`.coverage`, editor pointers,
    unrelated skill packs) that have no business being in a release manifest.
    """
    try:
        r = subprocess.run(
            ['git', '-C', str(opencode_dir), 'rev-parse', '--is-inside-work-tree'],
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0 or r.stdout.strip() != 'true':
        return []

    try:
        r = subprocess.run(
            ['git', '-C', str(opencode_dir), 'ls-files', '-z', '--', '.'],
            capture_output=True, text=True, timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []

    out = []
    for rel in r.stdout.split('\0'):
        if not rel:
            continue
        full = opencode_dir / rel
        if full.is_file():          # 索引里有、工作树里被删的条目不进清单
            out.append(full)
    return sorted(out)


def walk_files(opencode_dir: Path) -> list:
    """Filesystem fallback for non-git directories (fake repos in tests)."""
    out = []
    for root, dirs, filenames in os.walk(opencode_dir):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(_SKIP_EXTS):
                continue
            out.append(Path(root) / fn)
    return sorted(out)


def iter_manifest_files(opencode_dir: Path) -> list:
    files = git_tracked_files(opencode_dir)
    if files:
        return files
    return walk_files(opencode_dir)


def build_manifest(opencode_dir: Path, version: str,
                   tag: str = "", updated: str = "",
                   changelog: list = None) -> dict:
    """Build manifest dict for all files under opencode_dir."""
    files = {}
    for full in iter_manifest_files(opencode_dir):
        rel = full.relative_to(opencode_dir.parent).as_posix()
        files[rel] = {
            "sha256": hash_file_normalized(full),
            "size": normalized_size(full),
        }

    return {
        "version": version,
        "tag": tag,
        "updated": updated,
        "changelog": changelog or [],
        "files": files,
    }


def verify(opencode_dir: Path, manifest_path: Path) -> int:
    """Compare the on-disk manifest against the tree. 0 = consistent."""
    if not manifest_path.is_file():
        print(f"错误：找不到 {manifest_path}", file=sys.stderr)
        return 1

    recorded = json.loads(manifest_path.read_text(encoding='utf-8')).get('files') or {}
    actual = build_manifest(opencode_dir, version='')['files']

    missing = sorted(set(recorded) - set(actual))
    extra = sorted(set(actual) - set(recorded))
    changed = sorted(k for k in set(actual) & set(recorded)
                     if actual[k]['sha256'] != recorded[k]['sha256'])

    if not (missing or extra or changed):
        print(f"✅ manifest 与工作树一致（{len(actual)} 个文件）")
        return 0

    print(f"❌ manifest 已陈旧（记录 {len(recorded)} 个，实际 {len(actual)} 个）",
          file=sys.stderr)
    if changed:
        print(f"  内容变了 {len(changed)} 个：", file=sys.stderr)
        for k in changed[:20]:
            print(f"    ~ {k}", file=sys.stderr)
        if len(changed) > 20:
            print(f"    … 另有 {len(changed) - 20} 个", file=sys.stderr)
    if missing:
        print(f"  清单里有但磁盘上没有 {len(missing)} 个：", file=sys.stderr)
        for k in missing[:20]:
            print(f"    - {k}", file=sys.stderr)
    if extra:
        print(f"  磁盘上有但清单里没有 {len(extra)} 个：", file=sys.stderr)
        for k in extra[:20]:
            print(f"    + {k}", file=sys.stderr)
    print("\n重新生成：python .opencode/scripts/gen_manifest.py "
          "--version <当前版本> > manifest.json", file=sys.stderr)
    return 1


def main():
    parser = argparse.ArgumentParser(description="Generate manifest.json for webnovel-writer")
    parser.add_argument("--version", default="0.0.0")
    parser.add_argument("--tag", default="")
    parser.add_argument("--updated", default="")
    parser.add_argument("--changelog-file", default="",
                        help="Path to JSON file with changelog array")
    parser.add_argument("--opencode-dir", default=".opencode")
    parser.add_argument("--verify", action="store_true",
                        help="只校验现有 manifest 是否与工作树一致，不生成")
    parser.add_argument("--manifest", default="manifest.json",
                        help="--verify 要比对的清单路径（默认仓库根 manifest.json）")
    args = parser.parse_args()

    opencode_dir = Path(args.opencode_dir).resolve()
    if not opencode_dir.is_dir():
        print(f"Error: {opencode_dir} not found", file=sys.stderr)
        sys.exit(1)

    if args.verify:
        sys.exit(verify(opencode_dir, Path(args.manifest)))

    changelog = []
    if args.changelog_file and Path(args.changelog_file).is_file():
        changelog = json.loads(Path(args.changelog_file).read_text(encoding="utf-8"))

    manifest = build_manifest(opencode_dir, args.version,
                              tag=args.tag, updated=args.updated,
                              changelog=changelog)
    # ensure_ascii=True avoids encoding issues with non-UTF-8 filesystem paths
    # (e.g., GBK-encoded Chinese paths on Windows that break Linux CI runners)
    sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
