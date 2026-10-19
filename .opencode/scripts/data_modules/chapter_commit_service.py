#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from chapter_outline_loader import volume_num_for_chapter_from_state

import logging

from .commit_artifacts import extraction_list
from .config import DataModulesConfig
from .event_log_store import EventLogStore
from .event_projection_router import EventProjectionRouter
from .story_contracts import write_json
from .index_manager import IndexManager
from .override_ledger_service import (
    AmendProposalTrigger,
    ensure_override_ledger_columns,
    persist_amend_proposals,
)
from .review_schema import parse_review_output
from .ssot_enforcer import publish_event, read_events
from .write_gates import run_write_gate

logger = logging.getLogger(__name__)


class WriteGateRejected(RuntimeError):
    """precommit gate 拒绝提交。

    单独一个异常类型而不是 ValueError：调用方（CLI / memory_contract_adapter）
    需要区分「门禁拦下」与「代码 bug」，前者应原样转达给用户并附修复建议，
    后者应进日志排查。
    """

    def __init__(self, message: str, gate: dict | None = None):
        super().__init__(message)
        self.gate = gate or {}

# 伏笔默认偿还章数：创建债务时默认要求 10 章内偿还
_FORESHADOW_DUE_OFFSET = 10


class ChapterCommitService:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)
        state_file = self.project_root / ".webnovel" / "state.json"
        if not state_file.is_file():
            raise FileNotFoundError(
                f"无效的项目根目录: {self.project_root}（缺少 .webnovel/state.json）。"
                "请确认 --project-root 指向正确的书项目目录。"
            )

    def build_commit(
        self,
        chapter: int,
        review_result: Dict[str, Any],
        fulfillment_result: Dict[str, Any],
        disambiguation_result: Dict[str, Any],
        extraction_result: Dict[str, Any],
    ) -> Dict[str, Any]:
        # Classify missed nodes: CBN → blocking, CPN → warning, CEN → warning.
        # Non-dict nodes (plain strings) are treated as blocking for backward compat.
        missed = list(fulfillment_result.get("missed_nodes") or [])
        missed_raw = [n for n in missed if not isinstance(n, dict)]
        missed_dicts = [n for n in missed if isinstance(n, dict)]
        missed_cbn = [n for n in missed_dicts
                      if str(n.get("type", "")).upper() == "CBN"]
        missed_cpn = [n for n in missed_dicts
                      if str(n.get("type", "")).upper() == "CPN"]
        missed_cen = [n for n in missed_dicts
                      if str(n.get("type", "")).upper() == "CEN"]
        missed_other = [n for n in missed_dicts
                        if str(n.get("type", "")).upper() not in ("CBN", "CPN", "CEN")]

        # Plain strings or CBN-typed dicts → blocking
        # 通过 parse_review_output 归一化（含 severity→blocking 推导），不直接信任 LLM 原始值
        _normalized = parse_review_output(chapter, review_result)
        rejected = _normalized.has_blocking or bool(missed_raw) or bool(missed_cbn) or bool(
            disambiguation_result.get("pending")
        )
        status = "rejected" if rejected else "accepted"
        volume = volume_num_for_chapter_from_state(self.project_root, chapter) or 1
        return {
            "meta": {
                "schema_version": "story-system/v1",
                "chapter": chapter,
                "status": status,
            },
            "contract_refs": {
                "master": ".story-system/MASTER_SETTING.json",
                "volume": f".story-system/volumes/volume_{volume:03d}.json",
                "chapter": f".story-system/chapters/chapter_{chapter:03d}.json",
                "review": f".story-system/reviews/chapter_{chapter:03d}.review.json",
            },
            "provenance": {
                "write_fact_role": "chapter_commit",
                "projection_role": "derived_read_models",
                "legacy_state_role": "projection_only",
            },
            "outline_snapshot": {
                "planned_nodes": fulfillment_result.get("planned_nodes", []),
                "covered_nodes": fulfillment_result.get("covered_nodes", []),
                "missed_nodes": missed,
                "missed_cbn": missed_cbn,
                "missed_cpn": missed_cpn,
                "missed_cen": missed_cen,
                "missed_other": missed_other,
                "extra_nodes": fulfillment_result.get("extra_nodes", []),
            },
            "review_result": review_result,
            "fulfillment_result": fulfillment_result,
            "disambiguation_result": disambiguation_result,
            "extraction_result": dict(extraction_result),
            "projection_status": {
                "state": "pending",
                "index": "pending",
                "summary": "pending",
                "memory": "pending",
                "vector": "pending",
            },
        }

    def persist_commit(self, payload: Dict[str, Any]) -> Path:
        # precommit gate：畸形产物不得落盘进 commits/。
        #
        # 位置说明：`data_modules/write_gates/` 早已实现（3 个阶段 + 一份设计
        # 文档），但此前**从未被生产代码调用**——门禁是死代码，测试全绿而
        # 真实流程毫无防护。接入点选 persist_commit 而不是 build_commit：
        # build_commit 是纯函数（只按入参拼 payload），往里加入磁盘检查会让
        # 它不再可独立测试；而 persist_commit 是 **commit 的唯一写入点**
        # （chapter_commit.py 与 apply_projections 都经过它），与本仓库
        # 「publish_event 是 SSOT 唯一写路径」的约定一致。
        #
        # 只对 accepted 生效：rejected 章节的产物本就可能是残缺的（那正是被
        # 否的原因），拦它没有意义，而且 rejected 记录本身要留存备查。
        if str((payload.get("meta") or {}).get("status") or "") == "accepted":
            chapter_no = int((payload.get("meta") or {}).get("chapter") or 0)
            gate = run_write_gate("precommit", self.project_root, chapter_no)
            if not gate.get("ok"):
                payload.setdefault("write_gates", {})["precommit"] = gate
                raise WriteGateRejected(
                    f"precommit gate 阻断第 {chapter_no} 章提交："
                    + "; ".join(
                        f"[{e.get('code')}] {e.get('message')}"
                        for e in (gate.get("errors") or [])
                    ),
                    gate=gate,
                )

        target = self.project_root / ".story-system" / "commits"
        target.mkdir(parents=True, exist_ok=True)
        path = target / f"chapter_{int(payload['meta']['chapter']):03d}.commit.json"
        write_json(path, payload)
        return path

    def _sync_foreshadowing(self, commit_payload: dict) -> None:
        """Sync foreshadowing events from commit payload to debt tracker.

        债务 note 使用与 state/memory 投影**同一**的归一 content
        （foreshadowing_utils.coerce_loop_content），保证 created 建债与
        closed 销债匹配到同一条目——裸 payload.content 在字段缺失/
        多候选 schema（unanswered_question/loop_type+description）下
        会与投影侧 content 分叉，导致伏笔债永远无法闭合、active
        计数虚高。
        """
        from .foreshadowing_utils import coerce_loop_content
        events = extraction_list(commit_payload, "accepted_events")
        if not events:
            return
        chapter = int(commit_payload.get("meta", {}).get("chapter", 0))
        if chapter <= 0:
            return
        idx = IndexManager(DataModulesConfig.from_project_root(str(self.project_root)))
        for evt in events:
            if not isinstance(evt, dict):
                continue
            etype = evt.get("event_type", "")
            payload = evt.get("payload") or {}
            subject = evt.get("subject", payload.get("subject", ""))
            content = coerce_loop_content(payload, evt)
            if etype == "open_loop_created":
                due = chapter + _FORESHADOW_DUE_OFFSET
                note = content or subject or f"ch{chapter} foreshadowing"
                idx.create_simple_debt(
                    debt_type="foreshadowing",
                    source_chapter=chapter,
                    due_chapter=due,
                    note=note,
                    subject=subject,
                )
            elif etype in ("open_loop_closed", "promise_paid_off"):
                # 必须用与建债侧**完全相同**的键（coerce_loop_content 的结果）。
                # 此前建债按 content 记 note、销债按 subject 搜，两边对不上：
                # 伏笔债永远销不掉，active 计数虚高。
                # 键为空时直接跳过——resolve_debt_by_subject 用的是
                # LIKE %x%，空键会匹配并销掉**全部**活跃债务。
                debt_key = content or subject
                if debt_key:
                    idx.resolve_debt_by_subject(subject=debt_key, chapter=chapter)

    def apply_projections(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        status = str((payload.get("meta") or {}).get("status") or "")
        if status not in {"accepted", "rejected"}:
            return payload

        chapter = int((payload.get("meta") or {}).get("chapter") or 0)
        if chapter <= 0:
            logger.warning("apply_projections: chapter fell back to %s (meta=%s)",
                           chapter, payload.get("meta"))


        # 只有 accepted 章节才写入事件日志和 SSOT
        ssot_publish_failures = 0
        if status == "accepted":
            # Use commit_artifacts helper for backward compat (nested vs top-level)
            accepted_events = extraction_list(payload, "accepted_events")
            extraction = payload.setdefault("extraction_result", {})
            if not isinstance(extraction, dict):
                extraction = {}
                payload["extraction_result"] = extraction

            # Guard: skip SSOT publishing if this chapter already has events
            # (retry safety — prevents duplicate events in the immutable event log)
            existing_events = read_events(self.project_root, chapter=chapter)
            if not existing_events:
                for event in accepted_events:
                    if not isinstance(event, dict):
                        continue
                    try:
                        event_payload = dict(event.get("payload", {}))
                        subject = event.get("subject", "")
                        if subject:
                            event_payload["_subject"] = subject
                        publish_event(
                            self.project_root,
                            event.get("event_type", "unknown"),
                            event_payload,
                            chapter=chapter,
                        )
                    except Exception as exc:
                        ssot_publish_failures += 1
                        logger.warning("SSOT publish_event failed for chapter %s event %s: %s",
                                       chapter, event.get("event_type", ""), exc)

                try:
                    publish_event(
                        self.project_root,
                        "chapter_status_changed",
                        {"status": "committed"},
                        chapter=chapter,
                    )
                except Exception as exc:
                    ssot_publish_failures += 1
                    logger.warning("SSOT chapter_status_changed failed for chapter %s: %s", chapter, exc)

            if ssot_publish_failures:
                # 事件日志与 commit 产物分叉：commit 仍标记 accepted（投影
                # 已应用），但 SSOT 缺事件——升级日志并计入 payload，
                # postcommit gate / 操作者可感知"需 ssot rebuild 补跑"。
                logger.error("chapter %s: %d SSOT 事件发布失败——事件日志与 commit 分叉，"
                             "建议运行 `ssot rebuild` 补齐投影", chapter, ssot_publish_failures)
                payload.setdefault("ssot", {})["publish_failures"] = ssot_publish_failures

            # Normalize events and store back into extraction_result
            normalized = EventLogStore(self.project_root).normalize_events(chapter, accepted_events)
            extraction["accepted_events"] = normalized
            try:
                EventLogStore(self.project_root).write_events(chapter, normalized)
            except Exception as exc:
                logger.warning("EventLogStore.write_events failed for chapter %s: %s", chapter, exc)

            proposals = AmendProposalTrigger().check(chapter, normalized)
            if proposals:
                manager = IndexManager(DataModulesConfig.from_project_root(self.project_root))
                with manager._get_conn() as conn:
                    ensure_override_ledger_columns(conn)
                    persist_amend_proposals(conn, chapter, proposals)
                    conn.commit()

        from .index_projection_writer import IndexProjectionWriter
        from .memory_projection_writer import MemoryProjectionWriter
        from .state_projection_writer import StateProjectionWriter
        from .summary_projection_writer import SummaryProjectionWriter
        from .vector_projection_writer import VectorProjectionWriter

        writers = {
            "state": StateProjectionWriter(self.project_root),
            "index": IndexProjectionWriter(self.project_root),
            "summary": SummaryProjectionWriter(self.project_root),
            "memory": MemoryProjectionWriter(self.project_root),
            "vector": VectorProjectionWriter(self.project_root),
        }
        required_writers = set(EventProjectionRouter().required_writers(payload))
        for name, writer in writers.items():
            if name not in required_writers:
                payload["projection_status"][name] = "skipped"
                continue
            try:
                result = writer.apply(payload)
                payload["projection_status"][name] = "done" if result.get("applied") else "skipped"
            except Exception as exc:
                payload["projection_status"][name] = f"failed:{exc}"
        self.persist_commit(payload)

        # 同步写入 projection_log（权威记录）
        try:
            from .projection_log import append_projection_run
            writer_results = {
                name: {"status": payload["projection_status"].get(name, "unknown")}
                for name in payload["projection_status"]
            }
            append_projection_run(self.project_root, payload, writer_results)
        except Exception as exc:
            logger.warning("projection_log append failed: %s", exc)

        self._sync_foreshadowing(payload)

        # Render markdown projections
        try:
            from .state_projection_renderer import render_all_projections
            render_all_projections(self.project_root)
        except Exception as exc:
            logger.warning("Markdown projection render failed: %s", exc)

        # 追读债计息：accepted 章节推进后自动按章节数复利。
        #
        # 位置说明：放在投影循环与 _sync_foreshadowing 之后——此时本章的
        # 建债/销债都已落库，计息看到的是本章最终的债务集合。
        #
        # 为什么必须在这里触发：`accrue_interest()` 此前只有一条手工 CLI
        # （index_manager.py 的 accrue-interest），从未接入提交链路。作者不
        # 主动跑命令，追读债就永远不产生利息、`get_overdue_debts()` 永远是
        # 空的——这套机制在默认使用路径上等于不存在。
        #
        # 失败不阻断：提交本身已成功，计息是附带动作。记 warning 即可。
        # 幂等性由 accrue_interest 内部按 debt_events 的 MAX(chapter) 保证。
        if status == "accepted":
            try:
                manager = IndexManager(
                    DataModulesConfig.from_project_root(self.project_root))
                accrual = manager.accrue_interest(chapter)
                payload.setdefault("debt_accrual", accrual)
            except Exception as exc:
                logger.warning(
                    "追读债计息失败（第 %s 章）：%s——不影响提交，"
                    "可稍后手工运行 `index accrue-interest` 补算", chapter, exc)

        # postcommit gate：投影完整性核验。
        # 提交已落盘，失败不回滚（回滚会把已写的 commit 也弄丢），
        # 但必须让调用方看见——否则「投影 3/5 失败」会以 return payload
        # 的正常路径静默流过去，直到 dashboard 显示空白才被发现。
        post = run_write_gate("postcommit", self.project_root, chapter)
        payload.setdefault("write_gates", {})["postcommit"] = post
        if not post.get("ok"):
            logger.error(
                "postcommit gate 未通过（第 %s 章）：%s",
                chapter,
                "; ".join(f"[{e.get('code')}] {e.get('message')}"
                          for e in (post.get("errors") or [])),
            )

        return payload
