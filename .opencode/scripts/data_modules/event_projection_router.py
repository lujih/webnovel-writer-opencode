#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, List, Set

from .commit_artifacts import extraction_list, extraction_text


class EventProjectionRouter:
    TABLE = {
        "character_state_changed": ["state", "memory", "vector"],
        "power_breakthrough": ["state", "memory", "vector"],
        "relationship_changed": ["index", "vector"],
        "world_rule_revealed": ["memory", "vector"],
        "world_rule_broken": ["memory", "vector"],
        "open_loop_created": ["state", "memory"],
        "open_loop_closed": ["state", "memory"],
        "promise_created": ["memory"],
        "promise_paid_off": ["memory"],
        "artifact_obtained": ["index", "vector"],
    }

    def route(self, event: Dict) -> List[str]:
        return list(self.TABLE.get(str(event.get("event_type") or "").strip(), []))

    def required_writers(self, commit_payload: Dict) -> List[str]:
        writers: Set[str] = set()
        status = str((commit_payload.get("meta") or {}).get("status") or "")
        if status == "rejected":
            writers.add("state")
            return sorted(writers)
        if status == "accepted":
            writers.add("state")
            writers.add("index")
        if extraction_list(commit_payload, "entity_deltas"):
            writers.add("index")
        if extraction_text(commit_payload, "summary_text"):
            writers.add("summary")
        for event in extraction_list(commit_payload, "accepted_events"):
            if not isinstance(event, dict):
                continue
            writers.update(self.route(event))
        return sorted(writers)
