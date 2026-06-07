"""Orchestrator session models.

These mirror the orchestrator service's session lifecycle schemas
(``GET/POST /v1/sessions/*``), which are distinct from the platform
CRUD :class:`~unpod.models.session.Session` shape.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OrchestratorParticipant(BaseModel):
    """A participant in an orchestrator session."""

    participant_id: str
    type: str


class OrchestratorSession(BaseModel):
    """An orchestrator session (matches ``GET /v1/sessions/{id}``)."""

    model_config = ConfigDict(extra="allow")

    session_id: str
    tenant_id: str
    state: str
    external_call_id: str | None = None
    job_id: str | None = None
    room_id: str | None = None
    participants: list[OrchestratorParticipant] = Field(default_factory=list)


class SessionEndResult(BaseModel):
    """Result of ending a session (``POST /v1/sessions/{id}/end``)."""

    session_id: str
    state: str


class SessionTransferResult(BaseModel):
    """Result of a session transfer (``POST /v1/sessions/{id}/transfer``)."""

    session_id: str
    added_participant_id: str
    removed_participant_id: str | None = None
    mode: str


class MergeOutcome(BaseModel):
    """Per-session outcome within a merge operation."""

    session_id: str
    status: str
    moved_participant_ids: list[str] = Field(default_factory=list)
    error: str | None = None


class SessionMergeResult(BaseModel):
    """Result of a session merge (``POST /v1/sessions/merge``)."""

    primary_session_id: str
    outcomes: list[MergeOutcome] = Field(default_factory=list)
