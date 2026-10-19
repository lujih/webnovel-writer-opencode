#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
webnovel 统一入口脚本（无须 `cd`）

用法示例：
  python "<SCRIPTS_DIR>/webnovel.py" preflight
  python "<SCRIPTS_DIR>/webnovel.py" where
  python "<SCRIPTS_DIR>/webnovel.py" index stats

说明：
- 该脚本仅负责把 `.opencode/scripts` 加入 sys.path，然后转发到 `data_modules.webnovel`。
- 适配 skills/agents 在项目级或用户级（~/.claude）安装时的调用方式。

错误呈现约定
------------
这里是所有 skills/agents 的唯一入口，也是用户第一次接触本项目时敲的第一条
命令。**预期内的用户错误**必须显示成一行可读的中文提示，而不是 Python 栈：

    ERROR: 找不到书项目（缺少 .webnovel/state.json）。
           先运行 /webnovel-init 初始化，或显式传 --project-root <书目录>。

新用户最常踩的就是「装好了、还没 init、先跑一条命令看看」——旧实现此时
会甩出两层 FileNotFoundError 栈帧（`_resolve_root` 先按 CWD 解析一次、
再按脚本所在工作区解析一次），真实信息被埋在最后一行。

反过来，**程序自身的 bug 仍然要冒栈**——把 catch-all 加进来会让真正的
缺陷伪装成「参数不对」，排查就断了。所以这里只捕获下面这组明确的类型。
"""

from __future__ import annotations

import sys
from pathlib import Path

from runtime_compat import enable_windows_utf8_stdio

#: 属于「用户可修复」的错误类型。新增类型前先想清楚：这是使用者的问题，
#: 还是代码的问题？只有前者才该被美化。
_USER_FACING_ERRORS = (
    FileNotFoundError,   # 找不到书项目 / 输入文件
    NotADirectoryError,  # --project-root 指向的是文件
)

_PROJECT_HINT = (
    "ERROR: 找不到书项目（缺少 .webnovel/state.json）。\n"
    "       先运行 /webnovel-init 初始化，或显式传 --project-root <书目录>。"
)


def _format_user_error(exc: BaseException) -> str:
    """把预期内的用户错误翻成一行可执行提示。"""
    if isinstance(exc, (FileNotFoundError, NotADirectoryError)):
        detail = str(exc).strip()
        # 解析器自带的提示已经足够清楚（含 "Run /webnovel-init" 之类），
        # 此时不要用一个笼统的通用提示把它盖掉。
        if detail and ("init" in detail or "--project-root" in detail or "manifest" in detail):
            return f"ERROR: {detail}"
        if detail:
            return f"ERROR: {detail}\n       若这是书项目路径，请确认 .webnovel/state.json 存在。"
        return _PROJECT_HINT
    return f"ERROR: {exc}"


def main() -> int:
    scripts_dir = Path(__file__).resolve().parent
    sys.path.insert(0, str(scripts_dir))

    # 延迟导入，避免 sys.path 未就绪
    from data_modules.webnovel import main as _main

    try:
        _main()
    except SystemExit:
        raise
    except _USER_FACING_ERRORS as exc:
        print(_format_user_error(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    enable_windows_utf8_stdio(skip_in_pytest=True)
    sys.exit(main())
