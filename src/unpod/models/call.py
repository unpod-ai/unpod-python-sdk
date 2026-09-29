"""Call models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class Call(BaseModel):
    """A voice call (matches sv_calls response shape)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    call_id: str = Field(validation_alias=AliasChoices("id", "call_id"))
    org_id: str | None = None
    project_id: str = ""
    user_id: str | None = None
    pipe_id: str | None = None
    agent_id: str | None = None
    direction: str = "outbound"
    from_number: str | None = None
    to_number: str | None = Field(
        default=None, validation_alias=AliasChoices("to_number", "user_number")
    )
    number_id: str | None = None
    trunk_id: str | None = None
    provider_trunk_id: str | None = None
    session_id: str | None = None
    room_name: str | None = None
    instructions: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    scheduled_at: datetime | None = None
    ended_at: datetime | None = None
    duration_s: float | None = None
    #: In flight: scheduled / pending / dispatching / ringing / active.
    #: Terminal — the LiveKit agent's CallEndStatus values: ``connected``,
    #: ``notConnected`` or ``failed`` (older servers: completed / not_connected).
    status: str = "pending"
    #: A LiveKit ``CallEndReason`` (USER_DID_NOT_PICK_UP, USER_HUNG_UP_IN_CALL…).
    end_reason: str | None = None
    disposition: str | None = None
    recording_url: str | None = None
    #: The follow-up chain's first call, for an automatically scheduled one.
    initial_call_id: str | None = None
    #: Per-call latency roll-up, and (detail reads) the per-turn rows behind it.
    latency_summary: dict[str, Any] | None = None
    latency_metrics: list[dict[str, Any]] | None = None
    #: What the attached analytics blocks extracted (detail reads).
    analytics: list[dict[str, Any]] = Field(default_factory=list)
    #: Free-form metrics snapshot: worker usage, ``call_timings`` and
    #: ``supervoice_end_reason`` (the code ``end_reason`` was translated from).
    traces: dict[str, Any] = Field(default_factory=dict)
    # Conversation turns ({role, content, timestamp}). Populated on
    # ``calls.get(call_id)``; the list endpoint projects it out, so a Call from
    # ``None`` means NOT LOADED, ``[]`` means the call genuinely has no turns.
    # ``calls.list()`` projects the turns out so a page stays small, and the old
    # ``[]`` default made that indistinguishable from a silent call. Use
    # ``transcript_turns`` to see whether there is anything to fetch, and
    # ``calls.get(call_id)`` to fetch it.
    transcript: list[dict[str, Any]] | None = None
    #: Turn count, present on list rows as well as detail reads.
    transcript_turns: int = 0
    created: datetime | None = None
    modified: datetime | None = None

    @property
    def id(self) -> str:
        return self.call_id

    @property
    def call_timings(self) -> dict[str, Any]:
        """Dial / connect / end, in the LiveKit agent's shape: ``call_dialed_at``,
        ``call_connected_at``, ``call_ended_at`` (ISO-8601, to the second),
        ``ring_duration_seconds`` and ``talk_duration_seconds``. ``{}`` when the
        server predates it."""
        return (self.traces or {}).get("call_timings") or {}

    @property
    def user_number(self) -> str | None:
        return self.to_number


class CallCreate(BaseModel):
    """Request to initiate an outbound call."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pipe_id: str = Field(default="", alias="agent")
    to_number: str = Field(default="", alias="user_number")
    from_number: str | None = None
    instructions: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def agent(self) -> str:
        return self.pipe_id

    @property
    def user_number(self) -> str:
        return self.to_number
