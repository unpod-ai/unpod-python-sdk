"""Tests for ObservabilityManager."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from unpod.observability import ObservabilityManager


def _make_manager(fire_hook=None) -> ObservabilityManager:
    return ObservabilityManager(
        session_id="sess-abc",
        user_id="user-1",
        fire_hook=fire_hook or AsyncMock(),
    )


@pytest.mark.anyio
async def test_start_and_end_turn_no_langfuse_no_crash():
    """Works silently without LANGFUSE_SECRET_KEY."""
    fired = []
    async def _hook(event, **kwargs):
        fired.append((event, kwargs))

    mgr = _make_manager(fire_hook=_hook)
    mgr.start_turn(1, "Hello")
    mgr.end_turn("Hi there", "greet", "ask_name")
    # No crash, no Langfuse calls expected


@pytest.mark.anyio
async def test_record_llm_call_fires_llm_call_hook():
    fired = []
    async def _hook(event, **kwargs):
        fired.append((event, kwargs))

    from unittest.mock import MagicMock
    data = MagicMock()
    data.node_id = "good_time_check"
    data.model = "gpt-4.1-mini"
    data.call_type = "routing"
    data.latency_ms = 2000.0
    data.tokens_in = 450
    data.tokens_out = 12
    data.prompt_messages = []
    data.response_json = {"tool_call": "edge_good"}
    data.edge_id = "edge_good"

    mgr = _make_manager(fire_hook=_hook)
    mgr.start_turn(2, "Okay")
    await mgr.record_llm_call(data)

    assert len(fired) == 1
    event, kwargs = fired[0]
    assert event == "llm_call"
    assert kwargs["node_id"] == "good_time_check"
    assert kwargs["latency_ms"] == 2000.0
    assert kwargs["tokens_in"] == 450
    assert kwargs["turn_id"] == 2


@pytest.mark.anyio
async def test_record_pipeline_scores_fires_turn_complete_hook():
    fired = []
    async def _hook(event, **kwargs):
        fired.append((event, kwargs))

    mgr = _make_manager(fire_hook=_hook)
    mgr.start_turn(3, "Yes")
    mgr.end_turn("Great!", "greet", "ask_yob")
    await mgr.record_pipeline_scores(
        turn_id=3, ttfa_ms=1800.0, asr_ms=180.0, tts_ttfb_ms=220.0,
        from_node="greet", to_node="ask_yob",
        llm_call_count=1, llm_total_ms=1500.0,
    )

    assert any(e == "turn_complete" for e, _ in fired)
    tc = next(kw for e, kw in fired if e == "turn_complete")
    assert tc["ttfa_ms"] == 1800.0
    assert tc["from_node"] == "greet"


@pytest.mark.anyio
async def test_no_llm_call_hook_without_start_turn():
    """record_llm_call before start_turn is silently ignored."""
    fired = []
    async def _hook(event, **kwargs): fired.append(event)

    from unittest.mock import MagicMock
    data = MagicMock()
    data.node_id = "n"
    data.model = "m"
    data.call_type = "routing"
    data.latency_ms = 100.0
    data.tokens_in = 10
    data.tokens_out = 5
    data.prompt_messages = []
    data.response_json = {}
    data.edge_id = None

    mgr = _make_manager(fire_hook=_hook)
    await mgr.record_llm_call(data)
    assert fired == []
