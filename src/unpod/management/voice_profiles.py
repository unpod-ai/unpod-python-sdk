"""Voice profiles resource (backend-core platform plane).

Reads voice profiles from the backend-core ``/api/v2/platform/voice-profiles/``
surface (Django) — the same plane the telephony namespace uses — NOT the
supervoice speech proxy. This is a read-only surface; profiles are managed
upstream in the platform, so only ``list`` and ``get`` are exposed.
"""

from __future__ import annotations

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import VoiceProfile


class VoiceProfilesResource:
    """Browse available voice profiles from the backend-core platform plane."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        # The backend-core platform client (base ``/api/v2/platform``).
        self._http = http

    async def list(self, language: str | None = None) -> list[VoiceProfile]:
        """List active voice profiles, optionally filtered by language."""
        params = {"language": language} if language else None
        # Django wraps the list body twice: the view returns ``{"data": [...]}``
        # and UnpodJSONRenderer wraps that again — so unwrap the envelope twice.
        resp = unwrap_data(
            unwrap_data(await self._http.get("/voice-profiles/", params=params))
        )
        return [VoiceProfile(**item) for item in resp]

    async def get(self, profile_id: str) -> VoiceProfile:
        """Get a single voice profile by ID."""
        resp = unwrap_data(
            unwrap_data(await self._http.get(f"/voice-profiles/{profile_id}/"))
        )
        return VoiceProfile(**resp)
