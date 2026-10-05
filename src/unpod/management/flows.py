"""Flows resource: autonomous-agent campaigns.

Every path is the backend's ``/api/v2/platform/journey/v1`` proxy, which
forwards to the journey engine with the caller's org and space checked — the
same entry style as ``calls`` (``/api/v2/platform/speech/v1``).
"""

from __future__ import annotations

from typing import Any

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import Flow

JOURNEY_BASE = "/api/v2/platform/journey/v1"


def _body(flow: Flow | dict[str, Any]) -> dict[str, Any]:
    """A ``Flow`` sends its declared fields only; a dict is sent as given."""
    if isinstance(flow, Flow):
        return flow.model_dump(mode="json", exclude_none=True, include=set(Flow.model_fields))
    return flow


class FlowsResource:
    """Create, edit and switch autonomous-agent flows."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def create(self, space_id: str, flow: Flow | dict[str, Any]) -> Flow:
        resp = unwrap_data(await self._http.post(f"{JOURNEY_BASE}/spaces/{space_id}/flows", json=_body(flow)))
        return Flow(**resp)

    async def validate(self, space_id: str, flow: Flow | dict[str, Any]) -> dict[str, Any]:
        """Check a flow without saving it."""
        return unwrap_data(
            await self._http.post(f"{JOURNEY_BASE}/spaces/{space_id}/flows/validate", json=_body(flow))
        )

    async def update(self, flow_id: str, flow: Flow | dict[str, Any]) -> Flow:
        """Save a new version; running journeys keep the version they started on."""
        resp = unwrap_data(await self._http.put(f"{JOURNEY_BASE}/flows/{flow_id}", json=_body(flow)))
        return Flow(**resp)

    async def get(self, flow_id: str, version: int | None = None) -> Flow:
        params = {"version": version} if version is not None else None
        resp = unwrap_data(await self._http.get(f"{JOURNEY_BASE}/flows/{flow_id}", params=params))
        return Flow(**resp)

    async def list(self, space_id: str) -> list[Flow]:
        resp = unwrap_data(await self._http.get(f"{JOURNEY_BASE}/spaces/{space_id}/flows", params=None))
        return [Flow(**item) for item in resp]

    async def versions(self, flow_id: str) -> list[dict[str, Any]]:
        return unwrap_data(await self._http.get(f"{JOURNEY_BASE}/flows/{flow_id}/versions", params=None))

    async def activate(self, flow_id: str) -> dict[str, Any]:
        return unwrap_data(await self._http.post(f"{JOURNEY_BASE}/flows/{flow_id}/activate", json=None))

    async def pause(self, flow_id: str) -> dict[str, Any]:
        """Turn the flow off: no new journeys start; running ones finish."""
        return unwrap_data(await self._http.post(f"{JOURNEY_BASE}/flows/{flow_id}/deactivate", json=None))

    async def archive(self, flow_id: str) -> dict[str, Any]:
        return unwrap_data(await self._http.post(f"{JOURNEY_BASE}/flows/{flow_id}/archive", json=None))
