#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scratchpad 持久化与查询。
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..config import DataModulesConfig, get_config
from ..cli_output import print_error, print_success
from ..cli_args import normalize_global_project_root, load_json_arg

from .schema import (
    BUCKET_TO_CATEGORY,
    CATEGORY_KEY_RULES,
    CATEGORY_TO_BUCKET,
    MemoryItem,
    ScratchpadData,
    memory_item_key,
    now_iso,
)

try:
    from security_utils import atomic_write_json
except ImportError:  # pragma: no cover
    from scripts.security_utils import atomic_write_json

from filelock import FileLock


class MemoryStoreCorrupted(RuntimeError):
    """scratchpad 存在但读不出来（坏 JSON / 不是对象 / 权限）。

    修复前：``read_json_safe(path, default={})`` 把「读坏」当成 `{}`，
    `load()` 返回空结构，随后 `upsert_item` / `mark_status` 在锁内把这条
    空结构写回去 —— **一次读坏就清空整库长期记忆**；而且
    ``atomic_write_json(..., backup=True)`` 在覆盖前把那份损坏文件拷成
    ``.bak``，回滚通道同时失效。

    现在的约定：读路径可以降级（页面显示为空 + 打印告警），
    **写路径必须 strict** —— 读不出就中止，不允许用空库覆盖真数据。
    """


class ScratchpadManager:
    def __init__(self, config: DataModulesConfig | None = None):
        self.config = config or get_config()
        self.path = Path(self.config.scratchpad_file)
        self._lock = FileLock(str(self.path) + ".lock", timeout=30)

    def load(self, *, strict: bool = False) -> ScratchpadData:
        """读取 scratchpad。

        ``strict=True`` 供**写路径**使用：文件存在但解析失败时抛
        `MemoryStoreCorrupted`，绝不让调用方拿到一个"空库"去覆盖真数据。
        默认（读路径）降级为空结构，但**打印告警**——静默把损坏当空库
        是这条数据丢失链的起点。

        ``utf-8-sig`` 而非 ``utf-8``：BOM 会让 `json.loads` 失败，把
        「有内容」误判成「空库」。
        """
        if not self.path.exists():
            return ScratchpadData.empty()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            if strict:
                raise MemoryStoreCorrupted(
                    f"长期记忆文件无法解析，写入已中止：{self.path}（{exc}）。"
                    "继续写会把整库覆盖成空；请先修复该文件或从 .bak 恢复。"
                ) from exc
            print_error(f"长期记忆文件无法解析，已按空库处理：{self.path}（{exc}）")
            return ScratchpadData.empty()
        if not isinstance(payload, dict):
            if strict:
                raise MemoryStoreCorrupted(
                    f"长期记忆文件顶层不是对象，写入已中止：{self.path}"
                    f"（实际类型 {type(payload).__name__}）"
                )
            print_error(f"长期记忆文件顶层不是对象，已按空库处理：{self.path}")
            return ScratchpadData.empty()
        return ScratchpadData.from_dict(payload)

    def _assert_writable(self) -> None:
        """覆盖前确认既有文件可解析——直接调 `save()` 的路径同样不许毁库。"""
        if self.path.exists():
            self.load(strict=True)

    def save(self, data: ScratchpadData, _use_lock: bool = True) -> None:
        self.config.ensure_dirs()
        self._assert_writable()
        if bool(getattr(self.config, "memory_compactor_enabled", True)):
            threshold = max(1, int(getattr(self.config, "memory_compactor_threshold", 500)))
            if data.count_items() > threshold:
                from .compactor import compact_scratchpad

                data = compact_scratchpad(data, max_items=threshold)
        payload = data.to_dict()
        payload.setdefault("meta", {})
        payload["meta"]["last_updated"] = now_iso()
        payload["meta"]["total_items"] = data.count_items()
        atomic_write_json(self.path, payload, use_lock=_use_lock, backup=True)

    def _key_for(self, item: MemoryItem) -> tuple[Any, ...]:
        return memory_item_key(item)

    def upsert_item(self, item: MemoryItem) -> Dict[str, int]:
        normalized = item.normalized()
        with self._lock:
            data = self.load(strict=True)
            bucket = CATEGORY_TO_BUCKET[normalized.category]
            rows: List[MemoryItem] = list(getattr(data, bucket))
            target_key = self._key_for(normalized)

            outdated = 0
            replaced_existing = False
            new_rows: List[MemoryItem] = []
            for row in rows:
                row_key = self._key_for(row)
                if row_key == target_key and row.id != normalized.id:
                    if row.status == normalized.status:
                        # 同 key 同 status 旧值丢弃
                        outdated += 1
                        replaced_existing = True
                        continue
                    # 不同 status 的同 key 项保留（如 outdated vs active）
                    new_rows.append(row)
                    continue
                elif row.id == normalized.id:
                    replaced_existing = True
                    continue
                new_rows.append(row)

            normalized.updated_at = normalized.updated_at or now_iso()
            new_rows.append(normalized)
            setattr(data, bucket, new_rows)
            self.save(data, _use_lock=False)

        return {
            "added": 0 if replaced_existing else 1,
            "updated": 1 if replaced_existing else 0,
            "outdated": outdated,
        }

    def mark_status(self, item_id: str, status: str) -> bool:
        if not item_id:
            return False
        with self._lock:
            data = self.load(strict=True)
            updated = False
            for bucket in BUCKET_TO_CATEGORY:
                rows: List[MemoryItem] = getattr(data, bucket)
                for i, row in enumerate(rows):
                    if row.id == item_id:
                        rows[i] = MemoryItem(**{**asdict(row), "status": status, "updated_at": now_iso()})
                        updated = True
            if updated:
                self.save(data, _use_lock=False)
        return updated

    def query(
        self,
        category: Optional[str] = None,
        subject: Optional[str] = None,
        status: Optional[str] = "active",
    ) -> List[MemoryItem]:
        data = self.load()
        categories = [category] if category else list(CATEGORY_TO_BUCKET.keys())
        result: List[MemoryItem] = []
        for cat in categories:
            bucket = CATEGORY_TO_BUCKET.get(cat)
            if not bucket:
                continue
            rows: List[MemoryItem] = getattr(data, bucket)
            for row in rows:
                if subject and row.subject != subject:
                    continue
                if status and row.status != status:
                    continue
                result.append(row)
        return result

    def stats(self) -> Dict[str, Any]:
        data = self.load()
        by_category: Dict[str, int] = {}
        active = 0
        outdated = 0
        contradicted = 0
        tentative = 0
        for category, bucket in CATEGORY_TO_BUCKET.items():
            rows: List[MemoryItem] = getattr(data, bucket)
            by_category[category] = len(rows)
            for row in rows:
                if row.status == "active":
                    active += 1
                elif row.status == "outdated":
                    outdated += 1
                elif row.status == "contradicted":
                    contradicted += 1
                elif row.status == "tentative":
                    tentative += 1
        return {
            "total": data.count_items(),
            "active": active,
            "outdated": outdated,
            "contradicted": contradicted,
            "tentative": tentative,
            "by_category": by_category,
            "path": str(self.path),
        }

    def dump(self) -> Dict[str, Any]:
        return self.load().to_dict()

    def conflicts(self) -> List[Dict[str, Any]]:
        data = self.load()
        conflicts: List[Dict[str, Any]] = []
        for category, bucket in CATEGORY_TO_BUCKET.items():
            key_count: Dict[tuple[Any, ...], int] = {}
            rows: List[MemoryItem] = getattr(data, bucket)
            for row in rows:
                if row.status != "active":
                    continue
                key = self._key_for(row)
                key_count[key] = key_count.get(key, 0) + 1
            for key, cnt in key_count.items():
                if cnt > 1:
                    conflicts.append({"category": category, "key": list(key), "active_items": cnt})
        return conflicts


