"""Journeys resource: one contact through one autonomous-agent flow."""

from __future__ import annotations

from typing import Any

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.management.flows import JOURNEY_BASE, refused_as_validation
from unpod.models import ConversationMessage, Journey, JourneyStartResult

#: The levels the backend accepts for a space's agent (``human_review`` is the default).
APPROVAL_LEVELS = frozenset({"autonomous", "human_review", "shadow", "blocked"})
#: Channels a contact can opt out of one at a time (``opt_out(channel=...)``).
OPT_OUT_CHANNELS = frozenset({"email", "whatsapp", "sms", "call", "linkedin"})


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

        Raises ``FlowValidationError`` when the flow is off or no longer validates.

        ``participant_id`` must match ``^[a-z0-9_-]+$``; an invalid one comes
        back as a per-contact ``"invalid"`` result.
        """
        if participants is None:
            if not participant_id:
                raise ValueError("journeys.start needs a participant_id or participants")
            participants = [{"participant_id": participant_id, **(contact or {})}]
        resp = unwrap_data(
            await refused_as_validation(
                self._http.post(f"{JOURNEY_BASE}/flows/{flow_id}/journeys", json={"participants": participants})
            )
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

    # ── operator actions: what the inbox does, from code ──────────────────

    async def _act(self, journey_id: str, action: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        return unwrap_data(await self._http.post(f"{JOURNEY_BASE}/journeys/{journey_id}/{action}", json=body))

    async def draft(self, journey_id: str) -> dict[str, Any]:
        """The message the agent wrote and is holding for review (``human_review``)."""
        return unwrap_data(await self._http.get(f"{JOURNEY_BASE}/journeys/{journey_id}/draft", params=None))

    async def approve(
        self,
        journey_id: str,
        approved: bool = True,
        response_text: str | None = None,
        decision_id: str | None = None,
    ) -> dict[str, Any]:
        """Send (or reject) the held draft. ``response_text`` replaces its text.

        Counts as a person's send, so it needs a signed-in user (platform-token mode).
        """
        body: dict[str, Any] = {"approved": approved}
        if response_text is not None:
            body["response_text"] = response_text
        if decision_id:
            body["decision_id"] = decision_id
        return await self._act(journey_id, "approve", body)

    async def reply(
        self,
        journey_id: str,
        text: str,
        subject: str | None = None,
        request_id: str | None = None,
        pause_agent: bool = True,
    ) -> dict[str, Any]:
        """Send a reply as a person on the journey's channel.

        ``pause_agent`` (default) holds the agent until ``release``. Reuse a
        ``request_id`` to retry the same reply without sending it twice. Needs
        a signed-in user (platform-token mode).
        """
        if not text.strip():
            raise ValueError("journeys.reply needs non-blank text")
        body: dict[str, Any] = {"text": text}
        if subject is not None:
            body["subject"] = subject
        if request_id:
            body["request_id"] = request_id
        body["pause_agent"] = pause_agent
        return await self._act(journey_id, "reply", body)

    async def takeover(self, journey_id: str, operator: str = "", reason: str = "") -> dict[str, Any]:
        """Pause the agent so a person handles the conversation."""
        return await self._act(journey_id, "takeover", {"operator": operator, "reason": reason})

    async def release(self, journey_id: str, operator: str = "", reason: str = "") -> dict[str, Any]:
        """Hand the conversation back to the agent (ends a takeover or a needs-a-person hold)."""
        return await self._act(journey_id, "release", {"operator": operator, "reason": reason})

    async def resume(self, journey_id: str) -> dict[str, Any]:
        """Resume a paused journey."""
        return await self._act(journey_id, "resume")

    async def opt_out(self, journey_id: str, channel: str | None = None) -> dict[str, Any]:
        """Record that the contact opted out: of everything, or of one ``channel``."""
        if channel is None:
            return await self._act(journey_id, "opt-out", {"scope": "contact"})
        if channel not in OPT_OUT_CHANNELS:
            raise ValueError(f"channel must be one of: {', '.join(sorted(OPT_OUT_CHANNELS))}")
        return await self._act(journey_id, "opt-out", {"scope": "channel", "channel": channel})

    async def reactivate(self, journey_id: str) -> dict[str, Any]:
        """Start an ended journey again for the same contact (a new cycle)."""
        return await self._act(journey_id, "reactivate")

    async def alerts(
        self,
        space_id: str,
        kind: str = "possible_opt_out",
        limit: int = 100,
        before: str | None = None,
        before_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """The space's alerts for a person to decide, newest first.

        Next page: pass the last row's ``at`` as ``before`` and its ``alert_id`` as ``before_id``.
        """
        params: dict[str, Any] = {"kind": kind, "limit": limit}
        if before:
            params["before"] = before
        if before_id:
            params["before_id"] = before_id
        return unwrap_data(await self._http.get(f"{JOURNEY_BASE}/spaces/{space_id}/alerts", params=params))

    async def stuck_tasks(self, space_id: str, older_than_minutes: int = 15) -> list[dict[str, Any]]:
        """Sends that started but never finished. Never resent automatically: check, then act."""
        return unwrap_data(
            await self._http.get(
                f"{JOURNEY_BASE}/spaces/{space_id}/tasks/stuck", params={"older_than_minutes": older_than_minutes}
            )
        )

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
        """Can this org reach the autonomous-agent service? ``{ok, checks[], hint}``."""
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
