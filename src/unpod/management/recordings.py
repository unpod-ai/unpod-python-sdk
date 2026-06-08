"""Recordings resource.

The management plane has no standalone recording object: ``GET /v1/recordings``
returns the **sessions** that have a recording (``list[SessionResponse]``); the
recording itself is ``Session.recording_url``. These methods return
:class:`~unpod.models.Session`.
"""

from __future__ import annotations

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import Session


class RecordingsResource:
    """Access sessions that have recordings."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(self, call_id: str | None = None) -> list[Session]:
        """List sessions that have a recording (read ``.recording_url`` on each)."""
        params = {"call_id": call_id} if call_id else None
        resp = unwrap_data(await self._http.get("/v1/recordings", params=params))
        return [Session(**item) for item in resp]
