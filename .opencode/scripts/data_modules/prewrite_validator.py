#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from .placeholder_scanner import scan_placeholders


class PrewriteValidator:
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root)

    def build(
        self,
        chapter: int,
        review_contract: Dict[str, Any],
        plot_structure: Dict[str, Any],
        story_contract: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        # P2 修复：直读 state.json 在并发 os.replace（writer 原子写）中途可能
        # 读到 0 字节 → JSONDecodeError 崩掉 context 装配。改走 read_json_safe，
        # 失败降级 {} 让 prewrite 检查继续（缺字段自然不阻断）。
        try:
            from security_utils import read_json_safe
        except ImportError:  # pragma: no cover
            from scripts.security_utils import read_json_safe
        state = read_json_safe(
            self.project_root / ".webnovel" / "state.json", default={}
        )
        pending = state.get("disambiguation_pending") or []
        warnings = state.get("disambiguation_warnings") or []
        contract_provided = story_contract is not None
        story_contract = story_contract or {}
        missing_contracts = []
        if contract_provided:
            missing_contracts = [
                name
                for name in ("master_setting", "chapter_brief", "volume_brief", "review_contract")
                if not story_contract.get(name)
            ]
        blocking_reasons = []
        if pending:
            blocking_reasons.append("存在高优先级 disambiguation_pending")
        if missing_contracts:
            blocking_reasons.append(
                "缺少 Story System 合同: " + ", ".join(missing_contracts)
            )
        related_placeholders = self._related_placeholders(story_contract)
        if related_placeholders:
            blocking_reasons.append("当前章节相关设定存在未补齐占位")
        return {
            "chapter": chapter,
            "blocking": bool(pending) or bool(missing_contracts) or bool(related_placeholders),
            "blocking_reasons": blocking_reasons,
            "missing_contracts": missing_contracts,
            "related_placeholders": related_placeholders,
            "forbidden_zones": list(review_contract.get("blocking_rules") or []),
            "disambiguation_domain": {
                "pending_count": len(pending),
                "warning_count": len(warnings),
                "allowed_mentions": [
                    item.get("mention", "")
                    for item in warnings
                    if isinstance(item, dict) and item.get("mention")
                ],
            },
            "fulfillment_seed": {
                "planned_nodes": list(plot_structure.get("mandatory_nodes") or []),
                "prohibitions": list(plot_structure.get("prohibitions") or []),
            },
        }

    def _related_placeholders(self, story_contract: Dict[str, Any]) -> list[Dict[str, Any]]:
        chapter_brief = story_contract.get("chapter_brief") or {}
        directive = chapter_brief.get("chapter_directive") or {}
        entity_terms = [
            str(item or "").strip()
            for item in directive.get("key_entities") or []
            if str(item or "").strip()
        ]
        if not entity_terms:
            return []

        related: list[Dict[str, Any]] = []
        for item in scan_placeholders(self.project_root):
            context = str(item.get("context") or "")
            file_name = Path(str(item.get("file") or "")).stem
            if any(term in context or term in file_name for term in entity_terms):
                related.append(item)
        return related
