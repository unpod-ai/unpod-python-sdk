"""Phone numbers resource."""

from __future__ import annotations

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import Number


class NumbersResource:
    """Manage phone numbers synced from SIP trunks."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(
        self,
        status: str | None = None,
        country: str | None = None,
        trunk_type: str | None = None,
    ) -> list[Number]:
        """List all numbers, optionally filtered by status, country, or trunk type."""
        params: dict[str, str] = {}
        if status:
            params["status"] = status
        if country:
            params["country"] = country
        if trunk_type:
            params["trunk_type"] = trunk_type
        resp = unwrap_data(await self._http.get("/api/v2/platform/telephony/numbers/", params=params or None))
        return [Number(**item) for item in resp]

    async def delete(self, number_id: str) -> None:
        """Remove a number from My Numbers (reappears in provider sync)."""
        await self._http.delete(f"/api/v2/platform/telephony/numbers/{number_id}")

    async def release(self, number_id: str) -> None:
        """Release a phone number."""
        await self._http.delete(f"/api/v2/platform/telephony/numbers/{number_id}")

    async def sync(self) -> dict:
        """Sync numbers from LiveKit SIP trunks.

        Returns a summary dict: ``{"synced": int, "new": int}``. Unwrapped like
        every other method so direct and proxy responses parse identically.
        """
        resp = unwrap_data(await self._http.post("/api/v2/platform/speech/v1/numbers/sync", json=None))
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
                f"/api/v2/platform/speech/v1/numbers/{number_id}/attach",
                json=body,
            )
        )
        return Number(**resp)

    async def detach(self, number_id: str) -> Number:
        """Detach a number from its agent."""
        resp = unwrap_data(
            await self._http.delete_with_response(f"/api/v2/platform/speech/v1/numbers/{number_id}/attach")
        )
        return Number(**resp)
