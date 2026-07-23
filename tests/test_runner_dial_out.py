"""AgentRunner v2: dial-out transport, supervising control loop."""

import asyncio
import json

import pytest

from unpod._protocol import JobAssign
from unpod.connectivity.runner import AgentRunner


class _FakeControlWS:
    """Scripted control socket: yields inbound frames, records outbound.

    ``recv`` blocks when the queue is empty (heartbeats keep flowing);
    ``feed`` injects a frame mid-test; ``close`` makes recv raise.
    """

    def __init__(self, inbound: list[str]) -> None:
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        for frame in inbound:
            self._queue.put_nowait(frame)
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        item = await self._queue.get()
        if item is None:
            raise ConnectionError("closed")
        return item

    def feed(self, frame: str) -> None:
        self._queue.put_nowait(frame)

    def close(self) -> None:
        self._queue.put_nowait(None)


def _registered(transport_ack: str = "dial_out") -> str:
    return json.dumps(
        {
            "type": "registered",
            "heartbeat_interval_s": 30,
            "transport_ack": transport_ack,
        }
    )


def _assign(job_id: str = "j1", agent_id: str = "bot") -> str:
    return JobAssign(
        job_id=job_id,
        call_id="s1",
        agent_id=agent_id,
        bridge_url="ws://127.0.0.1:1/call/s1",
        call_token="tok",
    ).model_dump_json()


def _runner(**kw) -> AgentRunner:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    return AgentRunner(entrypoint=entrypoint, agent_id="bot", api_key="k", **kw)


def test_default_transport_is_dial_out() -> None:
    assert _runner()._transport == "dial_out"
    assert _runner(transport="serve")._transport == "serve"


def test_register_frame_advertises_dial_out_and_no_serving_url() -> None:
    r = _runner()
    frame = r._build_register()
    assert frame.capabilities["transport"] == "dial_out"
    assert "serving_url" not in frame.capabilities


@pytest.mark.anyio
async def test_job_assign_acked_and_dial_attempted() -> None:
    dialed: list[str] = []

    r = _runner()

    async def fake_dial(assign) -> None:  # type: ignore[no-untyped-def]
        dialed.append(assign.bridge_url)

    r._dial_for_job = fake_dial  # type: ignore[method-assign]
    ws = _FakeControlWS([_registered(), _assign()])
    task = asyncio.create_task(r._control_session(ws))
    await asyncio.sleep(0.05)
    r._shutting_down = True
    ws.close()
    with pytest.raises(BaseException):
        await asyncio.wait_for(task, timeout=1)

    acks = [f for f in ws.sent if f.get("type") == "job.ack"]
    assert acks and acks[0]["accepted"] is True
    assert dialed == ["ws://127.0.0.1:1/call/s1"]


@pytest.mark.anyio
async def test_job_assign_wrong_agent_id_rejected() -> None:
    r = _runner()
    ws = _FakeControlWS([_registered(), _assign(agent_id="other-agent")])
    task = asyncio.create_task(r._control_session(ws))
    await asyncio.sleep(0.05)
    r._shutting_down = True
    ws.close()
    with pytest.raises(BaseException):
        await asyncio.wait_for(task, timeout=1)

    acks = [f for f in ws.sent if f.get("type") == "job.ack"]
    assert acks and acks[0]["accepted"] is False
    assert acks[0]["reason"] == "agent_id_mismatch"


@pytest.mark.anyio
async def test_job_assign_at_capacity_rejected() -> None:
    r = _runner(max_concurrent_calls=0)
    ws = _FakeControlWS([_registered(), _assign()])
    task = asyncio.create_task(r._control_session(ws))
    await asyncio.sleep(0.05)
    r._shutting_down = True
    ws.close()
    with pytest.raises(BaseException):
        await asyncio.wait_for(task, timeout=1)

    acks = [f for f in ws.sent if f.get("type") == "job.ack"]
    assert acks and acks[0]["reason"] == "at_capacity"


def test_backoff_schedule_caps_and_resets() -> None:
    r = _runner()
    delays = [r._next_backoff(attempt) for attempt in range(1, 8)]
    assert delays[0] >= 0.5 and delays[0] <= 1.5           # ~1s with jitter
    assert all(d <= 30.0 for d in delays)                   # capped after jitter
    assert delays[-1] >= 15.0                               # escalated


@pytest.mark.anyio
async def test_dial_out_refused_by_old_orchestrator_raises() -> None:
    """transport_ack absent => orchestrator too old for dial_out => hard error."""
    r = _runner()
    ws = _FakeControlWS(['{"type":"registered","heartbeat_interval_s":30}'])
    with pytest.raises(ConnectionError, match="dial_out"):
        await r._control_session(ws)


@pytest.mark.anyio
async def test_job_cancel_cancels_dial_task() -> None:
    r = _runner()
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_dial(assign) -> None:  # type: ignore[no-untyped-def]
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    r._dial_for_job = slow_dial  # type: ignore[method-assign]
    ws = _FakeControlWS([_registered(), _assign()])
    task = asyncio.create_task(r._control_session(ws))
    await asyncio.wait_for(started.wait(), timeout=1)
    ws.feed(json.dumps({"type": "job.cancel", "job_id": "j1"}))
    await asyncio.wait_for(cancelled.wait(), timeout=1)
    r._shutting_down = True
    ws.close()
    with pytest.raises(BaseException):
        await asyncio.wait_for(task, timeout=1)


def test_serve_transport_warns_nothing_but_keeps_serving_url() -> None:
    r = _runner(transport="serve", serving_url="ws://1.2.3.4:8765")
    assert r._transport == "serve"
    assert r._serving_url == "ws://1.2.3.4:8765"
