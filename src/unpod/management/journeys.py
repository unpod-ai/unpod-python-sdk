"""Journeys resource: one contact through one autonomous-agent flow."""

from __future__ import annotations

from typing import Any

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.management.flows import JOURNEY_BASE
from unpod.models import ConversationMessage, Journey, JourneyStartResult

#: The levels the backend accepts for a space's agent (``human_review`` is the default).
APPROVAL_LEVELS = frozenset({"autonomous", "human_review", "shadow", "blocked"})


def _blocks(resp: Any) -> list[dict[str, Any]]:
    """The thread's blocks, whether the engine answers a list or an object."""
    if isinstance(resp, list):
        return resp
    if isinstance(resp, dict):
        return list(resp.get("blocks") or resp.get("items") or [])
    return []


class JourneysResource:
    """Start, inspect, steer and stop journeys; set a space's agent approval."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def start(
        self,
        flow_id: str,
        participant_id: str | None = None,
        contact: dict[str, Any] | None = None,
        participants: list[dict[str, Any]] | None = None,
    ) -> list[JourneyStartResult]:
        """Start the flow for one contact (``participant_id`` + ``contact``) or many.

        ``participant_id`` must match ``^[a-z0-9_-]+$``; an invalid one comes
        back as a per-contact ``"invalid"`` result.
        """
        if participants is None:
            if not participant_id:
                raise ValueError("journeys.start needs a participant_id or participants")
            participants = [{"participant_id": participant_id, **(contact or {})}]
        resp = unwrap_data(
            await self._http.post(f"{JOURNEY_BASE}/flows/{flow_id}/journeys", json={"participants": participants})
        )
        return [JourneyStartResult(**r) for r in resp.get("results", [])]

    async def get(self, journey_id: str) -> Journey:
        resp = unwrap_data(await self._http.get(f"{JOURNEY_BASE}/journeys/{journey_id}", params=None))
        return Journey(**resp)

    async def list(
        self,
        space_id: str,
        flow_id: str | None = None,
        participant_id: str | None = None,
        status: str | None = None,
    ) -> list[Journey]:
        if not space_id:
            raise ValueError("journeys.list needs a space_id")
        params = {"space_id": space_id}
        for key, value in (("flow_id", flow_id), ("participant_id", participant_id), ("status", status)):
            if value:
                params[key] = value
        resp = unwrap_data(await self._http.get(f"{JOURNEY_BASE}/journeys", params=params))
        return [Journey(**item) for item in resp]

    async def cancel(self, journey_id: str) -> dict[str, Any]:
        return unwrap_data(await self._http.post(f"{JOURNEY_BASE}/journeys/{journey_id}/cancel", json=None))

    async def send_event(
        self,
        journey_id: str,
        type: str,
        outcome: dict[str, Any] | None = None,
        summary: str = "",
        content: dict[str, Any] | None = None,
        channel: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """Signal a running journey (a ``reply``, an ``approval``, ...)."""
        body: dict[str, Any] = {"type": type, "outcome": outcome or {}, "summary": summary}
        if content is not None:
            body["content"] = content
        if channel:
            body["channel"] = channel
        if task_id:
            body["task_id"] = task_id
        return unwrap_data(await self._http.post(f"{JOURNEY_BASE}/journeys/{journey_id}/events", json=body))

    async def conversation(self, journey: Journey | str, limit: int = 200) -> list[ConversationMessage]:
        """The journey's inbox thread, oldest first; ``[]`` before a thread exists."""
        if isinstance(journey, str):
            journey = await self.get(journey)
        if not journey.inbox_thread_id:
            return []
        resp = unwrap_data(
            await self._http.get(
                f"{JOURNEY_BASE}/spaces/{journey.space_id}/threads/{journey.inbox_thread_id}/blocks",
                params={"limit": limit},
            )
        )
        return [ConversationMessage(**b) for b in _blocks(resp)]

    async def ping(self) -> dict[str, Any]:
        """Can this org reach the journey engine? ``{ok, checks[], hint}``."""
        return unwrap_data(await self._http.get("/api/v2/platform/journey/connection/", params=None))

    async def set_approval(self, space_id: str, approval: str) -> dict[str, Any]:
        """Set whether the space's agent may send on its own.

        ``autonomous`` sends without review; ``human_review`` (the default)
        blocks sends; ``shadow`` records decisions only; ``blocked`` sends
        nothing. Owners and editors of the space only (checked server-side).
        """
        if approval not in APPROVAL_LEVELS:
            raise ValueError(f"approval must be one of: {', '.join(sorted(APPROVAL_LEVELS))}")
        return unwrap_data(
            await self._http.post(
                f"/api/v1/autonomous-agents/spaces/{space_id}/agent-approval/", json={"approval": approval}
            )
        )
