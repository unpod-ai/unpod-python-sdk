"""Calls resource."""

from __future__ import annotations

from typing import Any

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import Call


class CallsResource:
    """Manage voice calls."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(
        self,
        status: str | None = None,
        pipe_id: str | None = None,
    ) -> list[Call]:
        """List calls, optionally filtered by status or pipe."""
        params: dict[str, str] = {}
        if status:
            params["call_status"] = status
        if pipe_id:
            params["pipe_id"] = pipe_id
        resp = unwrap_data(await self._http.get("/api/v2/platform/speech/v1/calls", params=params or None))
        return [Call(**item) for item in resp]

    async def get(self, call_id: str) -> Call:
        """Get a single call by ID."""
        resp = unwrap_data(await self._http.get(f"/api/v2/platform/speech/v1/calls/{call_id}"))
        return Call(**resp)

    async def create(
        self,
        pipe_id: str | None = None,
        to_number: str | None = None,
        from_number: str | None = None,
        instructions: str | None = None,
        data: dict[str, Any] | None = None,
        agent_id: str | None = None,
        agent: str | None = None,
        user_number: str | None = None,
    ) -> Call:
        """Initiate an outbound call.

        Provide ``agent_id`` OR ``pipe_id`` (``agent_id`` takes priority). When
        only ``agent_id`` is given the platform resolves a pipe bound to that
        agent server-side, so ``pipe_id`` is optional. ``to_number`` is required.

        The platform enqueues the call asynchronously (dispatched via the
        orchestration queue); the returned :class:`~unpod.models.Call`
        reflects the queued/accepted state, not call completion.
        """
        resolved_agent = agent_id or agent
        body: dict[str, Any] = {
            "to_number": to_number or user_number,
        }
        if resolved_agent is not None:
            body["agent_id"] = resolved_agent
        if pipe_id is not None:
            body["pipe_id"] = pipe_id
        if from_number is not None:
            body["from_number"] = from_number
        if instructions is not None:
            body["instructions"] = instructions
        if data is not None:
            body["data"] = data
        resp = unwrap_data(await self._http.post("/api/v2/platform/speech/v1/calls", json=body))
        return Call(**resp)

    async def hangup(self, call_id: str) -> Call:
        """Hang up an active call."""
        resp = unwrap_data(
            await self._http.post(f"/api/v2/platform/speech/v1/calls/{call_id}/hangup", json=None)
        )
        return Call(**resp) if resp else Call(id=call_id, status="hangup_requested")
