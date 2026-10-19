#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`StorySystemEngine` 的 CSV 读取健壮性。

CSV 是**知识层**（题材路由、反模式、裁决规则），位于六层数据流的最上游。
读坏了，后面每一层都跟着偏——而且不会报错，只会"路由到错误的题材"。

`_load_csv_rows()` 用的是 `csv.DictReader`，对畸形行有三种默认行为，
本仓库都没有处理：

1. **列数多于表头** → 多出来的值被塞进 `None` 键
   `{'题材': '仙侠', None: ['多一列', '再多一列']}`

2. **列数少于表头** → 缺的列取 `None`
   `{'题材': '科幻', '关键词': None, '说明': None}`

3. **全空行** → 保留成一行全空字符串
   `{'题材': '', '关键词': '', '说明': '', None: ['']}`

`None` 键最危险：下游 `row.get("关键词")` 之类的访问看着正常，但一旦有人
遍历 `row.items()` 或做 `dict(row)` 比较，就会撞上 `None` 键。而全空行会
让「文件存在但没有有效内容」看起来像"有数据"，掩盖配置缺失。

本文件钉住：畸形行要么被剔除，要么被明确记录，不能让 `None` 键流进业务逻辑。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from data_modules.story_system_engine import StorySystemEngine  # noqa: E402


@pytest.fixture
def engine(tmp_path: Path) -> StorySystemEngine:
    return StorySystemEngine(tmp_path)


def _write(tmp_path: Path, name: str, content: str) -> None:
    (tmp_path / f"{name}.csv").write_text(content, encoding="utf-8")


class TestWellFormedCsvUnchanged:
    """正常 CSV 的行为不能变。"""

    def test_reads_rows(self, engine, tmp_path):
        _write(tmp_path, "题材路由", "题材,关键词\n玄幻,a|b\n仙侠,c\n")
        rows = engine._load_csv_rows("题材路由")
        assert len(rows) == 2
        assert rows[0]["题材"] == "玄幻"

    def test_missing_file_returns_empty(self, engine):
        assert engine._load_csv_rows("不存在的表") == []

    def test_bom_is_stripped(self, engine, tmp_path):
        (tmp_path / "题材路由.csv").write_bytes(
            "\ufeff题材,关键词\n玄幻,a\n".encode("utf-8"))
        rows = engine._load_csv_rows("题材路由")
        assert rows and rows[0]["题材"] == "玄幻"

    def test_cache_returns_copy(self, engine, tmp_path):
        _write(tmp_path, "题材路由", "题材,关键词\n玄幻,a\n")
        first = engine._load_csv_rows("题材路由")
        first.clear()
        assert len(engine._load_csv_rows("题材路由")) == 1, "缓存被调用方改坏了"


class TestNoNoneKeyLeaks:
    """核心：`None` 键不能流进业务逻辑。"""

    def test_extra_columns_do_not_create_none_key(self, engine, tmp_path):
        _write(tmp_path, "题材路由", "题材,关键词,说明\n仙侠,c,d,多一列,再多一列\n")
        rows = engine._load_csv_rows("题材路由")
        assert rows, "该行仍应被读取（前面几列是有效的）"
        for row in rows:
            assert None not in row, (
                f"畸形行产生了 None 键：{row}。"
                f"下游遍历 row.items() 或做 dict 比较时会撞上 None。"
            )

    def test_extra_columns_keep_valid_prefix(self, engine, tmp_path):
        _write(tmp_path, "题材路由", "题材,关键词,说明\n仙侠,c,d,多一列\n")
        row = engine._load_csv_rows("题材路由")[0]
        assert row["题材"] == "仙侠"
        assert row["关键词"] == "c"

    def test_missing_columns_are_empty_string_not_none(self, engine, tmp_path):
        _write(tmp_path, "题材路由", "题材,关键词,说明\n科幻\n")
        row = engine._load_csv_rows("题材路由")[0]
        assert row["关键词"] == "", (
            f"缺列被填成 None（{row}）——下游 .strip() / 拼接会崩"
        )
        assert row["说明"] == ""


class TestBlankRowsAreDropped:
    """全空行不该被当成一条数据。"""

    def test_fully_blank_row_dropped(self, engine, tmp_path):
        _write(tmp_path, "题材路由", "题材,关键词\n,, \n玄幻,a\n")
        rows = engine._load_csv_rows("题材路由")
        assert len(rows) == 1, f"全空行没被剔除：{rows}"
        assert rows[0]["题材"] == "玄幻"

    def test_file_of_only_blank_rows_reads_as_empty(self, engine, tmp_path):
        _write(tmp_path, "题材路由", "题材,关键词\n,\n,,\n")
        assert engine._load_csv_rows("题材路由") == [], (
            "只有空行的文件应读成空——否则「配置缺失」会被掩盖成「有数据」"
        )


class TestNoNoneKeyAnywhereInEngine:
    """端到端：整个引擎不应在任何路径上把 None 键带出来。"""

    def test_all_tables_have_no_none_keys(self, engine, tmp_path):
        tables = {
            "题材路由": "题材,关键词,说明\n玄幻,a,b,extra\n\n科幻\n",
            "裁决规则": "题材,关键词,意图与同义词\n玄幻,x,y,extra\n",
        }
        for name, content in tables.items():
            _write(tmp_path, name, content)

        for name in tables:
            for row in engine._load_csv_rows(name):
                assert None not in row, f"{name} 产生了 None 键：{row}"


class TestMalformedRowsAreObservable:
    """被剔除的行必须可计数，否则数据质量问题会静默。"""

    def test_reports_dropped_row_count(self, engine, tmp_path):
        _write(tmp_path, "题材路由", "题材,关键词\n,, \n玄幻,a\n仙侠,b\n")
        engine._load_csv_rows("题材路由")

        # 允许任何合理的暴露方式：属性、方法或返回结构
        candidates = [
            getattr(engine, "csv_diagnostics", None),
            getattr(engine, "csv_warnings", None),
            getattr(engine, "_csv_diagnostics", None),
        ]
        diagnostics = next((c for c in candidates if c), None)
        assert diagnostics, (
            "剔除畸形行后没有任何诊断信息。CSV 是知识层的最上游，"
            "静默丢行会让「路由不到正确题材」无从排查。"
        )
        blob = str(diagnostics)
        assert "题材路由" in blob
