#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""关键路径的计时基线（goal 要求的「基线数据」）。

为什么需要它：前九轮每轮都跑测试，但「全绿」只说明**正确**，不说明**不变慢**。
本项目最容易退化的三处是无界增长的循环：

  * `changes audit` 逐章读 commit 文件再归一化；
  * `annotate_changes` 对每条变更做一次 lock 字典查找（O(变更数)）；
  * `context_manager` 组装时对每个出场实体查一次关系。

这些都不会被单元测试抓到——它们只是「慢」，功能完全正确。本脚本给出可复现的
中位数/p95，改动前后各跑一次就能看出有没有退化。

用法：
    python .opencode/scripts/benchmarks/benchmark_core.py            # 表格
    python .opencode/scripts/benchmarks/benchmark_core.py --json     # JSON
    python .opencode/scripts/benchmarks/benchmark_core.py --quick    # CI/冒烟用

**不要**把它接进 pytest 主套件：计时在共享 CI runner 上没有意义，
只会变成 flaky 的来源。`test_benchmarks_smoke.py` 只验证脚本能跑通、结构正确。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

SCRIPTS = Path(__file__).resolve().parents[1]
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def bench(name: str, fn: Callable[[], Any], *, repeat: int = 5, warmup: int = 2) -> Dict[str, Any]:
    """跑 `fn` 并返回中位数/p95（毫秒）。每次计时都是完整一次调用。"""
    for _ in range(warmup):
        fn()
    samples: List[float] = []
    for _ in range(repeat):
        start = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - start) * 1000.0)
    samples.sort()
    median = statistics.median(samples)
    p95 = samples[min(len(samples) - 1, int(len(samples) * 0.95))]
    return {
        "name": name,
        "median_ms": round(median, 3),
        "p95_ms": round(p95, 3),
        "min_ms": round(samples[0], 3),
        "max_ms": round(samples[-1], 3),
        "repeat": repeat,
    }


# --------------------------------------------------------------------------- #
# 合成数据：足够大以暴露 O(n²)，又足够小以便 30 秒内跑完
# --------------------------------------------------------------------------- #

