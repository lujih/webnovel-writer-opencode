#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""扫描 `.opencode/scripts` 下是否有 Python 3.11 无法解析的语法。

**为什么需要**：CI（`.github/workflows/test.yml`）跑的是 **Python 3.11**，而本机
只有 3.12。历史上就因为这个差异吃过一次大亏：`tempfile.TemporaryDirectory(delete=)`
是 3.14 才有的参数，本机全绿、CI 在**收集阶段**全灭（filelock 导入期探测是第一个
触发者），连红 12 次都没人联想到版本差。

本脚本检查两层：

1. `ast.parse(..., feature_version=(3, 11))`——抓 3.12+ 的**语法**（PEP 695 泛型等）；
2. **PEP 701 的 f-string 同引号嵌套**——`ast` 对这项不敏感（3.12 解析器照收），
   但 3.11 会 SyntaxError。

第 2 项刻意用 **AST 而非正则**：正则会把注释和字符串里的**示例文本**也当代码
（第一版就因此报了 10 个假阳性，其中 2 个是本文档自身的示例）。遍历 `JoinedStr`
节点只看真实 f-string，且要求「外层引号字符出现在 `{}` 表达式源码里」。

**不检查 API 可用性**（某函数在 3.11 是否不存在）。那类只能靠真跑 3.11。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path


def _outer_quote(segment: str) -> str:
    """返回 f-string 的外层引号字符（`'` 或 `"`）；识别不出返空串。"""
    for prefix in ("f", "F", "rf", "fr", "Rf", "fR", "Fr", "RF", "FR", "rF", "fF", "Ff"):
        if segment.startswith(prefix):
            rest = segment[len(prefix):]
            for quote in ('"""', "'''", '"', "'"):
                if rest.startswith(quote):
                    return quote[0]
            return ""
    return ""


def scan_file(path: Path) -> list[dict]:
    issues: list[dict] = []
    try:
        source = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        return [{"path": str(path), "line": 0, "kind": "unreadable", "detail": str(exc)}]

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [{"path": str(path), "line": int(exc.lineno or 0),
                 "kind": "syntax_error", "detail": str(exc.msg)}]

    # 语法层：3.11 解析器是否吃得下
    try:
        ast.parse(source, filename=str(path), feature_version=(3, 11))
    except SyntaxError as exc:
        issues.append({
            "path": str(path),
            "line": int(exc.lineno or 0),
            "kind": "py312_syntax",
            "detail": str(exc.msg),
        })

    # PEP 701：真实 f-string 里外层引号出现在表达式内部
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        segment = ast.get_source_segment(source, node)
        if not segment:
            continue
        quote = _outer_quote(segment)
        if not quote:
            continue
        for value in node.values:
            if not isinstance(value, ast.FormattedValue):
                continue
            inner = ast.get_source_segment(source, value.value) or ""
            if quote in inner:
                issues.append({
                    "path": str(path),
                    "line": int(getattr(node, "lineno", 0)),
                    "kind": "pep701_fstring",
                    "detail": f"f-string 表达式内含与外层相同的引号 {quote!r}（3.12+ 语法）",
                })
                break
    return issues


def scan(root: Path) -> list[dict]:
    issues: list[dict] = []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        issues.extend(scan_file(path))
    return issues


def main(argv: list[str] | None = None) -> int:
    root = Path(argv[0]) if argv else Path(__file__).resolve().parents[1]
    issues = scan(root)
    if not issues:
        print(f"OK：{root} 下没有 3.11 无法解析的语法。")
        return 0
    print(f"发现 {len(issues)} 处 3.11 不兼容：")
    for item in issues:
        rel = item["path"].replace(str(root) + "\\", "").replace(str(root) + "/", "")
        print(f"  {rel}:{item['line']}  [{item['kind']}] {item['detail']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
