#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文档里写的 `webnovel.py <cmd>` 必须是**真实存在**的子命令。

为什么需要：本项目反复栽在「声明了但不存在」上——`write-guard.js` 的白名单
里写着 `projections retry`，而命令从未实现，作者照着敲只会得到
`unrecognized arguments`。文档比代码更容易腐烂：代码改了没人红，文档没人读。

判定方式：从 `webnovel.py` 源码里抽所有 `add_parser("<name>")`，得到权威命令
集合（比解析 `--help` 快且不依赖子进程）；再扫文档里的 `webnovel.py <cmd>`。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]
CLI_SOURCE = SCRIPTS / "data_modules" / "webnovel.py"

#: `webnovel.py <cmd>` —— 只认**同一行**内、小写开头的裸命令名。
#:
#: 两处细节都是实测踩出来的：
#: 1. 用 `[ \t]+` 而不是 `\s+`——`\s` 跨行，会把「`webnovel.py` 行尾 + 下一段
#:    首词」当成命令（误报过 `p` / `from` 这类普通单词）；
#: 2. 末尾负向前瞻排除 `[/\w-]`——否则 `git add webnovel-writer/…/webnovel.py
#:    webnovel-writer/skills/…` 这种**路径列表**里的下一段路径会被当成命令。
#: 同样天然排除 `--project-root`、`<command>`、`[args]` 这些占位。
DOC_COMMAND = re.compile(r"webnovel\.py[ \t]+([a-z][a-z0-9-]*)(?![a-z0-9/_-])")

#: `add_parser("xxx")` / `add_parser('xxx')`
PARSER_CALL = re.compile(r"add_parser\(\s*[\"']([a-zA-Z0-9_-]+)[\"']")

#: 文档里引用的仓内脚本路径，例如 `.opencode/scripts/data_modules/canon_locks.py`
SCRIPT_PATH = re.compile(r"\.opencode/scripts/([A-Za-z0-9_./-]+\.py)")

#: 文档里允许出现的非命令词（紧跟 webnovel.py 但其实是参数或说明）
IGNORED = frozenset({""})


def real_commands() -> set[str]:
    return set(PARSER_CALL.findall(CLI_SOURCE.read_text(encoding="utf-8")))


def documented_commands(path: Path) -> set[str]:
    return set(DOC_COMMAND.findall(path.read_text(encoding="utf-8"))) - IGNORED


def documented_scripts(path: Path) -> set[str]:
    return set(SCRIPT_PATH.findall(path.read_text(encoding="utf-8")))


#: 历史**计划**文档：它们是当时的快照，记录"打算建什么、建在哪"，
#: 之后实现可能合并、改名、甚至放弃。要求它们与当前实现逐字一致，
#: 等于篡改档案——所以两类检查都跳过这个目录。
HISTORICAL_DIRS = ("superpowers", "plans")


def doc_files() -> list[Path]:
    """面向**当前读者**的 markdown：docs/ + README + INSTALL + AGENTS + 发行说明。

    排除：
    - `docs/superpowers/plans/`：历史计划快照（见 `HISTORICAL_DIRS`）；
    - `.tmp/`（草稿）与 `.opencode/references/`（写作素材，里面出现
      `.opencode/scripts/xxx.py` 是巧合而非引用）。
    """
    files = [
        path for path in sorted((REPO_ROOT / "docs").rglob("*.md"))
        if not any(part in HISTORICAL_DIRS for part in path.parts)
    ]
    for name in ("README.md", "INSTALL.md", "AGENTS.md"):
        path = REPO_ROOT / name
        if path.is_file():
            files.append(path)
    files.extend(sorted(REPO_ROOT.glob("RELEASE_NOTES_*.md")))
    return files


class TestDocsCommandsExist:
    def test_cli_source_parses(self):
        """抽取逻辑本身要能拿到足够多的命令，否则这个测试会永远绿。"""
        commands = real_commands()
        for expected in ("chapter-commit", "changes", "projections", "canon"):
            assert expected in commands, f"未从 webnovel.py 抽到 {expected}"

    @pytest.mark.parametrize("path", doc_files(), ids=lambda p: p.name)
    def test_no_phantom_command(self, path: Path):
        known = real_commands()
        phantom = sorted(cmd for cmd in documented_commands(path) if cmd not in known)
        assert not phantom, (
            f"{path.name} 写了不存在的命令：{phantom}\n"
            f"  可用命令（{len(known)} 个）：{', '.join(sorted(known))}"
        )

    @pytest.mark.parametrize("path", doc_files(), ids=lambda p: p.name)
    def test_no_phantom_script_path(self, path: Path):
        """文档里引用的 `.opencode/scripts/xxx.py` 必须真实存在。

        与命令同理：`memory_manager.py` 被改名/合并后，文档里那条路会一直指向
        虚空，读者照着敲只会得到 `No such file or directory`。
        """
        missing = sorted(
            rel for rel in documented_scripts(path)
            if not (SCRIPTS / rel.replace("/", "\\")).is_file()
            and not (SCRIPTS / rel).is_file()
        )
        assert not missing, f"{path.name} 引用了不存在的脚本：{missing}"