def _make_project(root: Path, *, chapters: int = 30, deltas_per_chapter: int = 30) -> Path:
    from data_modules.config import DataModulesConfig
    from data_modules.index_manager import IndexManager

    (root / ".webnovel").mkdir(parents=True, exist_ok=True)
    (root / ".story-system" / "commits").mkdir(parents=True, exist_ok=True)
    (root / ".webnovel" / "state.json").write_text(
        json.dumps({
            "progress": {"current_chapter": chapters},
            "protagonist_state": {"location": {"current": "乌坦城"}},
            "entities_v3": {
                f"e{i}": {"name": f"实体{i}", "type": "角色"} for i in range(30)
            },
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (root / ".story-system" / "canon_locks.json").write_text(
        json.dumps({"locks": [
            {"subject": f"e{i}", "field": "realm", "value": "斗师", "chapter": 1}
            for i in range(30)
        ]}, ensure_ascii=False),
        encoding="utf-8",
    )

    for chapter in range(1, chapters + 1):
        payload = {
            "meta": {"chapter": chapter, "status": "accepted"},
            "state_deltas": [
                {"entity_id": f"e{i % 30}", "field": "realm" if i % 3 == 0 else "mood",
                 "old": "旧", "new": f"新{chapter}-{i}"}
                for i in range(deltas_per_chapter)
            ],
            "accepted_events": [],
        }
        (root / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    config = DataModulesConfig.from_project_root(str(root))
    index = IndexManager(config)
    from data_modules.index_manager import RelationshipMeta
    for i in range(30):
        index.upsert_relationship(RelationshipMeta(
            from_entity=f"e{i}", to_entity=f"e{(i + 1) % 30}", type="盟友",
            description="合成关系", chapter=1,
        ))
    return root


def run(*, quick: bool = False) -> Dict[str, Any]:
    """跑全部基准。`quick` 供冒烟测试用（规模与重复数都缩到最小）。"""
    repeat = 1 if quick else 5
    chapters = 3 if quick else 30
    deltas = 5 if quick else 30

    from data_modules.canon_locks import detect_conflicts, load_locks
    from data_modules.state_change_log import (
        annotate_changes, audit_changes, collect_changes,
    )

    results: List[Dict[str, Any]] = []
    tmp = Path(tempfile.mkdtemp(prefix="wn-bench-"))
    root = _make_project(tmp, chapters=chapters, deltas_per_chapter=deltas)

    # 1) 单章归一化：O(deltas)
    payload = json.loads(
        (root / ".story-system" / "commits" / "chapter_001.commit.json").read_text(encoding="utf-8"))
    results.append(bench("changes.collect_changes（单章）", lambda: collect_changes(payload),
                         repeat=repeat))

    # 2) 归一化 + 风险分级 + lock 比对：O(deltas × log locks)
    rows = collect_changes(payload)
    locks = load_locks(root)
    results.append(bench("changes.annotate_changes（含 lock 比对）",
                         lambda: annotate_changes(rows, locks), repeat=repeat))

    # 3) 回溯审计：O(章数 × deltas)
    results.append(bench(f"changes.audit_changes（{chapters} 章）",
                         lambda: audit_changes(root, limit=chapters, locks=locks),
                         repeat=repeat))

    # 4) lock 冲突检测：O(事件数)
    events = [
        {"event_type": "power_breakthrough", "subject": f"e{i % 30}",
         "payload": {"new_realm": "斗宗"}}
        for i in range(200)
    ]
    results.append(bench("canon.detect_conflicts（200 事件 × 30 锁）",
                         lambda: detect_conflicts(locks, events), repeat=repeat))

    # 5) CED 计算：纯函数，O(issues)
    from data_modules.review_schema import ReviewIssue, compute_ced
    issues = [
        ReviewIssue(category="timeline_plot", severity="medium",
                    description=f"合成问题 {i}", blocking=(i % 5 == 0))
        for i in range(200)
    ]
    results.append(bench("review.compute_ced（200 issues）",
                         lambda: compute_ced(issues, 3000), repeat=repeat))

    # 6) review_metrics 写入 + 分类聚合（SQLite）
    from data_modules.config import DataModulesConfig
    from data_modules.index_manager import IndexManager, ReviewMetrics
    index = IndexManager(DataModulesConfig.from_project_root(str(root)))

    def _write_then_aggregate() -> None:
        for chapter in range(1, 11 if not quick else 2):
            index.save_review_metrics(ReviewMetrics(
                start_chapter=chapter, end_chapter=chapter, chapter_words=3000,
                ced_raw=1.0, ced_weighted=2.0, ced_blocking=0.5,
                error_category_counts={"timeline_plot": chapter, "worldbuilding": 1},
            ))
        index.get_error_category_totals(limit=50)

    results.append(bench("index.save_review_metrics + 分类聚合", _write_then_aggregate,
                         repeat=repeat))

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "scale": {"chapters": chapters, "deltas_per_chapter": deltas, "repeat": repeat},
        "results": results,
    }


def format_table(report: Dict[str, Any]) -> str:
    lines = [
        f"规模：{report['scale']['chapters']} 章 × {report['scale']['deltas_per_chapter']} 变更"
        f"，repeat={report['scale']['repeat']}",
        "",
        f"{'基准项':<46}{'中位数(ms)':>12}{'p95(ms)':>10}{'min':>8}{'max':>8}",
        "-" * 84,
    ]
    for row in report["results"]:
        lines.append(
            f"{row['name']:<46}{row['median_ms']:>12}{row['p95_ms']:>10}"
            f"{row['min_ms']:>8}{row['max_ms']:>8}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="关键路径计时基线")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--quick", action="store_true", help="最小规模（冒烟用）")
    args = parser.parse_args(argv)

    report = run(quick=args.quick)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_table(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
