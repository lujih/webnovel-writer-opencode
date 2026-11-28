#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canon locks —— 作者显式钉死的设定断言（外部实践：NarraLume 的 locked facts）。

## 问题

accepted 章节的 state_deltas 会直接写进 SSOT。若某个设定已被作者"钉死"
（例如「主角境界在第 12 章之后恒为斗师」「某角色已死亡」），模型在后续章节里
把它改掉时，现有链路只有 LLM 语义审查兜着——它会漏、会误判，而且**没有任何
确定性防线**。外部产品（NarraLume）把这条作为硬约束：与 locked fact 冲突时
**暂停任务等作者裁决**，而不是让 AI 静默改 canon。

## 做法

- 锁定存成独立文件 `.story-system/canon_locks.json`，不进 `state.json`：
  后者有 rebuild 的非事件字段白名单问题（新增顶层字段必须登记，否则被擦除），
  而"作者的硬约束"不该依赖投影层的记忆。
- 提交时对 `accepted_events` 做**确定性**冲突检测：同一 (subject, field) 的
  新值与锁定值不同 → 冲突。
- 冲突即**拒章**（技术故障，与 `missed_nodes` 同级），不降级为 `review_flags`：
  lock 是作者显式声明、可机判、零误判空间，符合本项目的技术层 fail-closed 划分。

## 不做什么

- 不做跨字段推导（锁 `realm=斗师` 不会推断"不能飞行"）。那是设定校验器的事，
  混进来会让 lock 从"断言"变成"半部规则引擎"。
- 不自动锁定：锁只能由作者显式声明（CLI 或手工编辑）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from security_utils import atomic_write_json
    from project_locator import resolve_project_root
except ImportError:  # pragma: no cover - __main__ / sys.path 直跑
    from scripts.security_utils import atomic_write_json
    from scripts.project_locator import resolve_project_root

SCHEMA_VERSION = "canon-locks/v1"
LOCKS_REL = Path(".story-system") / "canon_locks.json"

#: 会被 lock 覆盖的事件类型与其状态字段来源。
#: 只认这两类：它们携带"某实体某字段的新值"，其余事件的载荷没有稳定字段语义。
STATEFUL_EVENT_TYPES = ("character_state_changed", "power_breakthrough")


class CanonLocksCorrupted(RuntimeError):
    """锁定文件存在但读不出来 —— 不允许用空集合覆盖作者的硬约束。"""


def locks_path(project_root: Path) -> Path:
    return Path(project_root) / LOCKS_REL


def lock_id(subject: str, field: str) -> str:
    """按 (subject, field) 生成稳定 id：一个字段只允许一条锁。"""
    raw = f"{subject}\x00{field}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:8]


def load_locks(project_root: Path, *, strict: bool = False) -> List[Dict[str, Any]]:
    """读取锁定列表。

    `strict=True` 供**写入路径**使用：文件存在但损坏时抛 `CanonLocksCorrupted`，
    不能拿一个空列表去覆盖——那等于作者锁的设定被一次读失败抹掉。
    读路径降级为空列表（面板/CLI 展示不需要为此失败）。
    """
    path = locks_path(project_root)
    if not path.is_file():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        if strict:
            raise CanonLocksCorrupted(
                f"canon_locks.json 无法解析，写入已中止：{path}（{exc}）。"
                "请先修复或移走该文件。"
            ) from exc
        return []
    if not isinstance(payload, dict):
        if strict:
            raise CanonLocksCorrupted(f"canon_locks.json 顶层不是对象：{path}")
        return []
    locks = payload.get("locks")
    if not isinstance(locks, list):
        return []
    return [item for item in locks if isinstance(item, dict) and item.get("subject") and item.get("field")]


def save_locks(project_root: Path, locks: Iterable[Dict[str, Any]]) -> Path:
    """原子写回；写前校验既有文件可解析（不覆盖坏文件）。"""
    load_locks(project_root, strict=True)
    path = locks_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "locks": sorted(
            (dict(item) for item in locks),
            key=lambda x: (str(x.get("subject")), str(x.get("field"))),
        ),
    }
    atomic_write_json(path, payload, use_lock=True, backup=True)
    return path


