"""Mid-stream barge-in cancellation in Session.run() (§2.3/§2.5).

These tests drive run() through a QUEUE-BACKED bridge whose recv_event
genuinely blocks until an event arrives — the real-bridge timing shape —
unlike the instantly-resolving AsyncMocks elsewhere, which is exactly the
pattern that made a naive stream-vs-recv race abort every normal turn.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest

from unpod._protocol import (
    AgentTextDeltaEvent,
    AgentTextEndEvent,
    MetricEvent,
    UserInterruptEvent,
    UserTextEvent,
)
from unpod.connectivity.session import Session

pytestmark = pytest.mark.anyio


class QueueBridge:
    """Bridge whose recv blocks on a queue — real event timing in-process."""

    def __init__(self) -> None:
        self._q: asyncio.Queue[Any] = asyncio.Queue()
        self.sent: list[Any] = []

    async def recv_event(self) -> Any:
        item = await self._q.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send_verb(self, verb: Any) -> None:
        self.sent.append(verb)

    def push(self, item: Any) -> None:
        self._q.put_nowait(item)

    def deltas(self) -> list[str]:
        return [v.text for v in self.sent if isinstance(v, AgentTextDeltaEvent)]

    def text_ends(self) -> int:
        return sum(1 for v in self.sent if isinstance(v, AgentTextEndEvent))


class SlowAdapter:
    """Streams chunks with a real await between them; records lifecycle."""

    is_complete = False

    def __init__(self, chunks: tuple[str, ...] = ("a ", "b ", "c"), delay=0.02):
        self._chunks = chunks
        self._delay = delay
        self.closed_early = False
        self.marked: list[Any] = []
        self.assists: list[str] = []
        self.streamed_turns: list[str] = []

    async def turn(self, text: str, context: dict | None = None) -> str:
        return "".join(self._chunks)

    async def stream(self, text, context=None, language=None):
        self.streamed_turns.append(text)
        emitted = 0
        try:
            for c in self._chunks:
                await asyncio.sleep(self._delay)
                yield c
                emitted += 1
        finally:
            if emitted < len(self._chunks):
                self.closed_early = True

    def mark_interrupted(self, heard=None) -> None:
        self.marked.append(heard)

    def assist(self, text: str) -> None:
        self.assists.append(text)


class GatedAdapter(SlowAdapter):
    """Yields one chunk, then blocks forever — only a cancel can end it."""

    def __init__(self) -> None:
        super().__init__()
        self._gate = asyncio.Event()  # never set

    async def stream(self, text, context=None, language=None):
        self.streamed_turns.append(text)
        try:
            yield "heard-part "
            await self._gate.wait()
            yield "never-spoken"
        finally:
            self.closed_early = True


async def _until(cond: Callable[[], bool], timeout: float = 2.0) -> None:
    for _ in range(int(timeout / 0.005)):
        if cond():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition not reached in time")


def _session(adapter: Any) -> tuple[Session, QueueBridge]:
    bridge = QueueBridge()
    session = Session(bridge=bridge)
    session.dialog_machine = adapter
    return session, bridge


async def test_midstream_interrupt_cancels_brain_stream() -> None:
    """A barge-in mid-stream aclose()s the generator (provider cancel),
    stops deltas, marks the log with the heard prefix, nudges, and still
    sends text.end exactly once."""
    adapter = GatedAdapter()
    session, bridge = _session(adapter)
    task = asyncio.create_task(session.run())

    bridge.push(UserTextEvent(text="hi"))
    await _until(lambda: "heard-part " in bridge.deltas())
    bridge.push(UserInterruptEvent(turn_id=1, heard_prefix="heard-p"))
    await _until(lambda: adapter.marked)

    bridge.push(Exception("hangup"))
    await asyncio.wait_for(task, timeout=2.0)

    assert adapter.closed_early is True  # generator finally ran (cancelled)
    assert bridge.deltas() == ["heard-part "]  # nothing after the interrupt
    assert adapter.marked == ["heard-p"]
    assert len(adapter.assists) == 1  # legacy nudge still fires
    assert bridge.text_ends() == 1  # worker turn-gate contract preserved


async def test_midstream_interrupt_without_heard_prefix_still_cancels() -> None:
    """Old worker (no heard_prefix): the stream is still cancelled and the
    partial is tagged interrupted (marker called with None)."""
    adapter = GatedAdapter()
    session, bridge = _session(adapter)
    task = asyncio.create_task(session.run())

    bridge.push(UserTextEvent(text="hi"))
    await _until(lambda: bridge.deltas())
    bridge.push(UserInterruptEvent())
    await _until(lambda: adapter.marked)
    bridge.push(Exception("hangup"))
    await asyncio.wait_for(task, timeout=2.0)

    assert adapter.closed_early is True
    assert adapter.marked == [None]


async def test_normal_turns_never_aborted_by_blocking_recv() -> None:
    """The original hazard: with recv genuinely blocking, consecutive normal
    turns stream to completion — nothing treats quiet recv as an interrupt."""
    adapter = SlowAdapter()
    session, bridge = _session(adapter)
    bridge.push(UserTextEvent(text="one"))
    bridge.push(UserTextEvent(text="two"))
    bridge.push(Exception("hangup"))

    await asyncio.wait_for(session.run(), timeout=5.0)

    assert adapter.streamed_turns == ["one", "two"]
    assert bridge.deltas() == ["a ", "b ", "c", "a ", "b ", "c"]
    assert bridge.text_ends() == 2
    assert adapter.closed_early is False
    assert adapter.marked == []


async def test_passive_metric_midstream_does_not_abort_turn() -> None:
    metrics: list[Any] = []
    adapter = SlowAdapter()
    session, bridge = _session(adapter)

    @session.on("metric")
    async def _(event: Any) -> None:
        metrics.append(event)

    task = asyncio.create_task(session.run())
    bridge.push(UserTextEvent(text="one"))
    await _until(lambda: bridge.deltas())
    bridge.push(MetricEvent(ttfa_ms=42))
    await _until(lambda: metrics)  # handled while the turn is still streaming
    assert bridge.text_ends() == 0
    await _until(lambda: bridge.text_ends() == 1)
    bridge.push(Exception("hangup"))
    await asyncio.wait_for(task, timeout=2.0)

    assert bridge.deltas() == ["a ", "b ", "c"]  # turn unharmed
    assert adapter.closed_early is False


async def test_user_text_midstream_is_deferred_in_order() -> None:
    """A next user turn arriving mid-stream waits for the current turn."""
    adapter = SlowAdapter(delay=0.03)
    session, bridge = _session(adapter)
    task = asyncio.create_task(session.run())
    bridge.push(UserTextEvent(text="one"))
    await _until(lambda: bridge.deltas())
    bridge.push(UserTextEvent(text="two"))  # mid-stream of turn one
    await _until(lambda: bridge.text_ends() == 2, timeout=5.0)
    bridge.push(Exception("hangup"))
    await asyncio.wait_for(task, timeout=2.0)

    assert adapter.streamed_turns == ["one", "two"]
    # Turn one's three deltas all precede turn two's.
    assert bridge.deltas() == ["a ", "b ", "c", "a ", "b ", "c"]


async def test_bridge_death_midstream_drains_turn_then_exits() -> None:
    ended: list[str] = []
    adapter = SlowAdapter()
    session, bridge = _session(adapter)

    @session.on("call_end")
    async def _(reason: str) -> None:
        ended.append(reason)

    task = asyncio.create_task(session.run())
    bridge.push(UserTextEvent(text="one"))
    await _until(lambda: bridge.deltas())
    bridge.push(Exception("connection lost"))  # bridge dies mid-turn

    await asyncio.wait_for(task, timeout=5.0)

    assert bridge.deltas() == ["a ", "b ", "c"]  # turn drained (serial parity)
    assert bridge.text_ends() == 1
    assert ended == ["hangup"]


async def test_interrupt_between_turns_keeps_serial_semantics() -> None:
    """Not mid-stream: truncation requires heard_prefix (shipped 2.4 rule)."""
    adapter = SlowAdapter()
    session, bridge = _session(adapter)
    bridge.push(UserInterruptEvent())  # no heard_prefix, no stream in flight
    bridge.push(Exception("hangup"))

    await asyncio.wait_for(session.run(), timeout=2.0)

    assert adapter.marked == []  # no truncation without heard_prefix
    assert len(adapter.assists) == 1  # nudge only
