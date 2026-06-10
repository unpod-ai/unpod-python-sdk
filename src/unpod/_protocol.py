"""Protocol frames for Unpod dispatch and bridge communication.

Mirrors the supervoice protocol as standalone Pydantic models
with no external dependency on supervoice.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Union

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Dispatch protocol frames (orchestrator <-> runner)
# ---------------------------------------------------------------------------


class Register(BaseModel):
    """Worker -> Orchestrator: register this worker."""

    type: Literal["register"] = "register"
    worker_id: str
    pool: str
    capabilities: dict


class Registered(BaseModel):
    """Orchestrator -> Worker: registration acknowledged."""

    type: Literal["registered"] = "registered"
    heartbeat_interval_s: int = 30


class Heartbeat(BaseModel):
    """Worker -> Orchestrator: periodic heartbeat."""

    type: Literal["heartbeat"] = "heartbeat"
    active_jobs: int


class Dispatch(BaseModel):
    """Orchestrator -> Worker: dispatch a job."""

    type: Literal["dispatch"] = "dispatch"
    job_id: str
    session_id: str
    room: dict
    voice_profile_id: str
    runner_url: str
    agent_secret: str
    metadata: dict


class DispatchAck(BaseModel):
    """Worker -> Orchestrator: acknowledge a dispatch."""

    type: Literal["dispatch.ack"] = "dispatch.ack"
    job_id: str
    accepted: bool
    reason: str | None = None


class StateChanged(BaseModel):
    """Worker -> Orchestrator: job state transition."""

    type: Literal["state.changed"] = "state.changed"
    job_id: str
    state: str


class JobCompleted(BaseModel):
    """Worker -> Orchestrator: job finished."""

    type: Literal["job.completed"] = "job.completed"
    job_id: str
    final_state: str
    duration_s: float


DispatchFrame = Union[
    Register,
    Registered,
    Heartbeat,
    Dispatch,
    DispatchAck,
    StateChanged,
    JobCompleted,
]

_DISPATCH_TYPE_MAP: dict[str, type[BaseModel]] = {
    "register": Register,
    "registered": Registered,
    "heartbeat": Heartbeat,
    "dispatch": Dispatch,
    "dispatch.ack": DispatchAck,
    "state.changed": StateChanged,
    "job.completed": JobCompleted,
}


def parse_dispatch_frame(raw: str) -> DispatchFrame:
    """Parse a JSON string into the appropriate dispatch frame."""
    data = json.loads(raw)
    frame_type = data.get("type")
    cls = _DISPATCH_TYPE_MAP.get(frame_type)  # type: ignore[arg-type]
    if cls is None:
        raise ValueError(f"Unknown dispatch frame type: {frame_type!r}")
    return cls.model_validate(data)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Bridge protocol (per-call WSS)
# ---------------------------------------------------------------------------

# -- Upstream (bridge -> session) --


class UserTextEvent(BaseModel):
    """User speech-to-text result."""

    event: Literal["user.text"] = "user.text"
    text: str
    is_final: bool = True


class UserInterruptEvent(BaseModel):
    """User interrupted the agent."""

    event: Literal["user.interrupted"] = "user.interrupted"


class ErrorEvent(BaseModel):
    """Error from bridge or pipeline."""

    event: Literal["error"] = "error"
    severity: str
    source: str
    code: str
    message: str


class MetricEvent(BaseModel):
    """Pipeline metrics snapshot."""

    model_config = {"extra": "allow"}

    event: Literal["metric"] = "metric"
    ttfa_ms: int | None = None
    asr_p95_ms: int | None = None
    tts_p95_ms: int | None = None
    turns: int = 0
    cost_usd_so_far: float | None = None


class TurnMetricsEvent(BaseModel):
    """Pipeline timing snapshot for one completed turn."""

    event: Literal["turn.metrics"] = "turn.metrics"
    call_id: str = ""
    turn_id: int = 0
    ttfa_ms: float | None = None
    asr_ms: float | None = None
    llm_ttft_ms: float | None = None
    tts_ttfb_ms: float | None = None
    llm_call_count: int = 0
    llm_total_ms: float | None = None
    from_node: str | None = None
    to_node: str | None = None


class StateEvent(BaseModel):
    """Conversation-state transition computed by the media worker.

    The worker is the single authority; ``state`` is one of
    ``idle | listening | thinking | speaking | interrupted``. Mirrors the
    ``metric`` path: the SDK surfaces it via the ``state`` hook. ``extra`` is
    allowed for forward-compatibility (e.g. ``call_id``).
    """

    model_config = {"extra": "allow"}

    event: Literal["state"] = "state"
    state: str = "idle"
    turn_id: int = 0


# -- Downstream (session -> bridge) --


class AgentTextDeltaEvent(BaseModel):
    """Streaming text chunk from agent."""

    event: Literal["agent.text.delta"] = "agent.text.delta"
    text: str


class AgentTextEndEvent(BaseModel):
    """End of agent text stream."""

    event: Literal["agent.text.end"] = "agent.text.end"


class AgentSayVerb(BaseModel):
    """Command: speak this text immediately."""

    event: Literal["agent.say"] = "agent.say"
    text: str


class AgentTransferVerb(BaseModel):
    """Command: transfer the call."""

    event: Literal["agent.transfer"] = "agent.transfer"
    transfer_type: str
    target: str
    mode: str = "cold"


class AgentEndCallVerb(BaseModel):
    """Command: end the call."""

    event: Literal["agent.end_call"] = "agent.end_call"
    reason: str


class AgentDispatchVerb(BaseModel):
    """Command: dispatch to another agent."""

    event: Literal["agent.dispatch"] = "agent.dispatch"
    agent_id: str
    metadata: dict = Field(default_factory=dict)


class AgentAddParticipantVerb(BaseModel):
    """Command: add a participant to the call."""

    event: Literal["agent.add_participant"] = "agent.add_participant"
    participant_type: str
    config: dict


class AgentRemoveParticipantVerb(BaseModel):
    """Command: remove a participant from the call."""

    event: Literal["agent.remove_participant"] = "agent.remove_participant"
    participant_id: str


class AgentMergeVerb(BaseModel):
    """Command: merge another session into this call."""

    event: Literal["agent.merge"] = "agent.merge"
    source_session_id: str


# -- Handshake --


class HelloEvent(BaseModel):
    """Session -> Bridge: protocol negotiation."""

    event: Literal["hello"] = "hello"
    protocol_version: str
    supported_events: list[str]
    supported_verbs: list[str]


class HelloAckEvent(BaseModel):
    """Bridge -> Session: negotiation acknowledgement."""

    event: Literal["hello.ack"] = "hello.ack"
    negotiated_events: list[str]
    negotiated_verbs: list[str]
    call_id: str
    session_id: str
    job_id: str
    room_id: str


class CallStartedEvent(BaseModel):
    """Bridge -> Session: a call has started; carries call metadata.

    Sent by the media agent after hello/hello.ack so the runner can
    build a CallContext for the per-call entrypoint.
    """

    event: Literal["call.started"] = "call.started"
    session_id: str
    job_id: str
    room_id: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    voice_profile_id: str | None = None


BridgeEvent = Union[
    UserTextEvent,
    UserInterruptEvent,
    ErrorEvent,
    MetricEvent,
    TurnMetricsEvent,
    StateEvent,
    AgentTextDeltaEvent,
    AgentTextEndEvent,
    AgentSayVerb,
    AgentTransferVerb,
    AgentEndCallVerb,
    AgentDispatchVerb,
    AgentAddParticipantVerb,
    AgentRemoveParticipantVerb,
    AgentMergeVerb,
    HelloEvent,
    HelloAckEvent,
    CallStartedEvent,
]

_BRIDGE_EVENT_MAP: dict[str, type[BaseModel]] = {
    "user.text": UserTextEvent,
    "user.interrupted": UserInterruptEvent,
    "error": ErrorEvent,
    "metric": MetricEvent,
    "turn.metrics": TurnMetricsEvent,
    "state": StateEvent,
    "agent.text.delta": AgentTextDeltaEvent,
    "agent.text.end": AgentTextEndEvent,
    "agent.say": AgentSayVerb,
    "agent.transfer": AgentTransferVerb,
    "agent.end_call": AgentEndCallVerb,
    "agent.dispatch": AgentDispatchVerb,
    "agent.add_participant": AgentAddParticipantVerb,
    "agent.remove_participant": AgentRemoveParticipantVerb,
    "agent.merge": AgentMergeVerb,
    "hello": HelloEvent,
    "hello.ack": HelloAckEvent,
    "call.started": CallStartedEvent,
}


def parse_bridge_event(raw: str) -> BridgeEvent:
    """Parse a JSON string into the appropriate bridge event."""
    data = json.loads(raw)
    event_type = data.get("event")
    cls = _BRIDGE_EVENT_MAP.get(event_type)  # type: ignore[arg-type]
    if cls is None:
        raise ValueError(f"Unknown bridge event type: {event_type!r}")
    return cls.model_validate(data)  # type: ignore[return-value]
