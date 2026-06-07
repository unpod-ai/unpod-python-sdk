"""Trunks resource."""

from __future__ import annotations

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import Trunk, TrunkCreate


class TrunksResource:
    """Manage SIP trunks."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(self) -> list[Trunk]:
        """List all trunks."""
        resp = unwrap_data(await self._http.get("/v1/trunks"))
        return [Trunk(**item) for item in resp]

    async def create(self, trunk: TrunkCreate) -> Trunk:
        """Create a new trunk."""
        resp = unwrap_data(
            await self._http.post(
                "/v1/trunks", json=trunk.model_dump(exclude_none=True)
            )
        )
        return Trunk(**resp)

    async def delete(self, trunk_id: str) -> None:
        """Delete a trunk."""
        await self._http.delete(f"/v1/trunks/{trunk_id}")
