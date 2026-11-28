#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""章节状态变更的可读审计（G3 的"可见性"第一层）。

## 为什么需要

外部实践（NarraLume）：**AI 的建议不是已保存的决定**——故事变更（角色状态、
关系、时间线）应当先作为候选被作者看见，再由作者决定是否入 canon。

本项目当前是「accepted 章节的 state_deltas 直接投影进 state.json」，中间没有
可读的中间态：作者想知道"这一章到底改了什么"只能翻 commit JSON。本模块把这件事
变成一条命令：

    webnovel.py changes list --chapter 12      # 这一章改了哪些字段
    webnovel.py changes diff --from 10 --to 12 # 两章之间的字段差异

## 本模块**不**做什么

不做 accept/reject。真正的候选闸门要改提交链（state_deltas 不直接投影），
属于架构级改动；先把"改了什么"变成可见、可审计，是它的前置条件。这一点写清楚，
免得下一个人以为"有 changes 命令就等于有候选机制"。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from .commit_artifacts import extraction_list
except ImportError:  # pragma: no cover - __main__ / sys.path 直跑
    from commit_artifacts import extraction_list


def commit_path(project_root: str | Path, chapter: int) -> Optional[Path]:
    """定位章节 commit 文件（兼容 3 位与 4 位编号）。"""
    base = Path(project_root) / ".story-system" / "commits"
    for name in (f"chapter_{int(chapter):03d}.commit.json", f"chapter_{int(chapter):04d}.commit.json"):
        path = base / name
        if path.is_file():
            return path
    return None


