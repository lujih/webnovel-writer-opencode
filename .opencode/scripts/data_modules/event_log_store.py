#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List

from .story_contracts import StoryContractPaths, read_json_if_exists, write_json
from .story_event_schema import StoryEvent


class EventLogStore:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).expanduser().resolve()
        self.paths = StoryContractPaths.from_project_root(self.project_root)

    @contextmanager
    def _connect(self, *, row_factory: bool = False) -> Iterator[sqlite3.Connection]:
        """统一 SQLite 连接管理，确保连接始终关闭。"""
        db_path = self.project_root / ".webnovel" / "index.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(db_path), timeout=30)
        if row_factory:
            conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            yield conn
        finally:
            conn.close()

    def write_events(self, chapter: int, events: List[dict]) -> Path:
        """写事件：JSON 文件 + SQLite 镜像。

        两条路径不可能真正原子（一个是文件、一个是数据库），所以采用
        **失败即回滚文件**：SQLite 失败时把刚写的 JSON 也删掉，让磁盘回到
        调用前的状态。否则会留下一份"文件有、镜像没有"的分叉：
        `chapter_commit_service.py:271` 只对写失败做 `logger.warning`，
        分叉会静默留下来，直到 dashboard 少事件才被发现。
        """
        normalized = self.normalize_events(chapter, events)
        path = self.paths.event_json(chapter)
        previous = path.read_bytes() if path.is_file() else None

        write_json(path, normalized)
        try:
            self._write_sqlite_mirror(normalized)
        except Exception:
            # 回滚到写入前的字节（可能是"原本没有这个文件"）
            try:
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(previous)
            except OSError:
                # 回滚失败时至少留个痕迹：健康检查会发现分叉。
                print(
                    f"⚠️ 事件文件回滚失败，可能与 SQLite 镜像分叉: {path}",
                    file=sys.stderr,
                )
            raise
        return path

    def read_events(self, chapter: int) -> List[Dict[str, Any]]:
        return list(read_json_if_exists(self.paths.event_json(chapter)) or [])

    def list_recent(self, chapter: int | None = None, limit: int = 200) -> List[Dict[str, Any]]:
        db_path = self.project_root / ".webnovel" / "index.db"
        if not db_path.is_file():
            return []
        with self._connect(row_factory=True) as conn:
            try:
                if chapter is not None:
                    rows = conn.execute(
                        """
                        SELECT event_id, chapter, event_type, subject, payload_json
                        FROM story_events
                        WHERE chapter = ?
                        ORDER BY id DESC
                        LIMIT ?
                        """,
                        (chapter, limit),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        """
                        SELECT event_id, chapter, event_type, subject, payload_json
                        FROM story_events
                        ORDER BY chapter DESC, id DESC
                        LIMIT ?
                        """,
                        (limit,),
                    ).fetchall()
            except sqlite3.OperationalError:
                return []

        result: List[Dict[str, Any]] = []
        for row in rows:
            payload = {}
            try:
                payload = json.loads(row["payload_json"] or "{}")
            except json.JSONDecodeError:
                payload = {}
            result.append(
                {
                    "event_id": row["event_id"],
                    "chapter": row["chapter"],
                    "event_type": row["event_type"],
                    "subject": row["subject"],
                    "payload": payload,
                }
            )
        return result

    def health(self) -> Dict[str, Any]:
        """事件日志双写一致性检查。

        事件同时存在两处：`.story-system/events/chapter_*.events.json`
        （人类可读、进 git）与 index.db 的 `story_events` 表（查询用）。
        两者本应一一对应。此前本函数已经算出了两个数字却**从不比较**，
        永远返回 `ok: True`——而 `story_events.py` 会把它原样打印给用户
        （`webnovel.py story-events health`），于是分叉显示为健康。

        这里按「事件 ID 集合」而非「行数」比较：一个事件文件里可以有多条
        事件，行数相等不代表内容相同。
        """
        db_path = self.project_root / ".webnovel" / "index.db"
        event_files = sorted(self.paths.events_dir.glob("chapter_*.events.json"))
        file_count = len(event_files)

        file_ids: set[str] = set()
        for path in event_files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except (json.JSONDecodeError, OSError):
                # 解析不了的文件本身就是问题，记一个哨兵让它进不一致集合
                file_ids.add(f"<unreadable:{path.name}>")
                continue
            for item in payload if isinstance(payload, list) else []:
                if isinstance(item, dict) and item.get("event_id"):
                    file_ids.add(str(item["event_id"]))

        sqlite_rows = 0
        sqlite_ids: set[str] = set()
        if db_path.is_file():
            with self._connect() as conn:
                try:
                    rows = conn.execute("SELECT event_id FROM story_events").fetchall()
                    sqlite_ids = {str(r[0]) for r in rows}
                    sqlite_rows = len(sqlite_ids)
                except sqlite3.OperationalError:
                    sqlite_rows = 0

        only_in_files = file_ids - sqlite_ids
        only_in_sqlite = sqlite_ids - file_ids

        result: Dict[str, Any] = {
            "ok": not (only_in_files or only_in_sqlite),
            "sqlite_rows": sqlite_rows,
            "event_files": file_count,
            "event_ids_in_files": len(file_ids),
        }
        if only_in_files or only_in_sqlite:
            broken = [i for i in file_ids if i.startswith("<unreadable:")]
            detail = (
                f"事件日志双写分叉：{len(only_in_files)} 条只在文件里、"
                f"{len(only_in_sqlite)} 条只在 SQLite 里"
            )
            if broken:
                detail += f"；另有 {len(broken)} 个文件无法解析"
            result["mismatch"] = detail
            result["only_in_files"] = sorted(only_in_files)[:20]
            result["only_in_sqlite"] = sorted(only_in_sqlite)[:20]
            result["repair"] = (
                "运行 `webnovel.py ssot rebuild` 从事件文件重建 SQLite 镜像；"
                "若文件缺失，检查 git 是否回滚了 .story-system/events/"
            )
        return result

    def normalize_events(self, chapter: int, events: List[dict]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for event in events or []:
            if not isinstance(event, dict):
                continue
            payload = dict(event)
            payload["chapter"] = int(payload.get("chapter") or chapter)
            normalized.append(StoryEvent.model_validate(payload).model_dump())
        return normalized

    def _write_sqlite_mirror(self, events: List[Dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS story_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    chapter INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_story_events_chapter ON story_events(chapter)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_story_events_type ON story_events(event_type)"
            )
            conn.executemany(
                """
                INSERT OR IGNORE INTO story_events(event_id, chapter, event_type, subject, payload_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        event["event_id"],
                        int(event["chapter"]),
                        event["event_type"],
                        event["subject"],
                        json.dumps(event.get("payload") or {}, ensure_ascii=False),
                    )
                    for event in events
                ],
            )
            conn.commit()
