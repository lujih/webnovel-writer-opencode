"""Contract tests: foreshadowing loop-content coercion is identical across
state writer / memory writer / ssot enforcer rebuild (P1 audit fix #7).

Any divergence in the extracted content string means an `open_loop_closed`
event cannot match the same row in two projection paths → "active
foreshadowing" residue + top-level foreshadowing vs nested plot_threads
forever out of sync.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from data_modules.foreshadowing_utils import coerce_loop_content


class TestCoerceLoopContentContract:
    """All 6 priority tiers must produce the same string on every side."""

    def test_content_field_wins(self):
        payload = {"content": "黑色棺材的来历", "unanswered_question": "X",
                   "description": "d", "loop_type": "t"}
        assert coerce_loop_content(payload, None) == "黑色棺材的来历"

    def test_unanswered_question_second(self):
        payload = {"unanswered_question": "芯片设计者身份",
                   "description": "d", "loop_type": "t"}
        assert coerce_loop_content(payload, None) == "芯片设计者身份"

    def test_structured_loop_type_plus_description(self):
        payload = {"description": "芯片设计者身份", "loop_type": "信息悬疑"}
        assert coerce_loop_content(payload, None) == "信息悬疑：芯片设计者身份"

    def test_description_only(self):
        payload = {"description": "芯片设计者身份"}
        assert coerce_loop_content(payload, None) == "芯片设计者身份"

    def test_loop_type_only(self):
        payload = {"loop_type": "信息悬疑"}
        assert coerce_loop_content(payload, None) == "信息悬疑"

    def test_subject_fallback_from_event_top_level(self):
        payload = {}
        event = {"subject": "loop_ch3", "payload": payload}
        assert coerce_loop_content(payload, event) == "loop_ch3"

    def test_subject_fallback_from_flattened_payload(self):
        """rebuild replay: subject flattened into payload['_subject']."""
        payload = {"_subject": "chen_sheng"}
        assert coerce_loop_content(payload, None) == "chen_sheng"

    def test_event_subject_preferred_over_flattened(self):
        payload = {"_subject": "flattened"}
        event = {"subject": "live_top"}
        assert coerce_loop_content(payload, event) == "live_top"

    def test_all_empty_returns_empty_string(self):
        assert coerce_loop_content({}, None) == ""

    def test_non_dict_payload_returns_empty(self):
        assert coerce_loop_content("garbage", None) == ""
        assert coerce_loop_content(None, None) == ""


class TestThreeSidesContract:
    """state writer / memory writer / enforcer must all call the same helper
    and produce identical strings for the same event.

    This is the regression net: if anyone re-introduces a local copy of the
    coercion rule (e.g. by reverting state_projection_writer._coerce_loop_content
    to its own loop), the cross-call equivalence below breaks.
    """

    def test_state_writer_matches_helper(self):
        from data_modules.state_projection_writer import StateProjectionWriter
        payload = {"description": "芯片设计者身份", "loop_type": "信息悬疑"}
        event = {"subject": "s", "payload": payload}
        assert StateProjectionWriter._coerce_loop_content(payload, event) == \
            coerce_loop_content(payload, event)

    def test_memory_writer_matches_helper(self):
        from data_modules.memory.writer import MemoryWriter
        payload = {"description": "芯片设计者身份", "loop_type": "信息悬疑"}
        event = {"subject": "s", "payload": payload}
        assert MemoryWriter._coerce_loop_content(payload, event) == \
            coerce_loop_content(payload, event)

    def test_enforcer_matches_helper(self):
        from data_modules.ssot_enforcer import _loop_content
        payload = {"description": "芯片设计者身份", "loop_type": "信息悬疑",
                   "_subject": "chen"}
        assert _loop_content(payload) == coerce_loop_content(payload, None)

    def test_subject_fallback_identical_across_live_and_replay(self):
        """An open_loop event with NO content/unanswered_question/description/
        loop_type but with a subject must yield the same content string on the
        live path (event top-level subject) and the replay path
        (payload flattened _subject)."""
        from data_modules.state_projection_writer import StateProjectionWriter
        from data_modules.memory.writer import MemoryWriter
        from data_modules.ssot_enforcer import _loop_content

        live_event = {"subject": "loop_ch3", "payload": {}}
        replay_payload = {"_subject": "loop_ch3"}

        live_state = StateProjectionWriter._coerce_loop_content({}, live_event)
        live_mem = MemoryWriter._coerce_loop_content({}, live_event)
        replay_enf = _loop_content(replay_payload)

        assert live_state == "loop_ch3"
        assert live_mem == "loop_ch3"
        assert replay_enf == "loop_ch3"
        assert live_state == live_mem == replay_enf
