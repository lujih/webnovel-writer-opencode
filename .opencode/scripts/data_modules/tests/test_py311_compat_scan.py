#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CI 用 Python **3.11**，本机只有 3.12 —— 版本差必须在本地被抓住。

历史事故：`tempfile.TemporaryDirectory(delete=)` 是 3.14 才有的参数，本机全绿，
CI 在**收集阶段**全灭（filelock 导入期探测是第一个触发者），连红 12 次都没人
联想到版本差。

本测试把「当前代码库没有 3.11 无法解析的语法」钉住——**CI 的 pytest 步骤会自动
执行它**，所以不需要改 workflow。新增的 3.12+ 语法（PEP 695 泛型、PEP 701 的
f-string 同引号嵌套）会让它立刻变红。

`check_py311_compat.py` 用 AST 而非正则判断 PEP 701：正则会把注释与字符串里的
**示例文本**也当成代码（第一版报了 10 个假阳性，其中 2 个是脚本文档自身的示例）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2]

_spec = importlib.util.spec_from_file_location(
    "check_py311_compat", SCRIPTS / "check_py311_compat.py")
check_py311_compat = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("check_py311_compat", check_py311_compat)
_spec.loader.exec_module(check_py311_compat)

scan = check_py311_compat.scan
scan_file = check_py311_compat.scan_file


class TestCurrentTreeIsClean:
    def test_no_py311_incompatible_syntax(self):
        issues = scan(SCRIPTS)
        assert not issues, (
            "发现 3.11 解析不了的语法（CI 会红）：\n"
            + "\n".join(f"  {i['path']}:{i['line']} [{i['kind']}] {i['detail']}" for i in issues)
        )


class TestDetectorCatchesRealProblems:
    def _write(self, tmp_path: Path, name: str, source: str) -> Path:
        path = tmp_path / name
        path.write_text(source, encoding="utf-8")
        return path

    def test_flags_pep701_fstring(self, tmp_path):
        # 外层双引号、表达式内也用双引号 —— 3.11 直接 SyntaxError
        path = self._write(tmp_path, "bad_f.py", 'value = f"{d["k"]}"\n')
        issues = scan_file(path)
        assert any(i["kind"] == "pep701_fstring" for i in issues), issues

    def test_accepts_mixed_quotes(self, tmp_path):
        # 外层双引号、表达式内单引号 —— 3.11 合法
        path = self._write(tmp_path, "ok_f.py", "value = f\"{d['k']}\"\n")
        assert scan_file(path) == []

    def test_ignores_examples_inside_strings_and_comments(self, tmp_path):
        """正则版会把这些当代码；AST 版不会。"""
        source = (
            '"""文档里写了 f"{d["k"]}" 这个反例。"""\n'
            "# 注释里也写 f\"{d[\"k\"]}\"\n"
            "value = 1\n"
        )
        path = self._write(tmp_path, "doc_only.py", source)
        assert scan_file(path) == []

    def test_flags_py312_only_syntax(self, tmp_path):
        # PEP 695 泛型：3.12+ 才有
        path = self._write(tmp_path, "generic.py", "def first[T](items: list[T]) -> T:\n    return items[0]\n")
        issues = scan_file(path)
        assert any(i["kind"] == "py312_syntax" for i in issues), issues
