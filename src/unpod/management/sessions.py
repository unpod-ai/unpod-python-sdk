"""Sessions resource."""

from __future__ import annotations

from typing import Any

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import (
    Session,
    SessionEndResult,
    SessionMergeResult,
    SessionToken,
    SessionTransferResult,
)


class SessionsResource:
    """Access voice sessions.

    CRUD operations (``list``/``get``/``create_token``) target the platform
    service, while lifecycle operations (``end``/``transfer``/``merge``)
    target the orchestrator service.
    """

    def __init__(
        self, http: AsyncHTTPClient, orch_http: AsyncHTTPClient | None = None
    ) -> None:
        self._http = http
        self._orch_http = orch_http or http

    async def list(self) -> list[Session]:
        """List all sessions."""
        resp = unwrap_data(await self._http.get("/v1/sessions"))
        return [Session(**item) for item in resp]

    async def get(self, session_id: str) -> Session:
        """Get a single session by ID."""
        resp = unwrap_data(await self._http.get(f"/v1/sessions/{session_id}"))
        return Session(**resp)

    async def create_token(
        self, pipe_id: str, metadata: dict[str, Any] | None = None
    ) -> SessionToken:
        """Mint a short-lived, single-use token scoped to a Speech Pipe.

        A backend calls this with its API key and hands the returned token to
        a browser/app so it can establish a voice session without ever seeing
        the API key. ``pipe_id`` names the target Speech Pipe; optional
        ``metadata`` is attached to the resulting session.
        """
        body: dict[str, Any] = {"pipe_id": pipe_id}
        if metadata is not None:
            body["metadata"] = metadata
        resp = unwrap_data(await self._http.post("/v1/sessions/token", json=body))
        return SessionToken(**resp)

    async def end(self, session_id: str) -> SessionEndResult:
        """End an active orchestrator session."""
        resp = unwrap_data(await self._orch_http.post(f"/v1/sessions/{session_id}/end"))
        return SessionEndResult(**resp)

    async def transfer(
        self,
        session_id: str,
        to_type: str,
        to_config: dict[str, Any] | None = None,
        mode: str = "cold",
        warm_handoff_ms: int = 0,
        drop_participant_id: str | None = None,
    ) -> SessionTransferResult:
        """Transfer a session to a new target participant.

        ``to_type`` is the target kind (``sip``/``agent``/``webrtc``/
        ``livekit``) and ``to_config`` its provider config. ``mode`` selects
        a cold or warm handoff; ``drop_participant_id`` optionally removes an
        existing participant.
        """
        body: dict[str, Any] = {
            "to": {"type": to_type, "config": to_config or {}},
            "mode": mode,
            "warm_handoff_ms": warm_handoff_ms,
        }
        if drop_participant_id is not None:
            body["drop_participant_id"] = drop_participant_id
        resp = unwrap_data(
            await self._orch_http.post(f"/v1/sessions/{session_id}/transfer", json=body)
        )
        return SessionTransferResult(**resp)

    async def merge(
        self,
        primary_session_id: str,
        secondary_session_ids: list[str],
        drop_participants: list[dict[str, Any]] | None = None,
    ) -> SessionMergeResult:
        """Merge secondary sessions into a primary session."""
        body: dict[str, Any] = {
            "primary_session_id": primary_session_id,
            "secondary_session_ids": secondary_session_ids,
        }
        if drop_participants is not None:
            body["drop_participants"] = drop_participants
        resp = unwrap_data(await self._orch_http.post("/v1/sessions/merge", json=body))
        return SessionMergeResult(**resp)
