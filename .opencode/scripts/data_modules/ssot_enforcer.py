"""Single Source of Truth enforcer — all state mutations go through the event log.

Industry reference: Event Sourcing + CQRS (EventStoreDB, Axon Framework).

Architectural guarantee:
  .story-system/events/*.event.json  ←  append-only TRUTH (event log)
  state.json / index.db              ←  materialized VIEW (projection, rebuildable)

Every state mutation MUST:
  1. Write event to event log first (immutable)
  2. Apply to projection (state.json + index.db)
  3. Projection can be rebuilt from event log at any time

Consistency check:
  ssot verify --project-root <PATH>  →  compares projection vs event log, reports drift
  ssot rebuild --project-root <PATH> →  rebuilds all projections from event log
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ── Event log ────────────────────────────────────────────────────────

_EVENT_LOG_DIR = ".story-system/events"


def _event_log_dir(project_root: Path) -> Path:
    return project_root / _EVENT_LOG_DIR


def _next_event_seq(log_dir: Path) -> int:
    """Return the next sequence number for the event log."""
    if not log_dir.is_dir():
        return 1
    existing = sorted(log_dir.glob("*.event.json"))
    if not existing:
        return 1
    last = existing[-1].stem.replace(".event", "")
    try:
        return int(last) + 1
    except ValueError:
        return len(existing) + 1


def publish_event(project_root: Path, event_type: str, payload: dict,
                  chapter: int = 0) -> Path:
    """Append an event to the immutable event log. Returns path to event file.

    This is the ONLY write path for state-changing operations.
    SQLite mirroring is handled by EventLogStore for story content events;
    SSOT-specific meta events (chapter_status_changed, override_rule_added, etc.)
    exist only in the JSON event log and are consumed by rebuild_state_json.

    event_type examples:
      chapter_status_changed, entity_created, entity_updated,
      override_rule_added, override_rule_superseded,
      open_loop_created, open_loop_closed,
      checkpoint_reached, projection_rebuilt
    """
    log_dir = _event_log_dir(project_root)
    log_dir.mkdir(parents=True, exist_ok=True)

    seq = _next_event_seq(log_dir)
    event_id = f"evt_{chapter}_{event_type}_{seq}"
    subject = payload.get("_subject", "")
    event = {
        "seq": seq,
        "event_id": event_id,
        "event_type": event_type,
        "chapter": chapter,
        "subject": subject,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "payload": payload,
    }

    event_path = log_dir / f"{seq:06d}.event.json"
    # Atomic write: temp file → rename
    tmp = log_dir / f".tmp.{seq:06d}.{os.getpid()}"
    tmp.write_text(json.dumps(event, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(event_path))
    return event_path


def read_events(project_root: Path,
                event_type: Optional[str] = None,
                chapter: Optional[int] = None,
                after_seq: int = 0) -> list[dict]:
    """Read events from the log, optionally filtered."""
    log_dir = _event_log_dir(project_root)
    if not log_dir.is_dir():
        return []

    events = []
    for path in sorted(log_dir.glob("*.event.json")):
        try:
            seq = int(path.stem.split(".")[0])
        except ValueError:
            continue
        if seq <= after_seq:
            continue
        try:
            event = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if event_type and event.get("event_type") != event_type:
            continue
        if chapter is not None and event.get("chapter") != chapter:
            continue
        events.append(event)
    return events


# ── Projection rebuild ───────────────────────────────────────────────


def rebuild_state_json(project_root: Path,
                       events: Optional[list[dict]] = None) -> dict:
    """Rebuild state.json as a materialized view from the event log.

    This is deterministic: replaying the same events always produces the same state.
    Accepts pre-loaded events to avoid redundant I/O.

    Handles all StoryEvent types (10) plus SSOT-specific events:
      character_state_changed, relationship_changed, world_rule_revealed,
      world_rule_broken, power_breakthrough, artifact_obtained,
      promise_created, promise_paid_off, open_loop_created, open_loop_closed,
      chapter_status_changed, entity_created, entity_updated,
      chapter_deleted, override_rule_added, override_rule_superseded.
    """
    if events is None:
        events = read_events(project_root)
    state = _empty_state()

    for evt in events:
        etype = evt["event_type"]
        payload = evt.get("payload") or {}
        ch = str(evt["chapter"])
        subject = payload.get("_subject", "")

        if etype == "chapter_status_changed":
            state.setdefault("progress", {}).setdefault("chapter_status", {})[ch] = {
                "status": payload.get("status", "unknown"),
                "last_event_seq": evt["seq"],
            }
            if payload.get("status") == "committed":
                state["progress"]["current_chapter"] = evt["chapter"]
                state["progress"]["last_updated"] = evt["timestamp"]

        elif etype == "chapter_deleted":
            for c in payload.get("chapters", []):
                state.setdefault("progress", {}).setdefault("chapter_status", {}).pop(str(c), None)

        elif etype == "entity_created":
            eid = payload.get("entity_id", subject)
            if eid:
                state.setdefault("entities_v3", {})[eid] = {
                    "entity_type": payload.get("entity_type", "unknown"),
                    "name": payload.get("entity_name", payload.get("name", eid)),
                    "first_seen_chapter": evt["chapter"],
                }

        elif etype == "entity_updated":
            eid = payload.get("entity_id", subject)
            if eid and eid in state.get("entities_v3", {}):
                ent = state["entities_v3"][eid]
                for key in ("name", "entity_type", "tier"):
                    if key in payload:
                        ent[key] = payload[key]

        elif etype == "character_state_changed":
            eid = payload.get("entity_id", subject)
            field = payload.get("field", "")
            new_val = payload.get("new") if "new" in payload else payload.get("new_value")
            if eid and field and new_val is not None:
                ent = state.setdefault("entities_v3", {}).setdefault(eid, {
                    "entity_type": payload.get("entity_type", "unknown"),
                    "name": payload.get("entity_name", eid),
                    "first_seen_chapter": evt["chapter"],
                })
                ent.setdefault("current_state", {})[field] = new_val
                # Sync to protagonist_state if applicable
                ps = state.get("protagonist_state", {})
                if ps.get("entity_id") == eid or ps.get("name") == ent.get("name"):
                    state.setdefault("protagonist_state", {})[field] = new_val

        elif etype == "power_breakthrough":
            eid = payload.get("entity_id", subject)
            realm = payload.get("new_realm") or payload.get("realm") or payload.get("new")
            if eid and realm:
                ent = state.setdefault("entities_v3", {}).setdefault(eid, {
                    "entity_type": "角色",
                    "name": payload.get("entity_name", eid),
                    "first_seen_chapter": evt["chapter"],
                })
                ent.setdefault("current_state", {})["realm"] = realm
                ps = state.get("protagonist_state", {})
                if ps.get("entity_id") == eid or ps.get("name") == ent.get("name"):
                    state.setdefault("protagonist_state", {})["realm"] = realm

        elif etype == "relationship_changed":
            from_e = payload.get("from_entity", "")
            to_e = payload.get("to_entity", "")
            rel_type = payload.get("relationship_type") or payload.get("type", "")
            if from_e and to_e and rel_type:
                existing = [r for r in state.get("relationships", [])
                            if r.get("from") == from_e and r.get("to") == to_e and r.get("type") == rel_type]
                if existing:
                    existing[0]["last_seen_chapter"] = evt["chapter"]
                else:
                    state.setdefault("relationships", []).append({
                        "from": from_e,
                        "to": to_e,
                        "type": rel_type,
                        "description": payload.get("description", ""),
                        "first_seen_chapter": evt["chapter"],
                        "last_seen_chapter": evt["chapter"],
                    })

        elif etype == "artifact_obtained":
            eid = payload.get("artifact_id") or payload.get("entity_id", subject)
            owner = payload.get("owner") or payload.get("holder", "")
            if eid:
                artifacts = state.setdefault("artifacts", [])
                if not any(a.get("artifact_id") == eid for a in artifacts):
                    artifacts.append({
                        "artifact_id": eid,
                        "name": payload.get("name", eid),
                        "owner": owner,
                        "obtained_chapter": evt["chapter"],
                    })

        elif etype == "world_rule_revealed":
            rule_id = payload.get("rule_id", f"rule_ch{evt['chapter']}")
            rules = state.setdefault("world_rules", [])
            if not any(r.get("rule_id") == rule_id for r in rules):
                rules.append({
                    "rule_id": rule_id,
                    "description": payload.get("description", payload.get("rule", "")),
                    "revealed_chapter": evt["chapter"],
                    "status": "active",
                })

        elif etype == "world_rule_broken":
            rule_id = payload.get("rule_id", "")
            desc = payload.get("description", payload.get("rule", ""))
            for rule in state.get("world_rules", []):
                matched = (rule_id and rule.get("rule_id") == rule_id) or (desc and rule.get("description") == desc)
                if matched:
                    rule["status"] = "broken"
                    rule["broken_chapter"] = evt["chapter"]
                    rule["broken_reason"] = payload.get("reason", "")
                    break

        elif etype == "promise_created":
            pid = payload.get("promise_id", f"promise_ch{evt['chapter']}")
            promises = state.setdefault("reader_promises", [])
            if not any(p.get("promise_id") == pid for p in promises):
                promises.append({
                    "promise_id": pid,
                    "description": payload.get("description", ""),
                    "created_chapter": evt["chapter"],
                    "status": "active",
                })

        elif etype == "promise_paid_off":
            pid = payload.get("promise_id", "")
            desc = payload.get("description", "")
            for p in state.get("reader_promises", []):
                matched = False
                if pid and p.get("promise_id") == pid:
                    matched = True
                elif desc and not pid and p.get("description") == desc:
                    matched = True
                if matched:
                    p["status"] = "paid_off"
                    p["paid_chapter"] = evt["chapter"]
                    break

        elif etype == "override_rule_added":
            state.setdefault("override_rules", []).append({
                "constraint_id": payload.get("constraint_id", ""),
                "old_rule": payload.get("old_rule", ""),
                "new_rule": payload.get("new_rule", ""),
                "rationale": payload.get("rationale", ""),
                "chapter": evt["chapter"],
                "status": "active",
            })

        elif etype == "override_rule_superseded":
            cid = payload.get("constraint_id", "")
            for rule in state.get("override_rules", []):
                if rule.get("constraint_id") == cid and rule.get("status") == "active":
                    rule["status"] = "superseded"

        elif etype == "open_loop_created":
            loop = {
                "content": payload.get("content", ""),
                "urgency": payload.get("urgency", 50),
                "planted_chapter": evt["chapter"],
                "status": "active",
            }
            state.setdefault("foreshadowing", []).append(loop)
            # 与 StateProjectionWriter._apply_foreshadowing 同构的 plot_threads
            # 聚合路径（dashboard 消费）：created 按 content 去重，幂等。
            _apply_foreshadowing_event(
                state, evt["chapter"], "open_loop_created", payload,
            )

        elif etype == "open_loop_closed":
            for loop in state.get("foreshadowing", []):
                if loop.get("content") == payload.get("content"):
                    loop["status"] = "closed"
                    loop["closed_chapter"] = evt["chapter"]
            _apply_foreshadowing_event(
                state, evt["chapter"], "open_loop_closed", payload,
            )

    return state


def _loop_content(payload: dict) -> str:
    """从 open_loop 事件 payload 多候选字段提取 content（与 memory 侧
    _coerce_loop_content 的提取规则一致：content → unanswered_question →
    loop_type+description → description → subject 兜底）。"""
    for key in ("content", "unanswered_question"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value
    description = str(payload.get("description") or "").strip()
    loop_type = str(payload.get("loop_type") or "").strip()
    if description and loop_type:
        return f"{loop_type}：{description}"
    if description:
        return description
    if loop_type:
        return loop_type
    subject = str(payload.get("_subject") or "").strip()
    return subject


def _apply_foreshadowing_event(state: dict, chapter: int, event_type: str,
                               payload: dict) -> None:
    """把单个 open_loop 事件聚合进 plot_threads.foreshadowing（与
    StateProjectionWriter._apply_foreshadowing 的幂等规则一致）。

    用于 rebuild_state_json 的独立 replay 路径，确保重建结果与增量
    投影路径写入的字段保持一致，避免 ssot rebuild 冲掉 plot_threads。
    """
    content = _loop_content(payload)
    if not content:
        return
    plot_threads = state.get("plot_threads")
    if not isinstance(plot_threads, dict):
        plot_threads = {}
        state["plot_threads"] = plot_threads
    rows = plot_threads.get("foreshadowing")
    if not isinstance(rows, list):
        rows = []
        plot_threads["foreshadowing"] = rows

    row = next(
        (r for r in rows
         if isinstance(r, dict) and str(r.get("content") or "").strip() == content),
        None,
    )
    if event_type == "open_loop_created":
        if row is not None:
            row.setdefault("planted_chapter", chapter)
            return
        new_row: dict = {"content": content, "status": "active",
                         "planted_chapter": chapter}
        target = _to_int(payload.get("target_chapter") or payload.get("due_chapter"))
        if target > 0:
            new_row["target_chapter"] = target
        tier = str(payload.get("tier") or "").strip()
        if tier:
            new_row["tier"] = tier
        rows.append(new_row)
    else:
        if row is None:
            rows.append({"content": content, "status": "resolved",
                         "resolved_chapter": chapter})
        elif str(row.get("status") or "") != "resolved":
            row["status"] = "resolved"
            row["resolved_chapter"] = chapter


def _to_int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _empty_state() -> dict:
    return {
        "schema_version": "5.1",
        "progress": {"current_chapter": 0, "chapter_status": {}, "last_updated": ""},
        "entities_v3": {},
        "relationships": [],
        "foreshadowing": [],
        "protagonist_state": {},
        "world_rules": [],
        "reader_promises": [],
        "artifacts": [],
        "override_rules": [],
    }


def rebuild_projections(project_root: Path) -> dict:
    """Rebuild all projections from event log. Returns summary."""
    state = rebuild_state_json(project_root)
    state_path = project_root / ".webnovel" / "state.json"

    from security_utils import atomic_write_json
    state_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(state_path, state, use_lock=True, backup=True)

    event_count = sum(1 for _ in _event_log_dir(project_root).glob("*.event.json"))
    publish_event(project_root, "projection_rebuilt", {
        "target": "state.json",
        "event_count": event_count,
    })

    # Render markdown projections after rebuild
    try:
        from .state_projection_renderer import render_all_projections
        render_all_projections(project_root)
    except Exception as exc:
        logger.warning("Markdown projection render failed during rebuild: %s", exc)

    return {
        "projection": "state.json",
        "event_count": event_count,
        "chapters_in_state": len(state.get("progress", {}).get("chapter_status", {})),
        "entities_in_state": len(state.get("entities_v3", {})),
    }


# ── Consistency verification ─────────────────────────────────────────


def verify_consistency(project_root: Path) -> list[dict]:
    """Compare state.json projection against event log. Returns list of drifts."""
    drifts = []

    state_path = project_root / ".webnovel" / "state.json"
    try:
        actual_state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [{"severity": "error", "detail": "state.json missing or unreadable"}]

    events = read_events(project_root)
    expected = rebuild_state_json(project_root, events=events)

    # Compare chapter_status
    actual_chs = set((actual_state.get("progress") or {}).get("chapter_status") or {})
    expected_chs = set((expected.get("progress") or {}).get("chapter_status") or {})

    if actual_chs != expected_chs:
        drifts.append({
            "severity": "warning",
            "field": "progress.chapter_status",
            "actual": sorted(actual_chs),
            "expected": sorted(expected_chs),
            "detail": f"State has keys {sorted(actual_chs)}, event log projects {sorted(expected_chs)}",
        })

    # Compare foreshadowing count
    actual_fs = len(actual_state.get("foreshadowing") or [])
    expected_fs = len(expected.get("foreshadowing") or [])
    if actual_fs != expected_fs:
        drifts.append({
            "severity": "warning",
            "field": "foreshadowing",
            "actual": actual_fs,
            "expected": expected_fs,
            "detail": f"State has {actual_fs} loops, event log projects {expected_fs}",
        })

    # Compare entities_v3
    actual_ent = set((actual_state.get("entities_v3") or {}).keys())
    expected_ent = set((expected.get("entities_v3") or {}).keys())
    if actual_ent != expected_ent:
        drifts.append({
            "severity": "warning",
            "field": "entities_v3",
            "actual_count": len(actual_ent),
            "expected_count": len(expected_ent),
            "detail": f"Entity key sets differ (state={len(actual_ent)}, log={len(expected_ent)})",
        })

    # Compare collection counts for fields rebuild_state_json now produces
    for field in ("relationships", "world_rules", "reader_promises", "artifacts", "override_rules"):
        actual_count = len(actual_state.get(field) or [])
        expected_count = len(expected.get(field) or [])
        if actual_count != expected_count:
            drifts.append({
                "severity": "warning",
                "field": field,
                "actual": actual_count,
                "expected": expected_count,
                "detail": f"State has {actual_count} {field}, event log projects {expected_count}",
            })

    if not drifts:
        drifts.append({"severity": "info", "detail": "State is consistent with event log."})
    return drifts


# ── CLI ───────────────────────────────────────────────────────────────


def cmd_ssot(args) -> int:
    project_root = Path(args.project_root).expanduser().resolve()

    if args.action == "verify":
        drifts = verify_consistency(project_root)
        for d in drifts:
            sev = d["severity"].upper()
            print(f"{sev} {d.get('field', '')}: {d['detail']}")
        return 0 if all(d["severity"] == "info" for d in drifts) else 1

    if args.action == "rebuild":
        summary = rebuild_projections(project_root)
        print(f"Rebuilt {summary['projection']}: "
              f"{summary['event_count']} events → "
              f"{summary['chapters_in_state']} chapters, "
              f"{summary['entities_in_state']} entities")
        return 0

    if args.action == "events":
        events = read_events(project_root,
                             event_type=getattr(args, 'event_type', None),
                             chapter=getattr(args, 'chapter', None))
        print(json.dumps(events, ensure_ascii=False, indent=2))
        return 0

    return 1


def main():
    import argparse
    ap = argparse.ArgumentParser(description="SSOT enforcer — event log consistency")
    ap.add_argument("--project-root", required=True, help="Book project root")
    sub = ap.add_subparsers(dest="action", required=True)

    sub.add_parser("verify", help="Check state.json consistency against event log")
    sub.add_parser("rebuild", help="Rebuild state.json from event log")
    p_events = sub.add_parser("events", help="Read event log")
    p_events.add_argument("--event-type", help="Filter by event type")
    p_events.add_argument("--chapter", type=int, help="Filter by chapter")

    args = ap.parse_args()
    raise SystemExit(cmd_ssot(args))
