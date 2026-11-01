#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""语义化版本推算：commit 列表 + 当前 tag → 下一个 tag。

## 为什么单独成模块

此前 `CLAUDE.md` 的「版本影响」表描述的是**约定**，而 `manifest.yml` 里
没有任何 semver 运算、也不打 `git tag`——`feat!:` 会发成
`v2.9.2-<时间戳>`、`docs:` 反而也产生新版本字符串。补齐这件事时，逻辑
不能埋在 YAML 的 bash 里：那里无法单测，写错只有在推送之后才知道。

故把「哪些 commit 该 bump 到什么」抽成本模块的纯函数，workflow 只做
「取 commit → 调它 → 打 tag」。单测在 `tests/test_semver_bump.py`。

## 规则（与 CLAUDE.md 的表一致）

| 条件 | 结果 |
|---|---|
| 任一 commit 正文含 `BREAKING CHANGE:`，或标题是 `type!:` / `type(scope)!:` | **MAJOR** |
| 任一标题是 `feat:` / `feat(scope):` | **MINOR** |
| 任一标题是 `fix:` / `refactor:` / `perf:`（可带 scope） | **PATCH** |
| 只有 `docs:` `ci:` `chore:` `test:` `simplify:` `style:` `build:` 等 | **不 bump（返回 None）** |
| 空列表 | 不 bump |

取所有命中里**最高**的一档。

## 忽略的 commit

- 含 `[skip ci]`：CI 自己产出的 manifest / release 提交，不是人为变更
- 含 `[no release]`：显式要求本次不发版（逃生口）
- 以 `Merge ` 开头：合并提交不携带 type 语义

## 命令行

    python semver_bump.py --current v3.0.0 --commits-file commits.txt
    git log --format=%B "$BASE..HEAD" | python semver_bump.py --current "$BASE" -
    python semver_bump.py --current v3.0.0 --commits-file commits.txt --dry-run

stdout 只输出结果（`v3.1.0` 或空行），便于 `$(...)` 直接取值；
诊断信息一律走 stderr。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

#: 语义化版本各部分的上限保护（防止 tag 数字畸形导致进位爆炸）
MAX_PART = 10**6

_BUMP_MAJOR, _BUMP_MINOR, _BUMP_PATCH, _BUMP_NONE = 3, 2, 1, 0

#: `type:` / `type(scope):` 的通用前缀解析
_SUBJECT_RE = re.compile(r"^(?P<type>[a-zA-Z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:")

_IGNORED_TYPES = frozenset({
    "docs", "ci", "chore", "test", "simplify", "style", "build", "revert",
})

_PATCH_TYPES = frozenset({"fix", "refactor", "perf"})

_IGNORE_MARKERS = ("[skip ci]", "[no release]")


def parse_tag(tag: str) -> Tuple[int, int, int]:
    """把 `v3.0.0` / `3.0.0` / `v3.0.0-20260101.0101` 解析成 (3, 0, 0)。

    非 semver（如 `nightly`、空串）一律回落 (0, 0, 0)——首次发版时
    `feat:` 会得到 v0.1.0，符合「还没有版本线」的直觉。
    """
    text = str(tag or "").strip()
    if text.startswith(("v", "V")):
        text = text[1:]
    # 去掉 prerelease / build 元数据
    text = text.split("-", 1)[0].split("+", 1)[0]
    parts = text.split(".")
    numbers: List[int] = []
    for raw in parts[:3]:
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            return (0, 0, 0)
        if value < 0 or value > MAX_PART:
            return (0, 0, 0)
        numbers.append(value)
    while len(numbers) < 3:
        numbers.append(0)
    major, minor, patch = numbers
    return (major, minor, patch)


def format_tag(version: Tuple[int, int, int]) -> str:
    return "v{}.{}.{}".format(*version)


def _should_ignore(message: str) -> bool:
    lowered = message.lower()
    if any(marker in lowered for marker in _IGNORE_MARKERS):
        return True
    first_line = message.strip().splitlines()[0] if message.strip() else ""
    return first_line.startswith("Merge ")


