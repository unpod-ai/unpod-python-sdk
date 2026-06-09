"""Tests for the per-call bridge server handler."""

from __future__ import annotations

import asyncio

import pytest
from unpod.connectivity.bridge_server import handle_bridge_connection


class _FakeWS:
    """In-memory duplex websocket stand-in for tests."""

    def __init__(self, inbound: list[str]) -> None:
        self._inbound = inbound
        self.sent: list[str] = []
        self.closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    async def recv(self) -> str:
        if not self._inbound:
            raise StopAsyncIteration
        return self._inbound.pop(0)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_handler_runs_entrypoint_with_call_started_context() -> None:
    seen: dict[str, object] = {}

    async def entrypoint(ctx) -> None:  # type: ignore[no-untyped-def]
        seen["session_id"] = ctx.session_id
        seen["user_number"] = ctx.user_number
        seen["direction"] = ctx.direction
        seen["agent_id"] = ctx.agent_id

    ws = _FakeWS(
        inbound=[
            '{"event":"hello.ack","negotiated_events":[],"negotiated_verbs":[],'
            '"call_id":"s1","session_id":"s1","job_id":"j1","room_id":"s1"}',
            '{"event":"call.started","session_id":"s1","job_id":"j1",'
            '"room_id":"s1","metadata":{"user_number":"+91-x",'
            '"direction":"inbound"}}',
        ]
    )

    await handle_bridge_connection(ws, entrypoint=entrypoint, agent_id="ag-x")

    assert seen["session_id"] == "s1"
    assert seen["user_number"] == "+91-x"
    assert seen["direction"] == "inbound"
    assert seen["agent_id"] == "ag-x"
    assert any('"event":"hello"' in m for m in ws.sent)
    assert ws.closed is True


@pytest.mark.anyio
async def test_handler_advertises_turn_metrics() -> None:
    """The bridge hello must advertise turn.metrics so workers emit per-turn traces."""
    async def entrypoint(ctx) -> None:  # type: ignore[no-untyped-def]
        pass

    ws = _FakeWS(
        inbound=[
            '{"event":"hello.ack","negotiated_events":[],"negotiated_verbs":[],'
            '"call_id":"s1","session_id":"s1","job_id":"j1","room_id":"s1"}',
            '{"event":"call.started","session_id":"s1","job_id":"j1",'
            '"room_id":"s1","metadata":{}}',
        ]
    )

    await handle_bridge_connection(ws, entrypoint=entrypoint, agent_id="ag-x")

    hello = next(raw for raw in ws.sent if '"event":"hello"' in raw)
    assert '"turn.metrics"' in hello


@pytest.mark.anyio
async def test_handler_skips_when_verify_returns_false() -> None:
    called = {"entrypoint": False}

    async def entrypoint(ctx) -> None:  # type: ignore[no-untyped-def]
        called["entrypoint"] = True

    ws = _FakeWS(inbound=[])

    await handle_bridge_connection(
        ws,
        entrypoint=entrypoint,
        agent_id="ag-x",
        verify=lambda _ws: False,
    )

    assert called["entrypoint"] is False
    assert ws.closed is True
    assert ws.sent == []


@pytest.mark.anyio
async def test_handler_session_bound_can_say() -> None:
    """The session bound to the connection sends verbs over ws."""
    sent_verbs: list[str] = []

    async def entrypoint(ctx) -> None:  # type: ignore[no-untyped-def]
        await ctx.session.say("Hello there")

    ws = _FakeWS(
        inbound=[
            '{"event":"hello.ack","negotiated_events":[],"negotiated_verbs":[],'
            '"call_id":"s1","session_id":"s1","job_id":"j1","room_id":"s1"}',
            '{"event":"call.started","session_id":"s1","job_id":"j1",'
            '"room_id":"s1","metadata":{}}',
        ]
    )

    await handle_bridge_connection(ws, entrypoint=entrypoint, agent_id="ag-x")

    sent_verbs = [m for m in ws.sent if '"agent.say"' in m]
    assert any("Hello there" in m for m in sent_verbs)


