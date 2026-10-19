#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def run_checks(project_root: Path, chapter: int, intended_strand: str = "") -> dict[str, Any]:
    checks = []
    state, load_error = _load_state(project_root)

    # state.json 读不出来时，先报一条 blocking——「无法判断」不等于「通过」。
    # 这一步放在最前面，是为了让报告的第一条就说明原因，而不是让下面 6 条
    # 检查各自基于空 state 得出"看起来正常"的结论。
    if load_error is not None:
        checks.append({
            "name": "state_readable",
            "passed": False,
            "severity": "blocking",
            "detail": (
                f"state.json 无法读取（{load_error}）。"
                f"结构性检查无法进行——请先修复或从 .bak 恢复，"
                f"再运行 `ssot rebuild` 重建投影。"
            ),
        })

    checks.append(_check_strand_balance(state, chapter, intended_strand))
    checks.append(_check_entity_freshness(state, chapter))
    checks.append(_check_memory_bloat(project_root))
    checks.append(_check_debt_burden(state, project_root, chapter))
    checks.append(_check_contract_coverage(project_root, chapter))
    checks.append(_check_entity_name_consistency(state))

    passed = not any(c["severity"] == "blocking" and not c["passed"] for c in checks)
    result: dict[str, Any] = {"chapter": chapter, "passed": passed, "checks": checks}
    if load_error is not None:
        result["state_error"] = str(load_error)
    return result


def _load_state(project_root: Path) -> tuple[dict, Exception | None]:
    """读取 state.json，返回 ``(state, error)``。

    为什么不再直接抛：本模块是 LLM 审查之前的确定性检查层，结论直接决定
    `blocking` 与 `passed`。自己崩掉会让整条审查链路以 traceback 中断，
    而正确语义是「报一条 blocking 检查未通过」。

    用 `utf-8-sig` 读取：Windows 记事本 / VS Code 的「UTF-8 with BOM」是
    中文作者的默认保存格式，BOM 会让 `json.loads` 报
    "Unexpected UTF-8 BOM"——合法文件被误判成损坏。仓库里 7 处已用
    `utf-8-sig`（chapter_commit / review_pipeline / skill_runner /
    story_system_engine …），此处同样对齐。
    """
    state_file = project_root / ".webnovel" / "state.json"
    if not state_file.is_file():
        return {}, None
    try:
        return json.loads(state_file.read_text(encoding="utf-8-sig")), None
    except (OSError, ValueError) as exc:
        return {}, exc


def _check_strand_balance(state: dict, chapter: int, intended_strand: str = "") -> dict:
    tracker = state.get("strand_tracker") or {}
    history = tracker.get("history") or []
    result = {
        "name": "strand_balance",
        "passed": True,
        "severity": "blocking",
        "detail": "",
        "fix": "",
    }

    if not history:
        return result

    # count consecutive same dominant
    last = history[-1].get("dominant", "")
    consecutive = 0
    for entry in reversed(history):
        if entry.get("dominant") == last:
            consecutive += 1
        else:
            break
    if last == "quest" and consecutive > 5:
        if intended_strand in ("fire", "constellation"):
            result["passed"] = True
            result["detail"] = f"quest 连续主导 {consecutive} 章（上限 5 章），但本章已设定为 {intended_strand} 线，正在修复中"
            result["fix"] = ""
            return result
        result["passed"] = False
        result["detail"] = f"quest 连续主导 {consecutive} 章（上限 5 章）"
        result["fix"] = "切换到 Fire（感情线）或 Constellation（世界观线）"
        return result

    # If intended_strand matches the missing strand, this chapter is actively fixing it
    if intended_strand:
        last_const = _safe_int(tracker.get("last_constellation_chapter"))
        if intended_strand == "constellation" and (last_const == 0 or chapter - last_const > 8):
            result["passed"] = True
            detail_msg = "本章已设定为 constellation 线，正在修复中"
            if last_const == 0:
                detail_msg += "（从未激活）"
            else:
                detail_msg += f"（上次: 第{last_const}章）"
            result["detail"] = detail_msg
            result["fix"] = ""
            return result
        # Similarly for fire strand intent
        last_fire = _safe_int(tracker.get("last_fire_chapter"))
        if intended_strand == "fire" and (last_fire == 0 or chapter - last_fire > 8):
            result["passed"] = True
            detail_msg = "本章已设定为 fire 线，正在修复中"
            if last_fire == 0:
                detail_msg += "（从未激活）"
            else:
                detail_msg += f"（上次: 第{last_fire}章）"
            result["detail"] = detail_msg
            result["fix"] = ""
            return result

    # constellation check
    last_const = _safe_int(tracker.get("last_constellation_chapter"))
    if last_const == 0 and chapter > 8:
        result["passed"] = False
        result["detail"] = f"constellation 从未激活（当前第{chapter}章），最高容忍 8 章"
        result["fix"] = "本章或下一章必须安排世界观展开：新势力/新地点/设定揭示/身世线索"
    elif last_const > 0:
        gap = chapter - last_const
        if gap > 8:
            result["passed"] = False
            result["detail"] = f"constellation 已 {gap} 章未出现（上限 8 章）"
            result["fix"] = "安排世界观展示内容"

    return result


