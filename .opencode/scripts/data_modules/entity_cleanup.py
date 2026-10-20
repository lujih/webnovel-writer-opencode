"""Detect and mark dirty entities in index.db — pinyin, snake_case, and other
non-CJK entity IDs that LLMs occasionally produce during extraction.

An entity is considered "clean" if its ID:
  - contains at least one CJK character (Chinese/Japanese/Korean unified ideograph)
  - uses standard separators: underscores, hyphens, or forward slashes between CJK parts

Dirty entity examples: "xiao_yan", "XiaoYan", "fire_spirit", "main_character"
Clean entity examples: "萧炎", "主角·萧炎", "火焰精灵"

Operation:
  --mark-invalid  Write dirty entities to invalid_facts table for manual review
"""

import sqlite3
import re
import sys
from pathlib import Path

_CJK_RANGE = re.compile(r'[一-鿿㐀-䶿豈-﫿]')


def _is_dirty(entity_id: str) -> bool:
    """An entity ID is dirty if it has no CJK characters."""
    if not entity_id or not isinstance(entity_id, str):
        return True
    return not bool(_CJK_RANGE.search(entity_id))


def _scan_dirty_entities(db_path: Path) -> list[dict]:
    """Return list of dirty entities from index.db entities table."""
    if not db_path.is_file():
        print(f"ERROR: index.db not found at {db_path}", file=sys.stderr)
        return []

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, entity_id, entity_type, entity_name, chapter_first_seen "
            "FROM entities ORDER BY chapter_first_seen"
        ).fetchall()

        dirty = []
        for row in rows:
            eid = row["entity_id"] or row["id"]
            if _is_dirty(eid):
                dirty.append({
                    "entity_id": eid,
                    "entity_type": row["entity_type"],
                    "entity_name": row["entity_name"],
                    "chapter_first_seen": row["chapter_first_seen"],
                })
        return dirty
    finally:
        conn.close()


def _mark_invalid(db_path: Path, entities: list[dict]) -> int:
    """Write dirty entities to invalid_facts table. Returns count written."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invalid_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_id TEXT,
                entity_type TEXT,
                entity_name TEXT,
                chapter INTEGER,
                reason TEXT,
                marked_at TEXT DEFAULT (datetime('now')),
                resolved INTEGER DEFAULT 0
            )
        """)
        count = 0
        for e in entities:
            eid = e["entity_id"]
            # 幂等：已存在未处理的同一条就不重复插入。
            #
            # 此前无条件 INSERT，连跑三次会让 invalid_facts 变成 1→2→3 条，
            # 人工复核时看到多条一模一样的记录，无从判断是真问题还是重复标记。
            existing = conn.execute(
                "SELECT 1 FROM invalid_facts WHERE entity_id = ? AND resolved = 0",
                (eid,),
            ).fetchone()
            if existing:
                continue
            # 已 resolved 的也不再标记：用户已经处理过这条，重新插入未处理记录
            # 等于推翻他的复核结论。
            resolved = conn.execute(
                "SELECT 1 FROM invalid_facts WHERE entity_id = ? AND resolved = 1",
                (eid,),
            ).fetchone()
            if resolved:
                continue
            conn.execute(
                "INSERT INTO invalid_facts (entity_id, entity_type, entity_name, chapter, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (eid, e["entity_type"], e["entity_name"],
                 e["chapter_first_seen"], "dirty_entity_id: non-CJK")
            )
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def cmd_entity_clean(args) -> int:
    project_root = Path(args.project_root).expanduser().resolve()
    db_path = project_root / ".webnovel" / "index.db"

    # 「index.db 不存在」与「数据库里没有脏实体」是两件事。前者说明项目还没
    # 建索引或路径不对，直接返回 0 会让脚本链误以为检查通过。
    if not db_path.is_file():
        print(
            f"ERROR: 未找到 index.db（{db_path}）。\n"
            f"   entity-clean 需要索引库才能检查实体 ID。\n"
            f"   修复：先运行 `webnovel.py index rebuild` 建立索引，"
            f"或用 --project-root 指定正确的书项目根目录。",
            file=sys.stderr,
        )
        return 1

    dirty = _scan_dirty_entities(db_path)
    if not dirty:
        print("No dirty entities found.")
        return 0

    print(f"Found {len(dirty)} dirty entities:")
    for e in dirty:
        print(f"  [{e['entity_type']}] {e['entity_id']} → {e['entity_name']} (ch{e['chapter_first_seen']})")

    if args.mark_invalid:
        count = _mark_invalid(db_path, dirty)
        print(f"\nMarked {count} entities in invalid_facts. Review manually, then re-extract affected chapters.")
    else:
        print("\nDry run. Use --mark-invalid to flag for review.")

    return 0


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Detect and mark non-CJK entity IDs in index.db")
    ap.add_argument("--project-root", required=True, help="Book project root")
    ap.add_argument("--mark-invalid", action="store_true", help="Write dirty entities to invalid_facts table")
    args = ap.parse_args()
    raise SystemExit(cmd_entity_clean(args))
