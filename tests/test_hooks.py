"""Tests for hooks and metrics."""

import pytest
from unpod.connectivity.hooks import VALID_EVENTS, HookRegistry


@pytest.mark.anyio
async def test_register_and_fire_hook() -> None:
    registry = HookRegistry()
    received: list[str] = []

    @registry.on("user_turn")
    async def handler(text: str) -> None:
        received.append(text)

    await registry.fire("user_turn", "hello")
    assert received == ["hello"]


@pytest.mark.anyio
async def test_multiple_handlers() -> None:
    registry = HookRegistry()
    count: list[int] = []

    @registry.on("call_start")
    async def h1() -> None:
        count.append(1)

    @registry.on("call_start")
    async def h2() -> None:
        count.append(2)

    await registry.fire("call_start")
    assert count == [1, 2]


@pytest.mark.anyio
async def test_fire_with_kwargs() -> None:
    registry = HookRegistry()
    received: dict[str, str] = {}

    @registry.on("metric")
    async def handler(m: str) -> None:
        received["metric"] = m

    await registry.fire("metric", "some_metric")
    assert received["metric"] == "some_metric"


@pytest.mark.anyio
async def test_fire_no_handlers() -> None:
    registry = HookRegistry()
    # Should not raise
    await registry.fire("call_start")


def test_valid_event_names() -> None:
    assert "user_turn" in VALID_EVENTS
    assert "call_start" in VALID_EVENTS
    assert "call_end" in VALID_EVENTS
    assert "user_partial" in VALID_EVENTS
    assert "agent_turn" in VALID_EVENTS
    assert "tool_call" in VALID_EVENTS
    assert "tool_result" in VALID_EVENTS
    assert "silence" in VALID_EVENTS
    assert "interruption" in VALID_EVENTS
    assert "metric" in VALID_EVENTS


def test_invalid_event_raises() -> None:
    registry = HookRegistry()
    with pytest.raises(ValueError, match="Unknown event"):

        @registry.on("invalid_event")
        async def handler() -> None:
            pass


def test_metrics_tracker() -> None:
    from unpod.connectivity.metrics import MetricsTracker

    tracker = MetricsTracker()
    tracker.record_turn(
        stt_ms=180,
        llm_ms=320,
        tts_ms=200,
        cost_voice=0.25,
        cost_llm=0.01,
        tokens_in=100,
        tokens_out=50,
        llm="anthropic/claude-haiku-4-5",
    )
    m = tracker.live()
    assert m.turns == 1
    assert m.cost.total == pytest.approx(0.26)
    assert m.tokens.input == 100


def test_metrics_tracker_multiple_turns() -> None:
    from unpod.connectivity.metrics import MetricsTracker

    tracker = MetricsTracker()
    tracker.record_turn(
        stt_ms=180,
        llm_ms=320,
        tts_ms=200,
        cost_voice=0.25,
        cost_llm=0.01,
        tokens_in=100,
        tokens_out=50,
        llm="haiku",
    )
    tracker.record_turn(
        stt_ms=200,
        llm_ms=400,
        tts_ms=250,
        cost_voice=0.25,
        cost_llm=0.02,
        tokens_in=200,
        tokens_out=80,
        llm="haiku",
    )
    m = tracker.live()
    assert m.turns == 2
    assert m.tokens.input == 300
    assert m.tokens.output == 130
    assert m.cost.total == pytest.approx(0.53)
