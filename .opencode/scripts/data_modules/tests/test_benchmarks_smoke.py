#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""计时基线的冒烟测试。

**不验证性能**——共享 CI runner 上的绝对耗时没有意义，把它写成断言只会变成
flaky 的来源。这里只保证：

1. 脚本能 import、能跑完（防止重构把 API 改坏了而没人发现）；
2. 报告结构稳定（`name` / `median_ms` / `p95_ms` 齐全且为正数）；
3. 表格渲染包含所有基准项。

真正的用法是改动前后**各跑一次** `benchmark_core.py` 对比中位数，见
`.tmp/analysis/BASELINE.md`。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

BENCH_DIR = Path(__file__).resolve().parents[2] / "benchmarks"

# 用 importlib 从路径加载，而不是 `sys.path.insert` + `import benchmark_core`：
# 后者会在本文件留下一条顶层 `import benchmark_core`，被
# `test_dep_declarations` 判成「未在 requirements 声明的第三方依赖」。
# 它本来就不是依赖，是仓库内的脚本——不该为了迁就检查器去伪造声明。
_spec = importlib.util.spec_from_file_location(
    "benchmark_core", BENCH_DIR / "benchmark_core.py")
benchmark_core = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("benchmark_core", benchmark_core)
_spec.loader.exec_module(benchmark_core)


@pytest.fixture(scope="module")
def report():
    return benchmark_core.run(quick=True)


def test_report_shape(report):
    assert report["scale"]["repeat"] >= 1
    assert isinstance(report["results"], list)
    assert report["results"], "至少要有一个基准项"


def test_each_item_has_positive_timings(report):
    for row in report["results"]:
        assert row["name"], row
        for key in ("median_ms", "p95_ms", "min_ms", "max_ms"):
            assert row[key] >= 0, (key, row)
        assert row["median_ms"] > 0, f"{row['name']} 的中位数必须为正"


def test_p95_not_below_median(report):
    for row in report["results"]:
        assert row["p95_ms"] >= row["median_ms"], row["name"]


def test_table_lists_every_item(report):
    text = benchmark_core.format_table(report)
    for row in report["results"]:
        assert row["name"] in text


def test_bench_helper_measures(report):
    """`bench()` 本身要能计到真实耗时，而不是常数 0。"""
    import time

    result = benchmark_core.bench("sleep", lambda: time.sleep(0.002), repeat=2, warmup=0)
    assert result["median_ms"] >= 1.0, result
