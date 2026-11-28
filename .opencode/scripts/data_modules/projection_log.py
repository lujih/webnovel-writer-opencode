#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


SCHEMA_VERSION = "webnovel-projection-log/v1"
PROJECTION_LOG_REL = Path(".webnovel") / "projection_log.jsonl"


def projection_log_path(project_root: str | Path) -> Path:
    return Path(project_root) / PROJECTION_LOG_REL


def commit_hash(commit_payload: dict[str, Any]) -> str:
    raw = json.dumps(commit_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _overall_status(writers: dict[str, dict[str, Any]]) -> str:
    statuses = {str(item.get("status") or "") for item in writers.values()}
    if any(status.startswith("failed") or status == "failed" for status in statuses):
        return "failed"
    if statuses and statuses <= {"skipped"}:
        return "skipped"
    if "pending" in statuses:
        return "pending"
    return "done"


def build_projection_run(
    *,
    project_root: str | Path,
    commit_payload: dict[str, Any],
    writer_results: dict[str, dict[str, Any]],
    commit_path: str | Path | None = None,
) -> dict[str, Any]:
    meta = commit_payload.get("meta") if isinstance(commit_payload, dict) else {}
    chapter = int((meta or {}).get("chapter") or 0)
    if commit_path is None and chapter > 0:
        commit_path = Path(project_root) / ".story-system" / "commits" / f"chapter_{chapter:03d}.commit.json"
    writers = {str(name): dict(result) for name, result in writer_results.items()}
    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": uuid4().hex,
        "created_at": _now_iso(),
        "chapter": chapter,
        "commit_path": str(commit_path or ""),
        "commit_hash": commit_hash(commit_payload),
        "commit_status": str((meta or {}).get("status") or ""),
        "status": _overall_status(writers),
        "writers": writers,
        "projection_status": dict(commit_payload.get("projection_status") or {}),
    }


def projection_status_from_run(run: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(run, dict):
        return {}
    writers = run.get("writers")
    if isinstance(writers, dict):
        statuses = {
            str(name): str(result.get("status") or "")
            for name, result in writers.items()
            if isinstance(result, dict) and result.get("status")
        }
        if statuses:
            return statuses
    projection_status = run.get("projection_status")
    if isinstance(projection_status, dict):
        return {str(name): str(status) for name, status in projection_status.items()}
    return {}


def projection_run_failed(run: dict[str, Any] | None) -> bool:
    if not isinstance(run, dict):
        return False
    if str(run.get("status") or "").startswith("failed"):
        return True
    return any(status.startswith("failed") for status in projection_status_from_run(run).values())


def projection_run_pending(run: dict[str, Any] | None) -> bool:
    if not isinstance(run, dict):
        return False
    if str(run.get("status") or "") == "pending":
        return True
    return any(status == "pending" for status in projection_status_from_run(run).values())


def append_projection_run(
    project_root: str | Path,
    commit_payload: dict[str, Any],
    writer_results: dict[str, dict[str, Any]],
    *,
    commit_path: str | Path | None = None,
) -> dict[str, Any]:
    record = build_projection_run(
        project_root=project_root,
        commit_payload=commit_payload,
        writer_results=writer_results,
        commit_path=commit_path,
    )
    path = projection_log_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".jsonl.lock")

    def _append(handle) -> None:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True))
        handle.write("\n")

    # 只在 filelock 不可用/超时时降级为无锁追加；**写入异常必须冒出去**。
    # 修复前这里是 `except (ImportError, Exception)`——等于 `except Exception`，
    # 把磁盘满、权限不足这类真实错误也当成"锁不可用"，再用一次写入掩盖掉真正的
    # 原因（第二次失败时抛出的是第二个异常，第一个永远看不到）。
    lock = None
    try:
        import filelock

        lock = filelock.FileLock(str(lock_path), timeout=5)
    except ImportError:  # pragma: no cover - filelock 是必装依赖
        lock = None

    if lock is None:
        with path.open("a", encoding="utf-8") as handle:
            _append(handle)
        return record

    with lock:
        with path.open("a", encoding="utf-8") as handle:
            _append(handle)
    return record


def read_projection_runs(project_root: str | Path, *, chapter: int | None = None) -> list[dict[str, Any]]:
    path = projection_log_path(project_root)
    if not path.is_file():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if chapter is not None:
            try:
                record_chapter = int(payload.get("chapter") or 0)
            except (TypeError, ValueError):
                continue
            if record_chapter != int(chapter):
                continue
        records.append(payload)
    return records