def _check_entity_freshness(state: dict, chapter: int) -> dict:
    result = {
        "name": "entity_freshness",
        "passed": True,
        "severity": "blocking",
        "detail": "",
        "fix": "",
    }
    protag = state.get("protagonist_state") or {}
    location = protag.get("location") or {}
    last_chapter = _safe_int(location.get("last_chapter"))
    if last_chapter <= 0:
        return result

    gap = chapter - last_chapter
    if gap >= 5:
        current_value = (location.get("current") or "").strip()
        if current_value:
            # Location value exists — data-agent IS writing location.
            # The last_chapter field is stale but the data is maintained.
            result["severity"] = "warning"
            result["passed"] = True
            result["detail"] = f"主角位置 last_chapter {gap} 章未更新（最后: 第{last_chapter}章），但 location.current 值存在"
            result["fix"] = "data-agent 需确保写入 location.current 时同步更新 last_chapter"
            return result
        result["passed"] = False
        result["detail"] = f"主角位置 {gap} 章未更新（最后: 第{last_chapter}章）"
        result["fix"] = "data-agent 需输出 location.current state_delta（即使位置未变）"
    return result


def _check_memory_bloat(project_root: Path) -> dict:
    result = {
        "name": "memory_bloat",
        "passed": True,
        "severity": "warning",
        "detail": "",
        "fix": "",
    }
    mem_file = project_root / ".webnovel" / "memory_scratchpad.json"
    if not mem_file.is_file():
        return result

    try:
        data = json.loads(mem_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return result

    # Support both flat list (legacy) and dict-of-buckets format
    if isinstance(data, dict):
        entries = []
        for v in data.values():
            if isinstance(v, list):
                entries.extend(v)
    elif isinstance(data, list):
        entries = data
    else:
        return result

    if not entries:
        return result

    outdated = sum(1 for e in entries if isinstance(e, dict) and e.get("status") == "outdated")
    ratio = outdated / len(entries)
    if ratio > 0.30:
        result["passed"] = False
        result["detail"] = f"记忆过期率 {ratio:.1%}（{outdated}/{len(entries)}），超过 30% 阈值"
        result["fix"] = "运行 memory-compact 清理过期条目"
    return result


def _check_debt_burden(state: dict, project_root=None, chapter=0):
    result = {
        "name": "debt_burden",
        "passed": True,
        "severity": "warning",
        "detail": "",
        "fix": "",
    }
    threshold = max(10, chapter // 2) if chapter > 0 else 10
    # Prefer index.db if available
    if project_root:
        db_path = Path(project_root) / ".webnovel" / "index.db"
        if db_path.is_file():
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            try:
                total = conn.execute(
                    "SELECT COUNT(*) FROM chase_debt WHERE status='active'"
                ).fetchone()[0]
                overdue = conn.execute(
                    "SELECT COUNT(*) FROM chase_debt WHERE status='active' AND due_chapter < ?",
                    (chapter,)
                ).fetchone()[0]
                if total > threshold:
                    result["passed"] = False
                    result["detail"] = f"active debts {total} (threshold {threshold}), {overdue} overdue"
                    result["fix"] = "resolve overdue foreshadowing in upcoming chapters"
                elif overdue > 0:
                    result["passed"] = False
                    result["detail"] = f"{overdue} debts overdue"
                    result["fix"] = "check overdue foreshadowing, repay in upcoming chapters"
                return result
            finally:
                conn.close()
    # Fallback: read from state.json
    foreshadowing = (state.get("plot_threads") or {}).get("foreshadowing") or []
    unresolved = [f for f in foreshadowing if f.get("status") == "未回收"]
    if len(unresolved) > threshold:
        result["passed"] = False
        result["detail"] = f"unresolved foreshadowing {len(unresolved)} (threshold {threshold})"
        result["fix"] = "resolve or mark abandoned for overdue foreshadowing"
    return result


def _check_contract_coverage(project_root: Path, chapter: int) -> dict:
    result = {
        "name": "contract_coverage",
        "passed": True,
        "severity": "blocking",
        "detail": "",
        "fix": "",
    }
    # Try :03d first, then :04d, then unpadded (legacy formats)
    candidates = [
        project_root / ".story-system" / "chapters" / f"chapter_{chapter:03d}.json",
        project_root / ".story-system" / "chapters" / f"chapter_{chapter:04d}.json",
        project_root / ".story-system" / "chapters" / f"chapter_{chapter}.json",
    ]
    contract = None
    for c in candidates:
        if c.is_file():
            contract = c
            break
    if contract is None:
        result["passed"] = False
        result["detail"] = f"缺少 chapter_{chapter:03d}.json 合同"
        result["fix"] = "运行 story-system 生成本章合同"
    return result


def _check_entity_name_consistency(state: dict) -> dict:
    """检查实体名是否有一字之差的疑似笔误（如 洪/鸿）。"""
    result = {
        "name": "entity_name_consistency",
        "passed": True,
        "severity": "warning",
        "detail": "",
        "fix": "",
    }
    entities = state.get("entities") or {}
    if not isinstance(entities, dict) or len(entities) < 2:
        return result

    names = [n for n in entities if isinstance(n, str) and len(n) >= 2]
    suspicious = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            if len(a) != len(b):
                continue
            diffs = sum(1 for ca, cb in zip(a, b) if ca != cb)
            if diffs == 1:
                suspicious.append(f"{a} ↔ {b}")

    if suspicious:
        result["passed"] = False
        result["detail"] = f"疑似笔误的相似实体名: {', '.join(suspicious)}"
        result["fix"] = "确认是否为同一实体，统一名称；若为不同实体，可忽略"
    return result


def _safe_int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="结构自检（写前阻断）")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--chapter", type=int, required=True, help="目标章节号")
    parser.add_argument("--format", choices=["json", "text"], default="json", help="输出格式")
    args = parser.parse_args()

    project_root = Path(args.project_root).expanduser().resolve()
    result = run_checks(project_root, args.chapter)

    if args.format == "json":
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        status = "全部通过" if result["passed"] else "存在阻断问题"
        print(f"第{result['chapter']}章 结构自检: {status}")
        for c in result["checks"]:
            icon = "PASS" if c["passed"] else "FAIL"
            print(f"  {icon} [{c['severity']}] {c['name']}")
            if not c["passed"]:
                print(f"         {c['detail']}")
                print(f"      -> {c['fix']}")

    raise SystemExit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
