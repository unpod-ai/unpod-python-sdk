"""Phone numbers resource."""

from __future__ import annotations

from unpod.management._auth import BearerAuth
from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import Number


#: backend-core proxy route (addressed from the host root by ``_http._url``).
_NUMBERS = "/api/v2/platform/speech/v1/numbers"
#: supervoice-native route, relative to the ``<host>/platform`` base
#: (``platform/routers/numbers.py`` mounted under ``/v1``).
_SV_NUMBERS = "/v1/numbers"


def _is_direct(http: AsyncHTTPClient) -> bool:
    """Direct-to-supervoice mode: the client authenticates with ``BearerAuth``.

    Supervoice accepts only ``Authorization: Bearer <api_key>``; the
    backend-core proxy accepts only DRF ``Token`` / platform ``JWT`` (it
    rejects Bearer). So the auth strategy, not the base URL, names the plane.
    A custom ``Auth`` is treated as proxy mode.
    """
    return isinstance(getattr(http, "_auth", None), BearerAuth)


class NumberAgentResource:
    """``client.numbers.agent`` — which agent answers a number."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def change(
        self, number: str, agent_id: str, *, attach_type: str | None = None
    ) -> dict:
        """Move an attached number to another agent of your organization.

        ``number`` is the number id or its E.164. No detach needed; inbound
        calls follow the new agent. Only numbers already attached in your own
        organization can be moved — any other number is a 404; a free number
        you have not attached yet is a 409 ``not_attached`` (use ``attach``).

        ``attach_type`` is optional and defaults to the proxy's ``"pipeline"``
        (a supervoice agent). Pass ``attach_type="agent"`` to move the number
        back to a Pilot agent.

        **Direct-supervoice mode** (``BearerAuth``): sends
        ``PATCH /v1/numbers/{number_id}`` with ``{"agent_id": ...}``, which
        rebinds ``sv_numbers`` (and the agents' number back-reference) ONLY.
        Inbound routing moves only in proxy mode, because Django owns the
        dispatch rule; directly, inbound calls keep going where Django's rule
        sends them. An E.164 is resolved to the supervoice ``number_id`` via
        ``GET /v1/numbers`` (``LookupError`` when the project has no such
        number). There is no ``not_attached`` 409 and ``attach_type`` is
        ignored in this mode.
        """
        if _is_direct(self._http):
            number_id = number
            if number.startswith("+"):
                rows = unwrap_data(await self._http.get(_SV_NUMBERS))
                match = next(
                    (r for r in rows if isinstance(r, dict) and r.get("number") == number),
                    None,
                )
                if match is None:
                    raise LookupError(f"number {number} not found in this project")
                number_id = str(match.get("number_id") or match.get("id"))
            return unwrap_data(  # type: ignore[return-value]
                await self._http.patch(f"{_SV_NUMBERS}/{number_id}", json={"agent_id": agent_id})
            )
        body: dict[str, str] = {"agent_id": agent_id}
        if number.startswith("+"):
            body["number"] = number
        if attach_type:
            body["attach_type"] = attach_type
        return unwrap_data(await self._http.put(f"{_NUMBERS}/{number}/attach", json=body))  # type: ignore[return-value]


class NumbersResource:
    """Manage phone numbers synced from SIP trunks."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http
        self.agent = NumberAgentResource(http)

    async def list(
        self,
        status: str | None = None,
        country: str | None = None,
        trunk_type: str | None = None,
    ) -> list[Number]:
        """List your organization's numbers, optionally filtered.

        Proxy mode reads ``speech/v1/numbers``; direct-supervoice mode reads
        ``GET /v1/numbers`` (supervoice names the status filter
        ``status_filter``).
        """
        direct = _is_direct(self._http)
        params: dict[str, str] = {}
        if status:
            params["status_filter" if direct else "status"] = status
        if country:
            params["country"] = country
        if trunk_type:
            params["trunk_type"] = trunk_type
        path = _SV_NUMBERS if direct else _NUMBERS
        resp = unwrap_data(await self._http.get(path, params=params or None))
        return [Number(**item) for item in resp]

    async def delete(self, number_id: str) -> None:
        """Remove the number from your organization's bridge and from every agent.

        Direct-supervoice mode deletes only the ``sv_numbers`` row
        (``DELETE /v1/numbers/{id}``); the bridge is Django's.
        """
        await self._http.delete(self._item(number_id))

    async def release(self, number_id: str) -> None:
        """Release a phone number (same route as ``delete``)."""
        await self._http.delete(self._item(number_id))

    def _item(self, number_id: str) -> str:
        base = _SV_NUMBERS if _is_direct(self._http) else _NUMBERS
        return f"{base}/{number_id}"

    async def sync(self) -> dict:
        """Sync numbers from LiveKit SIP trunks.

        Returns a summary dict: ``{"synced": int, "new": int}``. Unwrapped like
        every other method so direct and proxy responses parse identically.
        """
        resp = unwrap_data(await self._http.post(f"{_NUMBERS}/sync", json=None))
        return resp  # type: ignore[return-value]

    async def attach(self, number_id: str, agent_id: str, number: str | None = None) -> Number:
        """Attach a number to an agent.

        ``agent_id`` is the binding — the pipe pin is gone, because supervoice
        stopped storing one and resolved the pipe from the agent instead.

        The route is the speech numbers endpoint, which delegates to telephony's
        attach orchestration: that is what creates the ``VoiceBridgeNumber`` and
        the LiveKit trunks, then pushes number + both trunk ids back to
        supervoice. Posting straight to supervoice bound the agent in mongo and
        left the number with nothing to route through.

        ``number`` is the E.164 and is optional; pass it when ``number_id`` is a
        supervoice id, so telephony can resolve the row it owns.
        """
        body: dict[str, str] = {"agent_id": agent_id}
        if number is not None:
            body["number"] = number
        resp = unwrap_data(
            await self._http.post(
                f"{_NUMBERS}/{number_id}/attach",
                json=body,
            )
        )
        return Number(**resp)

    async def detach(self, number_id: str) -> Number:
        """Detach a number from its agent."""
        resp = unwrap_data(
            await self._http.delete_with_response(f"{_NUMBERS}/{number_id}/attach")
        )
        return Number(**resp)