def add_lock(
    project_root: Path,
    subject: str,
    field: str,
    value: str,
    *,
    rationale: str = "",
    chapter: int = 0,
) -> Dict[str, Any]:
    """新增/更新一条锁。同一 (subject, field) 只保留一条，新值覆盖旧值。

    覆盖而不是报错：作者改主意时应当直接生效，而不是先删再加。
    返回值带 `created`（True = 新增，False = 覆盖）。
    """
    subject = str(subject or "").strip()
    field = str(field or "").strip()
    value = str(value or "").strip()
    if not subject or not field or not value:
        raise ValueError("subject / field / value 都不能为空")

    locks = load_locks(project_root, strict=True)
    lock = {
        "id": lock_id(subject, field),
        "subject": subject,
        "field": field,
        "value": value,
        "rationale": str(rationale or ""),
        "created_chapter": int(chapter or 0),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    created = True
    for i, existing in enumerate(locks):
        if existing.get("id") == lock["id"]:
            lock["created_chapter"] = int(existing.get("created_chapter") or chapter or 0)
            locks[i] = lock
            created = False
            break
    else:
        locks.append(lock)
    save_locks(project_root, locks)
    return {**lock, "created": created}


def remove_lock(project_root: Path, identifier: str) -> bool:
    """按 id 或 `subject.field` 删除。返回是否真的删掉了。"""
    key = str(identifier or "").strip()
    if not key:
        return False
    locks = load_locks(project_root, strict=True)
    kept = [
        item for item in locks
        if item.get("id") != key
        and f"{item.get('subject')}.{item.get('field')}" != key
        and str(item.get("subject")) != key
    ]
    if len(kept) == len(locks):
        return False
    save_locks(project_root, kept)
    return True


def _state_change_of(event: Dict[str, Any]) -> Optional[tuple[str, str, str]]:
    """从事件里抽出 (subject, field, new_value)；抽不出返回 None。"""
    event_type = str(event.get("event_type") or "")
    if event_type not in STATEFUL_EVENT_TYPES:
        return None
    payload = event.get("payload") or {}
    if not isinstance(payload, dict):
        return None
    subject = str(event.get("subject") or payload.get("subject") or "").strip()
    field = str(payload.get("field") or payload.get("field_path") or "").strip()
    if event_type == "power_breakthrough" and not field:
        field = "realm"
    new_value = (
        payload.get("new")
        or payload.get("new_value")
        or payload.get("new_state")
        or payload.get("new_realm")
    )
    if not subject or not field or new_value is None:
        return None
    return subject, field, str(new_value).strip()


def detect_conflicts(
    locks: Iterable[Dict[str, Any]],
    events: Iterable[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """找出「事件里的新值与锁定值不一致」的情况。

    只比较**字符串完全相等**（去首尾空白）。不做模糊匹配：
    「斗师」与「斗师巅峰」是两个不同的值，是否等价应由作者决定，不能由
    字符串编辑距离替他决定。
    """
    index: Dict[tuple[str, str], Dict[str, Any]] = {}
    for lock in locks:
        subject = str(lock.get("subject") or "").strip()
        field = str(lock.get("field") or "").strip()
        if subject and field:
            index[(subject, field)] = lock

    conflicts: List[Dict[str, Any]] = []
    for event in events:
        parsed = _state_change_of(event)
        if not parsed:
            continue
        subject, field, new_value = parsed
        lock = index.get((subject, field))
        if not lock:
            continue
        locked_value = str(lock.get("value") or "").strip()
        if new_value == locked_value:
            continue
        conflicts.append({
            "lock_id": str(lock.get("id") or lock_id(subject, field)),
            "subject": subject,
            "field": field,
            "locked_value": locked_value,
            "new_value": new_value,
            "event_type": str(event.get("event_type") or ""),
            "rationale": str(lock.get("rationale") or ""),
        })
    return conflicts


def format_conflicts(conflicts: List[Dict[str, Any]]) -> str:
    """给人看的一行摘要（CLI 与 commit 拒绝理由共用）。"""
    if not conflicts:
        return ""
    parts = [
        f"{c['subject']}.{c['field']}: 锁定「{c['locked_value']}」→ 事件「{c['new_value']}」"
        for c in conflicts[:5]
    ]
    suffix = f"（共 {len(conflicts)} 处，此处列前 5）" if len(conflicts) > 5 else ""
    return "；".join(parts) + suffix
