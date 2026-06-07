"""Session models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CostBreakdown(BaseModel):
    """Cost breakdown for a call."""

    voice: float = 0.0
    llm: float = 0.0
    total: float = 0.0


class TokenUsage(BaseModel):
    """Token usage for a call."""

    input: int = 0
    output: int = 0


class CallMetrics(BaseModel):
    """Aggregated metrics for a call session."""

    duration_s: float = 0.0
    turns: int = 0
    stt_p95_ms: int = 0
    llm_p95_ms: int = 0
    tts_p95_ms: int = 0
    cost: CostBreakdown = Field(default_factory=CostBreakdown)
    tokens: TokenUsage = Field(default_factory=TokenUsage)
    active_llm: str = ""


class RunnerStats(BaseModel):
    """Snapshot of AgentRunner pool statistics."""

    in_flight: int = 0
    queued: int = 0
    completed_last_hour: int = 0
    failed_last_hour: int = 0
    capacity: int = 0
    mean_call_duration_s: float = 0.0


class TranscriptEntry(BaseModel):
    """A single turn in a session transcript."""

    role: str  # "agent" | "user"
    content: str
    timestamp: datetime | None = None


class SessionToken(BaseModel):
    """A short-lived token scoped to a Speech Pipe for browser/app connect.

    Returned by :meth:`SessionsResource.create_token`. ``token`` is an opaque
    transport credential the backend forwards to a browser verbatim — callers
    never parse it — and ``expires_at`` is its absolute UTC expiry.
    """

    token: str
    expires_at: datetime


class Session(BaseModel):
    """A voice session (matches sv_sessions response shape)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    session_id: str
    project_id: str = ""
    room_name: str | None = None
    call_id: str | None = None
    pipe_id: str | None = None
    participant_count: int = 0
    participants: list[str] = Field(default_factory=list)
    features: list[str] = Field(default_factory=list)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_s: int | None = None
    status: str = "active"
    end_reason: str | None = None
    transcript: list[TranscriptEntry] = Field(default_factory=list)
    recording_url: str | None = None
    summary: str | None = None
    usage: dict[str, Any] = Field(default_factory=dict)
    created: datetime | None = None
    modified: datetime | None = None