def load_commit(project_root: str | Path, chapter: int) -> Optional[Dict[str, Any]]:
    path = commit_path(project_root, chapter)
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _stringify(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


#: 终端输出的单值上限。真书验证发现的：`status` 这类字段的值常是一整段叙述
#: （「受伤（右肩与上臂间能量刃切口、左前臂尺骨被能量刃切中…）」），
#: 不截断时一条变更就占满整屏，36 项的高危清单根本扫不动。
#: JSON 输出**不截断**——机器读的场合需要完整值。
VALUE_MAX_CHARS = 60


def _clip(value: Any, limit: int = VALUE_MAX_CHARS) -> str:
    text = _stringify(value)
    return text if len(text) <= limit else text[:limit] + "…"


def collect_changes(commit_payload: Dict[str, Any] | None) -> List[Dict[str, Any]]:
    """把 commit 里的状态变更归一成 `{entity_id, field, old, new, source}`。

    规则与 `state_projection_writer._collect_state_deltas()` **一致**（同一份
    payload 必须得出同一组字段，否则"审计看到"与"实际投影"会分叉）：
    1. 先取 `state_deltas`（已归一的数据）；
    2. 再用 `accepted_events` 里的 `power_breakthrough` / `character_state_changed`
       补充（settler 只产事件、不产 delta 的情况）；
    3. 同一 `(entity_id, field)` 只保留第一条。
    """
    payload = commit_payload if isinstance(commit_payload, dict) else {}
    rows: List[Dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def _add(entity_id: str, field: str, old: Any, new: Any, source: str) -> None:
        key = (entity_id, field)
        if not entity_id or not field or key in seen:
            return
        seen.add(key)
        rows.append({
            "entity_id": entity_id,
            "field": field,
            "old": old,
            "new": new,
            "source": source,
        })

    for delta in extraction_list(payload, "state_deltas"):
        if not isinstance(delta, dict):
            continue
        _add(
            str(delta.get("entity_id") or "").strip(),
            str(delta.get("field") or "").strip(),
            delta.get("old", delta.get("old_value")),
            delta.get("new", delta.get("new_value")),
            "state_deltas",
        )

    for event in extraction_list(payload, "accepted_events"):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "").strip()
        event_payload = dict(event.get("payload") or {})
        entity_id = str(event_payload.get("entity_id") or event.get("subject") or "").strip()
        if event_type == "power_breakthrough":
            field = str(event_payload.get("field") or event_payload.get("field_path") or "realm").strip()
            new = event_payload.get("new", event_payload.get("new_realm"))
            old = event_payload.get("old", event_payload.get("old_realm"))
        elif event_type == "character_state_changed":
            field = str(event_payload.get("field") or event_payload.get("field_path") or "").strip()
            # 回退链必须与 `state_projection_writer._collect_state_deltas()` 一致，
            # 包括 `to` / `from` 这两个简写（settler 与 LLM 都会用到）。
            new = (
                event_payload.get("new")
                if "new" in event_payload
                else event_payload.get("to")
                if "to" in event_payload
                else event_payload.get("new_value")
                if "new_value" in event_payload
                else event_payload.get("new_state")
            )
            old = (
                event_payload.get("old")
                if "old" in event_payload
                else event_payload.get("from")
                if "from" in event_payload
                else event_payload.get("old_value")
                if "old_value" in event_payload
                else event_payload.get("previous_state")
            )
        else:
            continue
        _add(entity_id, field, old, new, f"event:{event_type}")

    return rows


#: 变更的「爆炸半径」。不是所有字段改错的代价一样大：境界写错会让后面
#: 三十章的战斗力全部失据，而发色写错只是读者皱一下眉。分级依据是本项目
#: 的领域结构（力量体系 / 生死 / 关系 / 位置），不是通用规则。
HIGH_RISK_FIELDS = frozenset({
    "realm", "cultivation", "power_level", "rank",
    "status", "alive", "life_status", "death", "is_dead",
})
MEDIUM_RISK_PREFIXES = ("location", "relationship", "relation", "affiliation", "faction")


def risk_of(field: str) -> str:
    """字段的变更风险：`high` / `medium` / `low`。"""
    name = str(field or "").strip().lower()
    if not name:
        return "low"
    if name in HIGH_RISK_FIELDS or name.split(".")[-1] in HIGH_RISK_FIELDS:
        return "high"
    if any(name.startswith(prefix) for prefix in MEDIUM_RISK_PREFIXES):
        return "medium"
    return "low"


def annotate_changes(
    rows: List[Dict[str, Any]],
    locks: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """给每条变更标注 `risk`，并在与 canon lock 冲突时标 `locked_value`。

    `locked_value` 是**回溯审计**的关键：lock 通常是事后才钉的，历史章节里
    可能已经存在违反它的记录。写时拦截（`build_commit`）只能管以后，
    这个标注能回答"过去的章节里有没有已经违背作者硬约束的地方"。
    """
    by_key = {
        (str(lock.get("subject") or ""), str(lock.get("field") or "")): lock
        for lock in (locks or [])
        if isinstance(lock, dict)
    }
    annotated: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["risk"] = risk_of(str(item.get("field") or ""))
        lock = by_key.get((str(item.get("entity_id") or ""), str(item.get("field") or "")))
        if lock is not None:
            locked = str(lock.get("value") or "").strip()
            if locked and locked != str(item.get("new") or "").strip():
                item["locked_value"] = lock.get("value")
        annotated.append(item)
    return annotated


def latest_chapter(project_root: str | Path) -> int:
    """commit 目录里最大的章号（没有则 0）。"""
    base = Path(project_root) / ".story-system" / "commits"
    if not base.is_dir():
        return 0
    newest = 0
    for path in base.glob("chapter_*.commit.json"):
        digits = "".join(ch for ch in path.name.split(".")[0] if ch.isdigit())
        if digits:
            newest = max(newest, int(digits))
    return newest


def audit_changes(
    project_root: str | Path,
    *,
    limit: int = 20,
    locks: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """回溯审计最近 `limit` 章：只返回值得人看一眼的变更。

    纳入两类：`risk == "high"`（改错代价大）与带 `locked_value`（违背硬约束）。
    其余（发色、口头禅）不进报告——全量清单会把关键项淹掉。

    **同一变更跨章出现会被合并**（`occurrences` / `chapters`）。真书验证时
    看到「第 020 章 与 第 024 章 出现完全相同的一条 status 变更」——那通常
    意味着投影重复应用了同一份 delta（或作者重复提交），比单条更值得看。
    """
    newest = latest_chapter(project_root)
    if newest <= 0:
        return []
    start = max(1, newest - max(1, int(limit or 20)) + 1)
    merged: Dict[tuple, Dict[str, Any]] = {}
    for chapter in range(start, newest + 1):
        rows = annotate_changes(collect_changes(load_commit(project_root, chapter)), locks)
        for row in rows:
            if not (row.get("risk") == "high" or "locked_value" in row):
                continue
            key = (
                str(row.get("entity_id") or ""),
                str(row.get("field") or ""),
                str(row.get("old")),
                str(row.get("new")),
            )
            if key in merged:
                merged[key]["occurrences"] += 1
                merged[key]["chapters"].append(chapter)
                continue
            merged[key] = {**row, "chapter": chapter, "occurrences": 1, "chapters": [chapter]}
    return sorted(merged.values(), key=lambda item: (item["chapter"], item["entity_id"], item["field"]))


def trace_changes(
    project_root: str | Path,
    entity: str,
    field: str,
    *,
    since_chapter: int = 0,
    locks: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """某个 `(entity, field)` 的**跨章变更时间线**（新 → 旧按章升序）。

    为什么需要：`canon lock` 的价值取决于「锁哪个字段」，而这个判断要基于事实——
    该字段改过几次、有没有**值回退**（A→B 之后又回到 A）、有没有在锁定值之外漂移。
    没有时间线，作者只能凭印象决定锁什么，而凭印象挑的字段往往正是从没出过问题的。

    只读、不判定。是否回退、是否该锁，由作者看时间线自己决定。
    """
    newest = latest_chapter(project_root)
    if newest <= 0:
        return []
    start = max(1, int(since_chapter or 0))
    rows: List[Dict[str, Any]] = []
    for chapter in range(start, newest + 1):
        changes = annotate_changes(
            collect_changes(load_commit(project_root, chapter)), locks,
        )
        for row in changes:
            if str(row.get("entity_id")) != str(entity) or str(row.get("field")) != str(field):
                continue
            rows.append({**row, "chapter": chapter})
    return rows


def detect_value_regressions(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """在时间线里找**值回退**：某章的新值等于该字段更早出现过的值。

    回退本身不是错误（剧情可能真的让状态复原），但它是「什么值得锁」的最强信号：
    状态机式字段（剩余次数、境界、存活）回退几乎总是 bug；情绪/位置类字段回退则正常。

    **`old == new` 的行不算回退**——那是"无变化的空变更"。真书验证时
    `chip.remaining_uses` 有 5 条 `29 → 29` / `25 → 25`，它们既不该被当成回退，
    本身也是值得单独标注的数据质量问题（见 `format_trace` 的 `≈无变化` 标记）。
    """
    seen: Dict[str, int] = {}
    regressions: List[Dict[str, Any]] = []
    for row in rows:
        old_value = _stringify(row.get("old"))
        new_value = _stringify(row.get("new"))
        if old_value == new_value:
            continue
        if new_value in seen:
            regressions.append({
                "chapter": row.get("chapter"),
                "value": row.get("new"),
                "previously_at": seen[new_value],
            })
        else:
            seen[new_value] = int(row.get("chapter") or 0)
    return regressions


def format_trace(
    rows: List[Dict[str, Any]],
    entity: str,
    field: str,
    *,
    regressions: List[Dict[str, Any]] | None = None,
) -> str:
    if not rows:
        return f"{entity}.{field}：没有找到任何变更记录。"
    lines = [f"{entity}.{field} 的变更时间线（{len(rows)} 次，按章升序）："]
    regressed = {r["chapter"] for r in (regressions or [])}
    noop_count = 0
    for row in rows:
        old_text, new_text = _clip(row.get("old")), _clip(row.get("new"))
        marks = []
        if row.get("chapter") in regressed:
            marks.append("↺回退")
        if _stringify(row.get("old")) == _stringify(row.get("new")):
            marks.append("≈无变化")
            noop_count += 1
        if "locked_value" in row:
            marks.append(f"🔒锁定={_clip(row['locked_value'])}")
        suffix = f"  {'/'.join(marks)}" if marks else ""
        lines.append(f"  第{row['chapter']:03d}章 {old_text} → {new_text}{suffix}")
    if regressions:
        lines.append(f"  发现 {len(regressions)} 处值回退（新值等于更早出现过的值）")
    if noop_count:
        lines.append(
            f"  另有 {noop_count} 条「无变化的空变更」（old == new）——"
            f"投影把未变的字段也记成了 delta，属数据质量噪声"
        )
    return "\n".join(lines)


def list_lock_candidates(
    project_root: str | Path,
    *,
    since_chapter: int = 0,
    min_changes: int = 2,
    locks: List[Dict[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    """扫描全书，列出「值得考虑交给 canon lock 钉住」的字段。

    判断依据只有三条**客观**统计，不做语义推断：

    - `changes`：改过几次（只改过一次的字段没什么可锁的）；
    - `regressions`：值回退几次——状态机式字段（境界/剩余次数/存活）回退几乎总是 bug；
    - `noops`：`old == new` 的空变更几次——那是投影噪声，说明该字段被反复重复记录。

    筛选条件：`risk_level == "high"` 且 `changes >= min_changes`。low 字段回退是剧情
    正常波动（情绪、位置），列进来只会稀释清单。

    **只给候选，不写 lock。** 锁哪个由作者决定——`add_lock` 会参与提交门禁，
    误锁的代价是章节被无理由拒绝。
    """
    newest = latest_chapter(project_root)
    if newest <= 0:
        return []
    start = max(1, int(since_chapter or 0))
    grouped: Dict[tuple, Dict[str, Any]] = {}
    for chapter in range(start, newest + 1):
        changes = annotate_changes(
            collect_changes(load_commit(project_root, chapter)), locks,
        )
        for row in changes:
            entity = str(row.get("entity_id") or "")
            field = str(row.get("field") or "")
            if not entity or not field:
                continue
            key = (entity, field)
            bucket = grouped.setdefault(key, {
                "entity": entity,
                "field": field,
                "risk_level": row.get("risk"),
                "changes": 0,
                "regressions": 0,
                "noops": 0,
                "first_chapter": chapter,
                "last_chapter": chapter,
                "latest_value": None,
                "locked_value": row.get("locked_value"),
                "_seen": {},
            })
            bucket["changes"] += 1
            bucket["last_chapter"] = chapter
            # **空值不覆盖已有最新值**。真书验证发现：`chen_sheng.realm` 的最后一条
            # 是 `— → —`（old/new 都缺失的空 delta），若无条件取最后一行，
            # 「最新值」会显示成 `—`，看起来像"境界变成空了"——纯误导。
            if _stringify(row.get("new")) not in ("—", ""):
                bucket["latest_value"] = row.get("new")
            old_value = _stringify(row.get("old"))
            new_value = _stringify(row.get("new"))
            if old_value == new_value:
                bucket["noops"] += 1
            elif new_value in bucket["_seen"]:
                bucket["regressions"] += 1
            else:
                bucket["_seen"][new_value] = chapter

    candidates = []
    for bucket in grouped.values():
        if bucket["risk_level"] != "high" or bucket["changes"] < max(1, int(min_changes)):
            continue
        item = {k: v for k, v in bucket.items() if k != "_seen"}
        item["already_locked"] = item.get("locked_value") is not None
        candidates.append(item)
    return sorted(
        candidates,
        key=lambda c: (-c["regressions"], -c["changes"], c["entity"], c["field"]),
    )


def format_candidates(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "没有符合条件的候选字段（高风险 + 变更 ≥ 3 次）。"
    lines = [
        f"值得考虑锁定的字段（{len(rows)} 个）：",
        "  判断依据只有客观统计：变更次数 / 值回退次数 / 空变更次数。",
        "",
    ]
    for row in rows:
        flags = []
        if row["regressions"]:
            flags.append(f"↺回退×{row['regressions']}")
        if row["noops"]:
            flags.append(f"≈空变更×{row['noops']}")
        if row.get("already_locked"):
            flags.append("🔒已锁")
        lines.append(
            f"  {row['entity']}.{row['field']}（第{row['first_chapter']}–{row['last_chapter']}章，"
            f"改 {row['changes']} 次{'，' + '，'.join(flags) if flags else ''}）"
        )
        lines.append(f"    最新值：{_clip(row.get('latest_value'))}")
    lines.append("")
    lines.append("锁定命令：webnovel.py canon lock --subject <实体> --field <字段> --value <值>")
    return "\n".join(lines)


def preview_payload(
    payload: Dict[str, Any] | None,
    *,
    locks: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    """对一份**还没提交**的 payload 预演它将产生的变更。

    这是 G3「候选闸门」的**不阻断版本**：闸门要在提交链上插一道人工确认，
    会打断 `orchestrate write "1-50"` 的批量流水线；而"事前可见"不需要拦，
    只需要让作者在 commit 之前知道「这次会改 N 项，其中 M 项高危、有没有撞锁」。

    输入可以是 `extraction_result.json`（`state_deltas` 在顶层）也可以是
    commit payload（在 `extraction_result.state_deltas` 里）——`collect_changes`
    用的是 `extraction_list()`，两种形状都认。
    """
    rows = annotate_changes(collect_changes(payload), locks)
    high = [row for row in rows if row.get("risk") == "high"]
    medium = [row for row in rows if row.get("risk") == "medium"]
    conflicts = [row for row in rows if "locked_value" in row]
    return {
        "changes": rows,
        "count": len(rows),
        "high_risk_count": len(high),
        "medium_risk_count": len(medium),
        "lock_conflicts": conflicts,
        "would_be_rejected": bool(conflicts),
    }


def format_preview(report: Dict[str, Any]) -> str:
    total = report.get("count") or 0
    if not total:
        return "这份 payload 不产生任何状态变更。"
    high = report.get("high_risk_count") or 0
    medium = report.get("medium_risk_count") or 0
    lines = [
        f"预演：这份 payload 将产生 {total} 项状态变更"
        f"（高危 {high}、中危 {medium}）。"
    ]
    if report.get("would_be_rejected"):
        lines.append("")
        lines.append("⚠️ 会因违背 canon lock 被拒章：")
        for row in report["lock_conflicts"]:
            lines.append(
                f"  {row['entity_id']}.{row['field']}: "
                f"{_clip(row.get('new'))} ≠ 锁定值 {_clip(row['locked_value'])}"
            )
    highs = [row for row in report.get("changes") or [] if row.get("risk") == "high"]
    if highs:
        lines.append("")
        lines.append("高危变更（改错代价大）：")
        for row in highs:
            lines.append(
                f"  {row['entity_id']}.{row['field']}: "
                f"{_clip(row.get('old'))} → {_clip(row.get('new'))}"
            )
    if not report.get("would_be_rejected") and not highs:
        lines.append("没有高危变更，也没有撞锁。")
    return "\n".join(lines)


def format_audit(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "最近区间没有高危或违背锁定的状态变更。"
    lines = [f"需要人看一眼的变更（{len(rows)} 项）："]
    for row in rows:
        marks = []
        if row.get("risk") == "high":
            marks.append("⚠️高危")
        if "locked_value" in row:
            marks.append(f"🔒违背锁定（锁定值 {_clip(row['locked_value'])}）")
        if (row.get("occurrences") or 1) > 1:
            chapters = ",".join(str(c) for c in row.get("chapters") or [])
            marks.append(f"↻重复 {row['occurrences']} 次（第 {chapters} 章）")
        lines.append(
            f"  第{row['chapter']:03d}章 {row['entity_id']}.{row['field']}: "
            f"{_clip(row.get('old'))} → {_clip(row.get('new'))}"
            f"  [{'/'.join(marks)}]"
        )
    return "\n".join(lines)


def diff_changes(
    earlier: List[Dict[str, Any]],
    later: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """两次变更集的差异：`changed` / `added` / `removed`。

    `changed` 指同一 `(entity_id, field)` 的新值不同；`added` 指后者新增的键；
    `removed` 指前者有、后者没有的键（字段被"遗忘"——通常意味着投影漏了一条）。
    """
    left = {(r["entity_id"], r["field"]): r for r in earlier}
    right = {(r["entity_id"], r["field"]): r for r in later}

    changed: List[Dict[str, Any]] = []
    for key in sorted(set(left) & set(right)):
        before, after = left[key], right[key]
        if _stringify(before.get("new")) != _stringify(after.get("new")):
            changed.append({
                "entity_id": key[0], "field": key[1],
                "from": before.get("new"), "to": after.get("new"),
            })
    added = [right[k] for k in sorted(set(right) - set(left))]
    removed = [left[k] for k in sorted(set(left) - set(right))]
    return {"changed": changed, "added": added, "removed": removed}


def format_changes(rows: List[Dict[str, Any]], chapter: int | None = None) -> str:
    if not rows:
        return f"第 {chapter} 章没有状态变更。" if chapter else "没有状态变更。"
    lines = [f"第 {chapter} 章的状态变更（{len(rows)} 项）：" if chapter else f"状态变更（{len(rows)} 项）："]
    for row in rows:
        marks = ""
        if row.get("risk") == "high":
            marks += " ⚠️"
        elif row.get("risk") == "medium":
            marks += " ·"
        if "locked_value" in row:
            marks += " 🔒"
        lines.append(
            f"  {row['entity_id']}.{row['field']}: "
            f"{_clip(row.get('old'))} → {_clip(row.get('new'))}"
            f"  [{row.get('source')}]{marks}"
        )
    return "\n".join(lines)


def format_diff(diff: Dict[str, List[Dict[str, Any]]]) -> str:
    lines: List[str] = []
    if diff.get("changed"):
        lines.append("变化：")
        for row in diff["changed"]:
            lines.append(f"  {row['entity_id']}.{row['field']}: {_stringify(row['from'])} → {_stringify(row['to'])}")
    if diff.get("added"):
        lines.append("新增：")
        for row in diff["added"]:
            lines.append(f"  {row['entity_id']}.{row['field']} = {_stringify(row.get('new'))}")
    if diff.get("removed"):
        lines.append("消失（后者不再有该字段）：")
        for row in diff["removed"]:
            lines.append(f"  {row['entity_id']}.{row['field']}（原值 {_stringify(row.get('new'))}）")
    return "\n".join(lines) if lines else "两次提交的状态变更完全一致。"


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    try:
        from .canon_locks import load_locks
    except ImportError:  # pragma: no cover
        from canon_locks import load_locks

    parser = argparse.ArgumentParser(description="章节状态变更审计（只读）")
    parser.add_argument("--project-root", required=True)
    parser.add_argument("action", nargs="?", default="list", choices=["list", "diff", "audit", "trace", "candidates", "preview"])
    parser.add_argument("--chapter", type=int)
    parser.add_argument("--from", dest="from_chapter", type=int)
    parser.add_argument("--to", dest="to_chapter", type=int)
    parser.add_argument("--limit", type=int, default=20, help="audit 回看章数")
    parser.add_argument("--high-risk", action="store_true", help="list 只显示 high/medium 风险项")
    # trace：某 (entity, field) 的跨章时间线 + 值回退检测
    parser.add_argument("--entity", help="trace 用：实体 id")
    parser.add_argument("--field", help="trace 用：字段名")
    parser.add_argument("--since-chapter", dest="since_chapter", type=int, default=0,
                        help="trace/candidates 用：从第几章开始")
    parser.add_argument("--min-changes", dest="min_changes", type=int, default=3,
                        help="candidates 用：至少变更几次才算候选（默认 3）")
    parser.add_argument("--file", help="preview 用：待预演的 extraction_result.json 或 commit.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.action == "diff":
        if args.from_chapter is None or args.to_chapter is None:
            print("diff 需要 --from 与 --to", file=sys.stderr)
            return 2
        left = collect_changes(load_commit(args.project_root, args.from_chapter))
        right = collect_changes(load_commit(args.project_root, args.to_chapter))
        diff = diff_changes(left, right)
        if args.json:
            print(json.dumps(diff, ensure_ascii=False, indent=2))
        else:
            print(f"第 {args.from_chapter} 章 → 第 {args.to_chapter} 章")
            print(format_diff(diff))
        return 0

    if args.action == "preview":
        if not args.file:
            print("preview 需要 --file <extraction_result.json 或 commit.json>", file=sys.stderr)
            return 2
        try:
            payload = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            print(f"无法读取 {args.file}: {exc}", file=sys.stderr)
            return 1
        report = preview_payload(payload, locks=load_locks(Path(args.project_root)))
        if args.json:
            print(json.dumps(report, ensure_ascii=False, indent=2))
        else:
            print(format_preview(report))
        return 0

    if args.action == "candidates":
        locks = load_locks(Path(args.project_root))
        rows = list_lock_candidates(
            args.project_root, since_chapter=args.since_chapter,
            min_changes=args.min_changes, locks=locks,
        )
        if args.json:
            print(json.dumps(rows, ensure_ascii=False, indent=2))
        else:
            print(format_candidates(rows))
        return 0

    if args.action == "trace":
        if not args.entity or not args.field:
            print("trace 需要 --entity 与 --field", file=sys.stderr)
            return 2
        locks = load_locks(Path(args.project_root))
        rows = trace_changes(
            args.project_root, args.entity, args.field,
            since_chapter=args.since_chapter, locks=locks,
        )
        regressions = detect_value_regressions(rows)
        if args.json:
            print(json.dumps({"entity": args.entity, "field": args.field,
                              "changes": rows, "regressions": regressions},
                             ensure_ascii=False, indent=2))
        else:
            print(format_trace(rows, args.entity, args.field, regressions=regressions))
        return 0

    if args.action == "audit":
        findings = audit_changes(
            args.project_root, limit=args.limit, locks=load_locks(Path(args.project_root)),
        )
        if args.json:
            print(json.dumps(findings, ensure_ascii=False, indent=2))
        else:
            print(format_audit(findings))
        return 0

    if args.chapter is None:
        print("list 需要 --chapter", file=sys.stderr)
        return 2
    payload = load_commit(args.project_root, args.chapter)
    if payload is None:
        print(f"第 {args.chapter} 章没有 commit 记录（未提交？）", file=sys.stderr)
        return 1
    rows = annotate_changes(
        collect_changes(payload), load_locks(Path(args.project_root)),
    )
    if args.high_risk:
        rows = [row for row in rows if row.get("risk") in ("high", "medium")]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print(format_changes(rows, args.chapter))
    return 0