@pytest.mark.anyio
async def test_handler_fires_async_callbacks_with_final_state() -> None:
    """on_call_start/on_call_end are awaited; on_call_end receives the
    'ended' final_state for a normal entrypoint."""
    events: list[tuple[str, object]] = []

    async def entrypoint(ctx) -> None:  # type: ignore[no-untyped-def]
        pass

    async def on_start(ctx) -> None:  # type: ignore[no-untyped-def]
        events.append(("start", ctx))

    async def on_end(ctx, final_state: str) -> None:  # type: ignore[no-untyped-def]
        events.append(("end", final_state))

    ws = _FakeWS(
        inbound=[
            '{"event":"hello.ack","negotiated_events":[],"negotiated_verbs":[],'
            '"call_id":"s1","session_id":"s1","job_id":"j1","room_id":"s1"}',
            '{"event":"call.started","session_id":"s1","job_id":"j1",'
            '"room_id":"s1","metadata":{}}',
        ]
    )

    await handle_bridge_connection(
        ws,
        entrypoint=entrypoint,
        agent_id="ag-x",
        on_call_start=on_start,
        on_call_end=on_end,
    )

    assert events[0][0] == "start"
    assert events[-1] == ("end", "ended")


@pytest.mark.anyio
async def test_handler_on_call_end_failed_when_entrypoint_raises() -> None:
    """When the entrypoint raises, on_call_end still fires with 'failed'
    and the exception propagates to the caller."""
    ended: list[str] = []

    async def entrypoint(ctx) -> None:  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    async def on_end(ctx, final_state: str) -> None:  # type: ignore[no-untyped-def]
        ended.append(final_state)

    ws = _FakeWS(
        inbound=[
            '{"event":"hello.ack","negotiated_events":[],"negotiated_verbs":[],'
            '"call_id":"s1","session_id":"s1","job_id":"j1","room_id":"s1"}',
            '{"event":"call.started","session_id":"s1","job_id":"j1",'
            '"room_id":"s1","metadata":{}}',
        ]
    )

    with pytest.raises(RuntimeError, match="boom"):
        await handle_bridge_connection(
            ws, entrypoint=entrypoint, agent_id="ag-x", on_call_end=on_end
        )

    assert ended == ["failed"]
    assert ws.closed is True


@pytest.mark.anyio
async def test_handler_no_callbacks_on_pre_call_abort() -> None:
    """A wrong event before call.started builds no context, so neither
    callback fires and the entrypoint never runs."""
    fired: list[str] = []

    async def entrypoint(ctx) -> None:  # type: ignore[no-untyped-def]
        fired.append("entrypoint")

    async def on_start(ctx) -> None:  # type: ignore[no-untyped-def]
        fired.append("start")

    async def on_end(ctx, final_state: str) -> None:  # type: ignore[no-untyped-def]
        fired.append("end")

    ws = _FakeWS(
        inbound=[
            '{"event":"hello.ack","negotiated_events":[],"negotiated_verbs":[],'
            '"call_id":"s1","session_id":"s1","job_id":"j1","room_id":"s1"}',
            '{"event":"user.text","text":"hi"}',
        ]
    )

    await handle_bridge_connection(
        ws,
        entrypoint=entrypoint,
        agent_id="ag-x",
        on_call_start=on_start,
        on_call_end=on_end,
    )

    assert fired == []
    assert ws.closed is True


@pytest.mark.anyio
async def test_handler_handshake_timeout_closes_without_hang() -> None:
    """A client that never sends call.started is dropped on timeout."""
    fired: list[str] = []

    async def entrypoint(ctx) -> None:  # type: ignore[no-untyped-def]
        fired.append("entrypoint")

    async def on_end(ctx, final_state: str) -> None:  # type: ignore[no-untyped-def]
        fired.append("end")

    class _HangingWS(_FakeWS):
        async def recv(self) -> str:
            if self._inbound:
                return self._inbound.pop(0)
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    ws = _HangingWS(
        inbound=[
            '{"event":"hello.ack","negotiated_events":[],"negotiated_verbs":[],'
            '"call_id":"s1","session_id":"s1","job_id":"j1","room_id":"s1"}',
        ]
    )

    async with asyncio.timeout(2.0):
        await handle_bridge_connection(
            ws,
            entrypoint=entrypoint,
            agent_id="ag-x",
            on_call_end=on_end,
            handshake_timeout_s=0.05,
        )

    assert fired == []
    assert ws.closed is True
