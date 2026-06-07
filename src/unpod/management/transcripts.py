"""Transcripts resource."""

from __future__ import annotations

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import Transcript


class TranscriptsResource:
    """Access sessions that have transcripts."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(self) -> list[Transcript]:
        """List sessions that have a transcript."""
        resp = unwrap_data(await self._http.get("/v1/transcripts"))
        return [Transcript(**item) for item in resp]

    async def get(self, call_or_session_id: str) -> Transcript:
        """Get a transcript by call or session ID."""
        resp = unwrap_data(
            await self._http.get(f"/v1/transcripts/{call_or_session_id}")
        )
        return Transcript(**resp)