def latest_projection_run(project_root: str | Path, *, chapter: int | None = None) -> dict[str, Any] | None:
    """返回最新的投影运行记录。从文件末尾反向搜索以提高效率。"""
    path = projection_log_path(project_root)
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    # 从后向前搜索
    for line in reversed(lines):
        raw = line.strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if chapter is not None:
            try:
                record_chapter = int(payload.get("chapter") or 0)
            except (TypeError, ValueError):
                continue
            if record_chapter != int(chapter):
                continue
        return payload
    return None


#: 投影失败时给作者的下一步。必须是**真实存在**的命令——建议一个不存在的
#: 命令比不给建议更糟（作者会照着敲、然后得到 "unrecognized arguments"）。
#:
#: 曾经这里写的是 `ssot rebuild`（重建全部投影）。它有两大问题：代价过大
#: （一章失败要重建整本书），且 `write-guard.js` 的白名单里早写着
#: `projections retry`——那个命令却从未实现。第 13 轮补上了 `projections retry`，
#: 建议随之换成精准的一条。
RECOVER_ACTION_TEMPLATE = (
    "python .opencode/scripts/webnovel.py projections retry --chapter {chapter}"
    "（只重跑该章投影；仍是失败再考虑 `ssot rebuild`）"
)


def recover_action(chapter: int) -> str:
    return RECOVER_ACTION_TEMPLATE.format(chapter=int(chapter))


def summarize_runs(
    project_root: str | Path,
    *,
    chapter: int | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """把投影运行记录整理成「人能判断该做什么」的摘要（新 → 旧）。

    为什么需要它：`projection_log` 早已被 `chapter_commit_service` 写入、被
    `postcommit` gate 与 `project_phase` 读取，但**没有任何作者可见的入口**——
    投影失败时作者只看到"某个阶段报错"，不知道该看哪一条、下一步做什么。
    外部产品（NarraLume 的 run center）把这件事当一等功能：任务步骤、错误、
    可恢复动作摆在同一视图里。
    """
    runs = read_projection_runs(project_root, chapter=chapter)
    size = max(1, int(limit or 10))
    rows: list[dict[str, Any]] = []
    for run in reversed(runs[-size:]):
        statuses = projection_status_from_run(run)
        failed = sorted(name for name, status in statuses.items() if status.startswith("failed"))
        pending = sorted(name for name, status in statuses.items() if status == "pending")
        ok = not failed and not pending
        rows.append({
            "chapter": int(run.get("chapter") or 0),
            "status": str(run.get("status") or ""),
            "commit_status": str(run.get("commit_status") or ""),
            "created_at": str(run.get("created_at") or ""),
            "run_id": str(run.get("run_id") or ""),
            "failed_writers": failed,
            "pending_writers": pending,
            "ok": ok,
            "action": "" if ok else recover_action(int(run.get("chapter") or 0)),
        })
    return rows


def format_runs(rows: list[dict[str, Any]]) -> str:
    """把摘要渲染成终端文本。空列表也要有一行说明，而不是空白。"""
    if not rows:
        return "（没有投影运行记录。投影日志在 .webnovel/projection_log.jsonl）"
    lines: list[str] = []
    for row in rows:
        if row["failed_writers"]:
            mark, detail = "❌", " 失败=" + ",".join(row["failed_writers"])
        elif row["pending_writers"]:
            mark, detail = "⚠️", " 未完成=" + ",".join(row["pending_writers"])
        else:
            mark, detail = "✅", ""
        status = row["status"] or "-"
        lines.append(
            f"{mark} 第{row['chapter']:03d}章 {status}{detail}  ({row['created_at']})"
        )
        if row["action"]:
            lines.append(f"   ↳ 下一步：{row['action']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """`run-log list` 的 CLI 实现（只读）。"""
    import argparse

    parser = argparse.ArgumentParser(description="Projection run log（只读）")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("action", nargs="?", default="list", choices=["list"])
    parser.add_argument("--chapter", type=int, default=None)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是文本表")
    args = parser.parse_args(argv)

    rows = summarize_runs(args.project_root, chapter=args.chapter, limit=args.limit)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(format_runs(rows))
    return 0
