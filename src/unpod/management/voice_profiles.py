"""Voice profiles resource."""

from __future__ import annotations

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import VoiceProfile


class VoiceProfilesResource:
    """Browse available voice profiles."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(self, language: str | None = None) -> list[VoiceProfile]:
        """List voice profiles, optionally filtered by language."""
        params = {"language": language} if language else None
        resp = unwrap_data(await self._http.get("/v1/voice-profiles", params=params))
        return [VoiceProfile(**item) for item in resp]

    async def get(self, profile_id: str) -> VoiceProfile:
        """Get a single voice profile by ID."""
        resp = unwrap_data(await self._http.get(f"/v1/voice-profiles/{profile_id}"))
        return VoiceProfile(**resp)