def classify(message: str) -> int:
    """单条 commit 的 bump 等级（_BUMP_*）。"""
    if not message or not message.strip():
        return _BUMP_NONE
    if _should_ignore(message):
        return _BUMP_NONE

    lines = message.strip().splitlines()
    subject = lines[0]
    body = "\n".join(lines[1:])

    # 正文里的 BREAKING CHANGE 优先于标题的 type
    if "BREAKING CHANGE:" in body or "BREAKING-CHANGE:" in body:
        return _BUMP_MAJOR

    match = _SUBJECT_RE.match(subject)
    if not match:
        return _BUMP_NONE

    commit_type = match.group("type").lower()
    if match.group("bang"):
        return _BUMP_MAJOR
    if commit_type == "feat":
        return _BUMP_MINOR
    if commit_type in _PATCH_TYPES:
        return _BUMP_PATCH
    if commit_type in _IGNORED_TYPES:
        return _BUMP_NONE
    # 未知 type：保守地当作不 bump（不猜）
    return _BUMP_NONE


def highest_bump(messages: Iterable[str]) -> int:
    level = _BUMP_NONE
    for message in messages:
        level = max(level, classify(message))
    return level


def next_version(
    commit_messages: Sequence[str],
    current_tag: str,
) -> Optional[str]:
    """推算下一个 tag。无有效变更时返回 None（表示「不发版」）。"""
    level = highest_bump(commit_messages)
    if level == _BUMP_NONE:
        return None

    major, minor, patch = parse_tag(current_tag)
    if level == _BUMP_MAJOR:
        return format_tag((major + 1, 0, 0))
    if level == _BUMP_MINOR:
        return format_tag((major, minor + 1, 0))
    return format_tag((major, minor, patch + 1))


def split_commits(text: str) -> List[str]:
    """把 `git log --format=%B` 的输出切成一条条完整 commit 消息。

    用一行 `\\x00` 分隔更稳，但 `--format=%B` 之间是空行，正文本身也可能
    含空行——故此处按「连续 2 个及以上换行 + 下一段以常规 type 开头」拆不可靠。
    实际约定：workflow 侧用 `--format=%B%x00` 传 NUL 分隔，本函数优先按 NUL 拆；
    没有 NUL 时退化为「按空行分段并把非 type 开头的段并入上一段」。
    """
    if "\x00" in text:
        return [chunk.strip() for chunk in text.split("\x00") if chunk.strip()]

    blocks: List[str] = []
    current: List[str] = []
    for line in text.splitlines():
        if not line.strip() and current:
            current.append("")
            continue
        if _SUBJECT_RE.match(line) or line.startswith("Merge "):
            if current:
                blocks.append("\n".join(current).strip())
            current = [line]
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [b for b in blocks if b]


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="按 Conventional Commits 推算下一个语义化版本 tag")
    parser.add_argument("--current", default="", help="当前最新的 git tag，如 v3.0.0")
    parser.add_argument(
        "--commits-file", default="-",
        help="commit 消息文件（NUL 分隔或空行分隔）；`-` 表示 stdin")
    parser.add_argument(
        "--dry-run", action="store_true", help="只打印推算过程，不改变输出格式")
    args = parser.parse_args(argv)

    if args.commits_file == "-":
        text = sys.stdin.read()
    else:
        text = Path(args.commits_file).read_text(encoding="utf-8")

    messages = split_commits(text)
    level = highest_bump(messages)
    result = next_version(messages, args.current)

    if args.dry_run:
        names = {_BUMP_MAJOR: "MAJOR", _BUMP_MINOR: "MINOR",
                 _BUMP_PATCH: "PATCH", _BUMP_NONE: "NONE"}
        print(f"commit 数: {len(messages)}", file=sys.stderr)
        print(f"最高等级: {names[level]}", file=sys.stderr)
        print(f"当前 tag: {args.current or '(无)'} → 下一个: {result or '(不发版)'}",
              file=sys.stderr)

    # stdout 只留结果，便于 `$(...)` 取值
    print(result or "")
    return 0


def _ensure_scripts_path() -> None:
    """把自己所在的 `scripts/` 加进 sys.path。

    直接以 `python .opencode/scripts/data_modules/semver_bump.py` 执行时，
    `sys.path[0]` 是 `data_modules/`，`runtime_compat`（在 `scripts/`）导不到。
    **CI 就是这种调用方式**：semver.yml 里没有设 PYTHONPATH，首次运行因此
    10 秒就挂在 `ModuleNotFoundError: No module named 'runtime_compat'`。
    本地之所以没发现，是因为本地验证时我手加了 `PYTHONPATH=.opencode/scripts`
    ——「本地跑得通」与「CI 跑得通」的差异又坑了一次。

    与 `review_pipeline.py` 的 `_ensure_scripts_path()` 同一做法。
    """
    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))


if __name__ == "__main__":
    _ensure_scripts_path()
    from runtime_compat import enable_windows_utf8_stdio

    enable_windows_utf8_stdio()
    raise SystemExit(main())
