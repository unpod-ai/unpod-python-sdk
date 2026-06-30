"""Session — per-call object with controls, hooks, and dialog routing."""

from __future__ import annotations

import asyncio
import contextlib
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
        # A2a: a speculative draft has been streamed (held by the worker) and is
        # awaiting its committed turn to release it.
        self._draft_in_flight = False
        # A2b revise-on-divergence: the draft now streams in a background task so
        # the run loop keeps reading events and can abort a stale draft when a
        # newer partial arrives. _draft_grounded is the text that draft assumed —
        # if the committed turn matches it the draft is released, else retracted.
        self._draft_task: asyncio.Task[None] | None = None
        self._draft_grounded: str | None = None
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

    async def transfer_to_human(self, queue: str) -> None:
        """Cold-transfer the call to a human queue."""
        await self._bridge.send_verb(
            AgentTransferVerb(transfer_type="human", target=queue, mode="cold")
        )

    async def transfer_to_agent(self, agent_id: str) -> None:
        """Cold-transfer the call to another agent."""
        await self._bridge.send_verb(
            AgentTransferVerb(transfer_type="agent", target=agent_id, mode="cold")
        )

    async def end(self, reason: str = "completed") -> None:
        """End the call with the given reason."""
        await self._bridge.send_verb(AgentEndCallVerb(reason=reason))

    async def _relay_draft(self, text: str, language: str | None) -> None:
        """Stream a speculative draft reply and relay HELD draft deltas (A2a).

        Runs as a background task (started by ``_start_draft``) so the run loop
        can keep reading events and abort this draft when a newer partial lands
        (A2b). Calls the adapter's optional ``draft_stream`` (superdialog's
        ``draft_turn`` — no log, no tools, no obs); relays each chunk as a
        draft-tagged ``agent.text.delta`` the worker buffers without speaking.
        Each send is shielded so a supersede-cancel never tears a half-written WS
        frame — the cancel lands cleanly at the next chunk boundary instead.
        No-op when the adapter has no draft surface (e.g. the legacy DialogMachine
        path), so a partial there is silently ignored rather than crashing.
        """
        from unpod._protocol import AgentTextDeltaEvent

        adapter = self._dialog_adapter
        if adapter is None or not hasattr(adapter, "draft_stream"):
            return
        try:
            async for chunk in adapter.draft_stream(text, language=language):
                if chunk:
                    await asyncio.shield(
                        self._bridge.send_verb(
                            AgentTextDeltaEvent(text=chunk, draft=True)
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            # A draft is best-effort speculation; a failure must never crash the
            # session loop. The committed turn still runs normally.
            pass

    def _start_draft(self, text: str, language: str | None) -> None:
        """Begin a speculative draft for ``text`` in a cancellable background task.

        No-op when the adapter has no draft surface (legacy DialogMachine path) —
        a partial there is ignored, never flipping ``_draft_in_flight``.
        """
        adapter = self._dialog_adapter
        if adapter is None or not hasattr(adapter, "draft_stream"):
            return
        self._draft_grounded = text
        self._draft_in_flight = True
        self._draft_task = asyncio.create_task(self._relay_draft(text, language))

    async def _drain_draft(self) -> None:
        """Await the in-flight draft to completion (committed text matched it).

        Leaves ``_draft_in_flight`` set so the committed turn below suppresses its
        own deltas and the worker speaks this (now fully buffered) draft on commit.
        """
        task, self._draft_task = self._draft_task, None
        self._draft_grounded = None
        if task is not None:
            with contextlib.suppress(Exception):
                await task

    async def _abort_draft(self, turn_id: int) -> None:
        """Cancel a stale in-flight draft and tell the worker to drop it (A2b).

        Cancels the streaming task (shielded sends drain cleanly), then emits
        ``agent.draft.retract`` so the worker clears its held buffer. After this
        the committed stream speaks fresh. Idempotent: a no-op when no draft is
        in flight.
        """
        if not self._draft_in_flight:
            return
        from unpod._protocol import AgentDraftRetractVerb

        task, self._draft_task = self._draft_task, None
        self._draft_grounded = None
        self._draft_in_flight = False
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        await self._bridge.send_verb(AgentDraftRetractVerb(turn_id=turn_id))

    async def run(self) -> None:
        """Main loop: read bridge events, fire hooks, route to dialog.

        Blocks until call ends.
        """
        from unpod._protocol import (
            AgentDraftCommitVerb,
            AgentTextDeltaEvent,
            AgentTextEndEvent,
            ErrorEvent,
            UserInterruptEvent,
            UserTextEvent,
        )

        await self._hooks.fire("call_start")
        _ended_by_error: bool = False
        try:
            while True:
                try:
                    event = await self._bridge.recv_event()
                except Exception:
                    break

                from unpod._protocol import MetricEvent

                if isinstance(event, MetricEvent):
                    self._last_metric_event = event
                    await self._hooks.fire("metric", event)
                    continue

                if isinstance(event, StateEvent):
                    # Conversation state (idle/listening/thinking/speaking/
                    # interrupted) from the media worker — observability only;
                    # never drives dialog. Mirrors the metric branch.
                    await self._hooks.fire("state", event)
                    continue

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
                    continue

                if isinstance(event, UserTextEvent):
                    text = event.text
                    language = (getattr(event, "extra", None) or {}).get("language")
                    # A2a/A2b speculative draft: a non-final partial drafts a reply
                    # the worker HOLDS (draft-tagged deltas, no TTS) until commit.
                    # No hook, no obs, no turn counter — a draft must leave zero
                    # trace (mirrors superdialog draft_turn). A2b: a newer partial
                    # supersedes — abort the stale draft (cancel + retract so the
                    # worker drops it) before drafting afresh.
                    if not event.is_final:
                        await self._abort_draft(self._turn_counter + 1)
                        self._start_draft(text, language)
                        continue
                    # Commit boundary. If a draft is held and the committed text
                    # matches what it assumed, drain + release it (A2a path). If it
                    # diverged, retract it and speak the committed stream fresh
                    # (A2b revise-on-divergence). Exact-string compare is the
                    # cheapest divergence metric (design §11).
                    if self._draft_in_flight:
                        if self._draft_grounded == text:
                            await self._drain_draft()
                        else:
                            await self._abort_draft(self._turn_counter + 1)
                    await self._hooks.fire("user_turn", text)
                    self._turn_counter += 1
                    if self._dialog_adapter is not None:
                        turn_id = self._turn_counter
                        self._obs.start_turn(turn_id, text)
                        full_text = ""
                        try:
                            async for chunk in self._dialog_adapter.stream(
                                text, language=language
                            ):
                                if chunk:
                                    full_text += chunk
                                    # A2a release-on-final: when a draft is held,
                                    # the worker speaks THAT on commit, so the
                                    # committed text is suppressed here — the
                                    # committed turn still runs to advance state +
                                    # fire tools. ponytail: divergence-blind (speaks
                                    # the draft even if it diverged from the final);
                                    # the prefix/redraft guard is A2b.
                                    if not self._draft_in_flight:
                                        await self._bridge.send_verb(
                                            AgentTextDeltaEvent(text=chunk)
                                        )
                            # Commit BEFORE end so the worker opens its response
                            # from the held draft, then end closes it.
                            if self._draft_in_flight:
                                self._draft_in_flight = False
                                await self._bridge.send_verb(
                                    AgentDraftCommitVerb(turn_id=turn_id)
                                )
                            await self._bridge.send_verb(AgentTextEndEvent())
                            if full_text:
                                await self._hooks.fire("agent_turn", full_text)
                        finally:
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
                    # A barge-in kills any held draft — it grounds in speech the
                    # user has now overridden. Cancel the stream (stop wasting LLM)
                    # and retract (the worker also clears locally; retract is
                    # idempotent).
                    await self._abort_draft(self._turn_counter + 1)
                    await self._hooks.fire("interruption")
                    if hasattr(self._dialog_adapter, "assist"):
                        self._dialog_adapter.assist(
                            "Note: The user just interrupted the agent mid-speech. "
                            "Their next utterance may be incomplete or unclear. "
                            "If what they say is ambiguous — do NOT repeat your previous statement. "
                            "Ask them to repeat: e.g. 'Sorry, I didn't catch that — what were you saying?' "
                            "/ 'माफ़ करें, आपकी बात समझ नहीं आई — आपने क्या कहा?' "
                            "If their utterance IS clear — acknowledge it, answer, then continue from where you left off."
                        )

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
            # Don't leak a draft task if the loop exits mid-draft (call end).
            if self._draft_task is not None and not self._draft_task.done():
                self._draft_task.cancel()
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