def main() -> None:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Memory Scratchpad CLI")
    parser.add_argument("--project-root", type=str, help="项目根目录")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("stats")
    p_query = sub.add_parser("query")
    p_query.add_argument("--category", type=str, default=None)
    p_query.add_argument("--subject", type=str, default=None)
    p_query.add_argument("--status", type=str, default="active")
    sub.add_parser("dump")
    sub.add_parser("conflicts")

    p_update = sub.add_parser("update")
    p_update.add_argument("--chapter", type=int, required=True)
    p_update.add_argument("--data", required=True, help="章节结构化结果 JSON")

    sub.add_parser("bootstrap")

    args = parser.parse_args(normalize_global_project_root(sys.argv[1:]))

    config = None
    if args.project_root:
        from project_locator import resolve_project_root

        resolved_root = resolve_project_root(args.project_root)
        config = DataModulesConfig.from_project_root(resolved_root)

    manager = ScratchpadManager(config)

    if args.command == "stats":
        print_success(manager.stats(), message="memory_stats")
        return
    if args.command == "dump":
        print_success(manager.dump(), message="memory_dump")
        return
    if args.command == "conflicts":
        print_success(manager.conflicts(), message="memory_conflicts")
        return
    if args.command == "query":
        rows = [row.to_dict() for row in manager.query(args.category, args.subject, args.status)]
        print_success(rows, message="memory_query")
        return
    if args.command == "update":
        from .writer import MemoryWriter

        payload = load_json_arg(args.data)
        writer = MemoryWriter(config or get_config())
        result = writer.update_from_chapter_result(args.chapter, payload)
        print_success(result, message="memory_updated")
        return
    if args.command == "bootstrap":
        from .bootstrap import bootstrap_from_index

        result = bootstrap_from_index(config or get_config())
        print_success(result, message="memory_bootstrapped")
        return

    print_error("UNKNOWN_COMMAND", "未知命令", suggestion="请查看 --help")
