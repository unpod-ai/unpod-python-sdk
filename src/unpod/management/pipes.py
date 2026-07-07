"""Pipes resource."""

from __future__ import annotations

from typing import Any

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import Pipe


class PipesResource:
    """Manage deployed Speech Pipes."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(self) -> list[Pipe]:
        """List all pipes."""
        resp = unwrap_data(await self._http.get("/v1/pipes"))
        return [Pipe(**item) for item in resp]

    async def get(self, pipe_id: str) -> Pipe:
        """Get a single pipe by ID."""
        resp = unwrap_data(await self._http.get(f"/v1/pipes/{pipe_id}"))
        return Pipe(**resp)

    async def create(
        self,
        name: str,
        voice_profile: str | None = None,
        agent_id: str | None = None,
        agent_endpoint: str | None = None,
        recording: bool = False,
        max_call_duration_s: int = 3600,
    ) -> Pipe:
        """Create a new pipe."""
        body: dict[str, Any] = {
            "name": name,
            "recording": recording,
            "max_call_duration_s": max_call_duration_s,
        }
        if voice_profile is not None:
            body["voice_profile"] = voice_profile
        if agent_id is not None:
            body["agent_id"] = agent_id
        if agent_endpoint is not None:
            body["agent_endpoint"] = agent_endpoint
        resp = unwrap_data(await self._http.post("/v1/pipes", json=body))
        return Pipe(**resp)

    async def update(self, pipe_id: str, **kwargs: Any) -> Pipe:
        """Update an existing pipe (PATCH — supervoice's only pipe-update verb)."""
        resp = unwrap_data(await self._http.patch(f"/v1/pipes/{pipe_id}", json=kwargs))
        return Pipe(**resp)

    async def delete(self, pipe_id: str) -> None:
        """Delete a pipe."""
        await self._http.delete(f"/v1/pipes/{pipe_id}")
