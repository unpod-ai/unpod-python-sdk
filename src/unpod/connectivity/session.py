"""Session — per-call object with controls, hooks, and dialog routing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Coroutine

from unpod._protocol import (
    AgentEndCallVerb,
    AgentSayVerb,
    AgentTextEndEvent,
    AgentTransferVerb,
    StateEvent,
    TurnMetricsEvent,
)
from unpod.adapters.base import DialogAdapter
from unpod.connectivity.hooks import HookRegistry
from unpod.connectivity.metrics import MetricsTracker
from unpod.connectivity.usage import UsageReporter
from unpod.observability import ObservabilityManager

if TYPE_CHECKING:
    from unpod.connectivity.bridge import BridgeClient


class RecordingControl:
    """Sub-object for session.recording.pause/resume."""

    def __init__(self, bridge: BridgeClient) -> None:
        self._bridge = bridge

    async def pause(self, reason: str = "") -> None:
        """Pause recording, optionally noting a reason (e.g. PII)."""
        await self._bridge.send_verb(AgentSayVerb(text=f"__recording_pause:{reason}"))

    async def resume(self) -> None:
        """Resume recording after a pause."""
        await self._bridge.send_verb(AgentSayVerb(text="__recording_resume"))


class Session:
    """Per-call session object providing controls, hooks, and metrics."""

    def __init__(self, ctx: Any = None, bridge: BridgeClient | None = None) -> None:
        if bridge is None:
            self._bridge = ctx
        else:
            self._bridge = bridge
        self._hooks = HookRegistry()
        self._metrics = MetricsTracker()
        self._dialog_adapter: DialogAdapter | None = None
        self._turn_counter = 0
        self._last_metric_event: Any | None = None
        self.data: dict[str, Any] = {}
        self.recording = RecordingControl(self._bridge)

        async def _fire_hook(event_name: str, **kwargs: Any) -> None:
            await self._hooks.fire(event_name, **kwargs)

        _session_id = getattr(self._bridge, "session_id", "") or ""
        self._obs = ObservabilityManager(
            session_id=_session_id,
            fire_hook=_fire_hook,
        )
        # SDK half of the usage ledger: buffers LLM tokens, flushed to the cloud
        # ingest on call_end. No-op unless UNPOD_USAGE_INGEST_URL is configured.
        self._usage = UsageReporter(_session_id)

    # --- Dialog machine property ---

    @property
    def dialog_machine(self) -> DialogAdapter | None:
        """Current dialog adapter, if set."""
        return self._dialog_adapter

    @dialog_machine.setter
    def dialog_machine(self, value: Any) -> None:
        """Set the dialog adapter, auto-wrapping superdialog types."""
        if _is_superdialog_type(value):
            from unpod.adapters.superdialog import SuperDialogAdapter

            self._dialog_adapter = SuperDialogAdapter(value)
        elif isinstance(value, DialogAdapter):
            self._dialog_adapter = value
        else:
            raise TypeError(
                f"Expected DialogAdapter or superdialog.DialogMachine, "
                f"got {type(value).__name__}"
            )

        if hasattr(self._dialog_adapter, "register_llm_callback"):

            async def _llm_cb(data: Any) -> None:
                await self._obs.record_llm_call(data)
                # Accumulate billable LLM tokens for the cloud usage ledger.
                self._usage.record_llm(
                    tokens_in=getattr(data, "tokens_in", 0),
                    tokens_out=getattr(data, "tokens_out", 0),
                    cached=getattr(data, "cached", 0),
                    cache_write=getattr(data, "cache_write", 0),
                    model=getattr(data, "model", "") or "",
                )

            self._dialog_adapter.register_llm_callback(_llm_cb)

    # --- Hook decorator ---

    def on(
        self, event: str
    ) -> Callable[
        [Callable[..., Coroutine[Any, Any, None]]],
        Callable[..., Coroutine[Any, Any, None]],
    ]:
        """Decorator to register a hook handler for a lifecycle event."""
        return self._hooks.on(event)

    # --- Metrics ---

    @property
    def metrics(self) -> MetricsTracker:
        """Access the per-call metrics tracker."""
        return self._metrics

    # --- Controls ---

    async def say(self, text: str) -> None:
        """Speak text immediately via the bridge."""
        await self._bridge.send_verb(AgentSayVerb(text=text))

    async def interrupt(self) -> None:
        """Interrupt the current agent utterance."""
        await self._bridge.send_verb(AgentTextEndEvent())

    async def set_filler(self, text: str) -> None:
        """Set a filler phrase hint for the bridge."""
        await self._bridge.send_verb(AgentSayVerb(text=f"__filler:{text}"))

    async def transfer(
        self,
        target: str,
        *,
        transfer_type: str = "number",
        mode: str = "cold",
        announcement: str = "",
    ) -> None:
        """Transfer the call to ``target`` (E.164 number, agent id, or queue).

        ``mode="warm"`` is an ANNOUNCED transfer, not a private consult: the
        human is dialed into the same room, so the caller hears the ringing
        and the ``announcement`` too. ``cold`` hands off immediately (SIP
        REFER when the trunk supports it). The worker enforces a destination
        allowlist before dialing; on success the agent's job ends.
        """
        await self._bridge.send_verb(
            AgentTransferVerb(
                transfer_type=transfer_type,
                target=target,
                mode=mode,
                announcement=announcement,
            )
        )

    async def transfer_to_human(
        self, queue: str, *, mode: str = "cold", announcement: str = ""
    ) -> None:
        """Transfer the call to a human queue."""
        await self.transfer(
            queue, transfer_type="human", mode=mode, announcement=announcement
        )

    async def transfer_to_agent(
        self, agent_id: str, *, mode: str = "cold", announcement: str = ""
    ) -> None:
        """Transfer the call to another agent."""
        await self.transfer(
            agent_id, transfer_type="agent", mode=mode, announcement=announcement
        )

    async def end(self, reason: str = "completed") -> None:
        """End the call with the given reason."""
        await self._bridge.send_verb(AgentEndCallVerb(reason=reason))

    async def _handle_passive_event(self, event: Any) -> bool:
        """Handle observability-only events (metric/state/turn-metrics).

        Safe to run at any point — including while a turn is streaming —
        because these never drive dialog. Returns True when handled.
        """
        from unpod._protocol import MetricEvent

        if isinstance(event, MetricEvent):
            self._last_metric_event = event
            await self._hooks.fire("metric", event)
            return True
        if isinstance(event, StateEvent):
            # Conversation state (idle/listening/thinking/speaking/
            # interrupted) from the media worker — observability only;
            # never drives dialog. Mirrors the metric branch.
            await self._hooks.fire("state", event)
            return True
        if isinstance(event, TurnMetricsEvent):
            await self._obs.record_pipeline_scores(
                turn_id=event.turn_id,
                ttfa_ms=event.ttfa_ms,
                asr_ms=event.asr_ms,
                llm_ttft_ms=event.llm_ttft_ms,
                tts_ttfb_ms=event.tts_ttfb_ms,
                from_node=event.from_node,
                to_node=event.to_node,
                llm_call_count=self._obs._turn_llm_calls,
                llm_total_ms=(
                    self._obs._turn_llm_total_ms
                    if self._obs._turn_llm_total_ms > 0
                    else None
                ),
            )
            return True
        return False

    async def _apply_interrupt(self, event: Any, *, mid_stream: bool) -> None:
        """Fire the interruption hook, truncate the brain's log, nudge.

        ``mid_stream=True`` means the turn's stream was just cancelled: the
        brain's last utterance is a partial the caller stopped hearing, so it
        is marked interrupted even without a ``heard_prefix`` (the tag alone
        keeps the Director honest about the cut). Read after the turn drained
        (serial path), truncation still requires ``heard_prefix`` — the
        already-shipped behavior.
        """
        await self._hooks.fire("interruption")
        heard = getattr(event, "heard_prefix", None)
        marker = getattr(self._dialog_adapter, "mark_interrupted", None)
        if callable(marker) and (heard is not None or mid_stream):
            marker(heard)
        if hasattr(self._dialog_adapter, "assist"):
            self._dialog_adapter.assist(
                "Note: The user just interrupted the agent mid-speech. "
                "Their next utterance may be incomplete or unclear. "
                "If what they say is ambiguous — do NOT repeat your previous statement. "
                "Ask them to repeat: e.g. 'Sorry, I didn't catch that — what were you saying?' "
                "/ 'माफ़ करें, आपकी बात समझ नहीं आई — आपने क्या कहा?' "
                "If their utterance IS clear — acknowledge it, answer, then continue from where you left off."
            )

    async def run(self) -> None:
        """Main loop: read bridge events, fire hooks, route to dialog.

        Blocks until call ends.

        While a turn is streaming, the loop selects between the next brain
        chunk and the next bridge event, so a ``UserInterruptEvent`` cancels
        the stream mid-flight (``aclose()`` propagates to the provider HTTP
        stream — no more late-delta re-speak) instead of waiting for the turn
        to drain. An in-flight ``recv_event`` is NEVER cancelled (a cancelled
        websocket read can lose an event); it is carried into the outer loop
        instead. Non-interrupt dialog events arriving mid-stream are deferred
        in order, preserving the serial loop's semantics.
        """
        import asyncio
        import contextlib
        from collections import deque

        from unpod._protocol import (
            AgentTextDeltaEvent,
            AgentTextEndEvent,
            ErrorEvent,
            UserInterruptEvent,
            UserTextEvent,
        )

        await self._hooks.fire("call_start")
        _ended_by_error: bool = False
        pending: deque[Any] = deque()  # dialog events deferred during a stream
        carried: asyncio.Task[Any] | None = None  # in-flight recv, never cancelled
        recv_failed = False
        try:
            while True:
                if pending:
                    event = pending.popleft()
                elif recv_failed:
                    break
                else:
                    try:
                        if carried is not None:
                            event = await carried
                            carried = None
                        else:
                            event = await self._bridge.recv_event()
                    except Exception:
                        break

                if await self._handle_passive_event(event):
                    continue

                if isinstance(event, UserTextEvent):
                    text = event.text
                    language = (getattr(event, "extra", None) or {}).get("language")
                    await self._hooks.fire("user_turn", text)
                    self._turn_counter += 1
                    if self._dialog_adapter is not None:
                        turn_id = self._turn_counter
                        self._obs.start_turn(turn_id, text)
                        full_text = ""
                        stream = self._dialog_adapter.stream(text, language=language)
                        chunk_task: asyncio.Task[Any] = asyncio.ensure_future(
                            stream.__anext__()
                        )
                        try:
                            while True:
                                if carried is None and not recv_failed:
                                    carried = asyncio.ensure_future(
                                        self._bridge.recv_event()
                                    )
                                wait_for = {chunk_task}
                                if carried is not None:
                                    wait_for.add(carried)
                                done, _ = await asyncio.wait(
                                    wait_for,
                                    return_when=asyncio.FIRST_COMPLETED,
                                )
                                if carried is not None and carried in done:
                                    try:
                                        ev = carried.result()
                                    except Exception:
                                        # Bridge gone: finish the turn, the
                                        # outer loop then exits (serial parity).
                                        recv_failed = True
                                        ev = None
                                    carried = None
                                    if ev is None:
                                        pass
                                    elif isinstance(ev, UserInterruptEvent):
                                        # Barge-in: stop the brain at the
                                        # provider, not just TTS locally.
                                        chunk_task.cancel()
                                        with contextlib.suppress(
                                            asyncio.CancelledError,
                                            StopAsyncIteration,
                                        ):
                                            await chunk_task
                                        with contextlib.suppress(Exception):
                                            await stream.aclose()
                                        await self._apply_interrupt(
                                            ev, mid_stream=True
                                        )
                                        break
                                    elif await self._handle_passive_event(ev):
                                        pass
                                    else:
                                        # UserText/Error/unknown: defer, in
                                        # order, to the outer loop.
                                        pending.append(ev)
                                if chunk_task in done and not chunk_task.cancelled():
                                    try:
                                        chunk = chunk_task.result()
                                    except StopAsyncIteration:
                                        break
                                    if chunk:
                                        full_text += chunk
                                        await self._bridge.send_verb(
                                            AgentTextDeltaEvent(text=chunk)
                                        )
                                    chunk_task = asyncio.ensure_future(
                                        stream.__anext__()
                                    )
                            # Sent exactly once per turn, interrupted or not —
                            # the worker's serial-era contract (it releases its
                            # turn gate on text.end).
                            await self._bridge.send_verb(AgentTextEndEvent())
                            if full_text:
                                await self._hooks.fire("agent_turn", full_text)
                        finally:
                            # Never leak the chunk task / generator on an
                            # exceptional exit (e.g. send_verb failing
                            # mid-drain); no-ops on the normal paths where
                            # both already finished.
                            if not chunk_task.done():
                                chunk_task.cancel()
                                with contextlib.suppress(
                                    asyncio.CancelledError, StopAsyncIteration
                                ):
                                    await chunk_task
                                with contextlib.suppress(Exception):
                                    await stream.aclose()
                            state = getattr(self._dialog_adapter, "state", {}) or {}
                            self._obs.end_turn(
                                full_text,
                                state.get("from_node", ""),
                                state.get("node_id", ""),
                            )
                            last_metric = self._last_metric_event
                            await self._obs.record_pipeline_scores(
                                turn_id=turn_id,
                                ttfa_ms=(
                                    last_metric.ttfa_ms
                                    if last_metric is not None
                                    else None
                                ),
                                asr_ms=None,
                                llm_ttft_ms=None,
                                tts_ttfb_ms=None,
                                from_node=state.get("from_node", ""),
                                to_node=state.get("node_id", ""),
                                llm_call_count=self._obs._turn_llm_calls,
                                llm_total_ms=(
                                    self._obs._turn_llm_total_ms
                                    if self._obs._turn_llm_total_ms > 0
                                    else None
                                ),
                            )
                        if (
                            hasattr(self._dialog_adapter, "is_complete")
                            and self._dialog_adapter.is_complete
                        ):
                            await self.end("completed")
                            break

                elif isinstance(event, UserInterruptEvent):
                    # Interrupt read between turns (or from an old serial
                    # queue): no stream to cancel; truncation still requires
                    # heard_prefix — the already-shipped 2.4 behavior.
                    await self._apply_interrupt(event, mid_stream=False)

                elif isinstance(event, ErrorEvent):
                    _ended_by_error = True
                    await self._hooks.fire(
                        "error",
                        event.code,
                        event.message,
                        event.severity,
                        event.source,
                    )
                    break
        finally:
            # A recv carried out of a turn that ended the session would leak
            # as a pending task; the session is over, so cancelling it (and
            # possibly losing that final event) is correct here.
            if carried is not None:
                carried.cancel()
                with contextlib.suppress(BaseException):
                    await carried
            await self._hooks.fire("call_end", "error" if _ended_by_error else "hangup")
            # Flush the session's accumulated LLM usage to the cloud ledger
            # (best-effort; no-op unless configured).
            await self._usage.flush()


def _is_superdialog_type(obj: Any) -> bool:
    """Check if obj is a superdialog DialogMachine or LLMAgent."""
    cls = obj.__class__
    module = getattr(cls, "__module__", "") or ""
    name = cls.__name__
    return module.startswith("superdialog") and name in (
        "DialogMachine",
        "LLMAgent",
    )
