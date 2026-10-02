"""把 CLAUDE.md 镜像为 AGENTS.md（OpenCode V2 项目说明通道）。

为什么需要
----------
OpenCode 2.0 **只发现 `AGENTS.md`，不再回退 `CLAUDE.md`**
（https://opencode.ai/v2/docs/migrate-v1 ，"Instruction files"）。没有
AGENTS.md，整套项目说明在 V2 下对模型完全不可见——16 个 skill 与 6 个
agent 都失去它们赖以工作的硬规则（SSOT 写入纪律、审查 fail-closed 等）。

为什么内容逐字节一致，而不是拆成两份
--------------------------------------
有些宿主（如 DeepSeek Harness）的 `instructionFileCandidates` 同时包含
AGENTS.md 与 CLAUDE.md，并在两者内容相同时**去重、优先 AGENTS.md**。
保持一致就不会双重注入；拆开写则两边的规则会各自漂移，且模型会收到
互相矛盾的两套说明。本仓库只面向 OpenCode，CLAUDE.md 是人工维护的正本，
AGENTS.md 是派生产物。

本命令是幂等的单向生成器：`CLAUDE.md` 为正本，`AGENTS.md` 为镜像。
`--check` 只校验不写盘，供 CI 门禁使用。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
CLAUDE_MD = REPO_ROOT / "CLAUDE.md"
AGENTS_MD = REPO_ROOT / "AGENTS.md"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="把 CLAUDE.md 镜像为 AGENTS.md（OpenCode V2 项目说明入口）"
    )
    ap.add_argument("--check", action="store_true", help="只校验不写盘（幂等性检查）")
    args = ap.parse_args()

    if not CLAUDE_MD.is_file():
        print(f"❌ 缺少正本 {CLAUDE_MD}", file=sys.stderr)
        return 1

    wanted = CLAUDE_MD.read_bytes()
    if args.check:
        if not AGENTS_MD.is_file() or AGENTS_MD.read_bytes() != wanted:
            print(f"stale: {AGENTS_MD.relative_to(REPO_ROOT)}")
            print("❌ AGENTS.md 与 CLAUDE.md 不一致——运行 "
                  "`python .opencode/scripts/webnovel.py sync-agents-md`")
            return 1
        print("✅ AGENTS.md 与 CLAUDE.md 一致")
        return 0

    if AGENTS_MD.is_file() and AGENTS_MD.read_bytes() == wanted:
        print("✅ AGENTS.md 已与 CLAUDE.md 一致")
        return 0

    AGENTS_MD.write_bytes(wanted)
    print(f"✅ 已生成 {AGENTS_MD.relative_to(REPO_ROOT)}"
          f"（{len(wanted)} 字节，镜像自 CLAUDE.md）")
    return 0


if __name__ == "__main__":
    sys.exit(main())