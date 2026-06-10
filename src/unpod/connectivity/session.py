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

        self._obs = ObservabilityManager(
            session_id=getattr(self._bridge, "session_id", "") or "",
            fire_hook=_fire_hook,
        )

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

    async def run(self) -> None:
        """Main loop: read bridge events, fire hooks, route to dialog.

        Blocks until call ends.
        """
        from unpod._protocol import (
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
                    await self._hooks.fire("user_turn", text)
                    self._turn_counter += 1
                    if self._dialog_adapter is not None:
                        turn_id = self._turn_counter
                        self._obs.start_turn(turn_id, text)
                        full_text = ""
                        try:
                            async for chunk in self._dialog_adapter.stream(text):
                                if chunk:
                                    full_text += chunk
                                    await self._bridge.send_verb(
                                        AgentTextDeltaEvent(text=chunk)
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
                    await self._hooks.fire("interruption")

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
            await self._hooks.fire("call_end", "error" if _ended_by_error else "hangup")


def _is_superdialog_type(obj: Any) -> bool:
    """Check if obj is a superdialog DialogMachine or LLMAgent."""
    cls = obj.__class__
    module = getattr(cls, "__module__", "") or ""
    name = cls.__name__
    return module.startswith("superdialog") and name in (
        "DialogMachine",
        "LLMAgent",
    )
