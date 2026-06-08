"""Transcripts resource.

The management plane has no standalone transcript object: ``GET /v1/transcripts``
returns the **sessions** that have a transcript (``list[SessionResponse]``), and a
single transcript is read from its session (``Session.transcript``). These methods
therefore return :class:`~unpod.models.Session`.
"""

from __future__ import annotations

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import Session


class TranscriptsResource:
    """Access sessions that have transcripts."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(self) -> list[Session]:
        """List sessions that have a transcript (read ``.transcript`` on each)."""
        resp = unwrap_data(await self._http.get("/v1/transcripts"))
        return [Session(**item) for item in resp]

    async def get(self, session_id: str) -> Session:
        """Get the session for ``session_id`` (its ``.transcript`` holds the turns).

        There is no per-transcript endpoint; this reads the session, which
        carries the transcript.
        """
        resp = unwrap_data(await self._http.get(f"/v1/sessions/{session_id}"))
        return Session(**resp)
