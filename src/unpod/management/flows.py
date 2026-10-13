"""Flows resource: autonomous-agent campaigns.

Every path is the backend's ``/api/v2/platform/journey/v1`` proxy, which
forwards to the autonomous-agent service with the caller's org and space checked — the
same entry style as ``calls`` (``/api/v2/platform/speech/v1``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable
from typing import Any, TypeVar

import httpx

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import Flow

JOURNEY_BASE = "/api/v2/platform/journey/v1"

_T = TypeVar("_T")


class FlowValidationError(Exception):
    """The service refused a flow (422), or a flow's state refused the call (409).

    ``problems`` holds one readable line per issue, e.g.
    ``"step 'step_1': fixed content needs a body or a template"``.
    """

    def __init__(self, status_code: int, problems: list[str]) -> None:
        super().__init__("; ".join(problems) or f"http {status_code}")
        self.status_code = status_code
        self.problems = problems


def _problem(item: Any) -> str:
    if isinstance(item, dict):
        loc = ".".join(str(part) for part in item.get("loc") or [])
        msg = str(item.get("msg") or item)
        return f"{loc}: {msg}" if loc else msg
    return str(item)


def _problems(detail: Any) -> list[str]:
    if isinstance(detail, list):
        return [_problem(item) for item in detail]
    return [_problem(detail)] if detail else []


async def refused_as_validation(call: Awaitable[_T]) -> _T:
    """Await ``call``; a 409/422 becomes a ``FlowValidationError``."""
    try:
        return await call
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in (409, 422):
            raise
        try:
            body = exc.response.json()
        except ValueError:
            body = exc.response.text
        detail = body.get("detail", body.get("message")) if isinstance(body, dict) else body
        raise FlowValidationError(exc.response.status_code, _problems(detail)) from exc


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
        """Save a new flow, created off. Leave out ``flow_id`` and the service
        picks one (returned on the result). Raises ``FlowValidationError``."""
        resp = unwrap_data(
            await refused_as_validation(
                self._http.post(f"{JOURNEY_BASE}/spaces/{space_id}/flows", json=_body(flow))
            )
        )
        return Flow(**resp)

    async def create_from_template(
        self,
        space_id: str,
        template_id: str,
        name: str | None = None,
        flow_id: str | None = None,
    ) -> Flow:
        """A new draft flow from one of ``templates()``."""
        body = {"template_id": template_id, "name": name, "flow_id": flow_id}
        resp = unwrap_data(
            await refused_as_validation(
                self._http.post(
                    f"{JOURNEY_BASE}/spaces/{space_id}/flows/from-template",
                    json={k: v for k, v in body.items() if v is not None},
                )
            )
        )
        return Flow(**resp)

    async def templates(self) -> list[dict[str, Any]]:
        """Ready-made flows: ``[{template_id, name}]``."""
        return unwrap_data(await self._http.get(f"{JOURNEY_BASE}/flows/templates", params=None))

    async def agentic_profiles(self) -> list[dict[str, Any]]:
        """Values a flow's ``agentic_profile`` may take: ``[{profile_id, name, description}]``."""
        return unwrap_data(await self._http.get(f"{JOURNEY_BASE}/agentic-profiles", params=None))

    async def channels(self, space_id: str) -> list[dict[str, Any]]:
        """The space's connected accounts (mailboxes, numbers) a step can send from.

        A step names one in ``connector_account_ids`` by its ``account_id``.
        Accounts are connected in the Unpod app.
        """
        return unwrap_data(await self._http.get(f"{JOURNEY_BASE}/spaces/{space_id}/connectors", params=None))

    async def validate(self, space_id: str, flow: Flow | dict[str, Any]) -> dict[str, Any]:
        """Check a flow without saving it. Raises ``FlowValidationError``."""
        return unwrap_data(
            await refused_as_validation(
                self._http.post(f"{JOURNEY_BASE}/spaces/{space_id}/flows/validate", json=_body(flow))
            )
        )

    async def update(self, flow_id: str, flow: Flow | dict[str, Any]) -> Flow:
        """Save a new version; running journeys keep the version they started on."""
        resp = unwrap_data(
            await refused_as_validation(self._http.put(f"{JOURNEY_BASE}/flows/{flow_id}", json=_body(flow)))
        )
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
        """Turn the flow on. Raises ``FlowValidationError`` if it no longer validates."""
        return unwrap_data(
            await refused_as_validation(self._http.post(f"{JOURNEY_BASE}/flows/{flow_id}/activate", json=None))
        )

    async def status(self, flow_id: str) -> dict[str, Any]:
        """``{flow_id, status, version, journeys: {status: count}}`` for the latest version."""
        flow = await self.get(flow_id)
        journeys = unwrap_data(
            await self._http.get(
                f"{JOURNEY_BASE}/journeys", params={"space_id": flow.space_id, "flow_id": flow.flow_id}
            )
        )
        # ponytail: counts the rows the list returns; a summary endpoint per flow if flows outgrow it.
        return {
            "flow_id": flow.flow_id,
            "status": flow.status,
            "version": flow.version,
            "journeys": dict(Counter(j.get("status", "") for j in journeys)),
        }

    async def pause(self, flow_id: str) -> dict[str, Any]:
        """Turn the flow off: no new journeys start; running ones finish."""
        return unwrap_data(await self._http.post(f"{JOURNEY_BASE}/flows/{flow_id}/deactivate", json=None))

    async def archive(self, flow_id: str) -> dict[str, Any]:
        return unwrap_data(await self._http.post(f"{JOURNEY_BASE}/flows/{flow_id}/archive", json=None))
