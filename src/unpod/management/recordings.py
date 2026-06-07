"""Recordings resource."""

from __future__ import annotations

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import Recording


class RecordingsResource:
    """Access sessions that have recordings."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(self, call_id: str | None = None) -> list[Recording]:
        """List sessions that have a recording."""
        params = {"call_id": call_id} if call_id else None
        resp = unwrap_data(await self._http.get("/v1/recordings", params=params))
        return [Recording(**item) for item in resp]
