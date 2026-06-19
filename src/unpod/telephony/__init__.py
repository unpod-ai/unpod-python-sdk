"""The backend-core telephony plane: ``client.telephony.*``.

A *trunk-centric* surface over backend-core's ``/api/v2/platform/telephony/*``
(distinct from the supervoice management plane at ``client.numbers`` /
``client.trunks``). A "Trunk" here is the SIP carrier credential; you create a
trunk, map one-or-many numbers to it, and get back the carrier *origin endpoint*:

    async with AsyncClient(auth=JWTAuth(token, org_handle="acme")) as client:
        nums  = await client.telephony.numbers.list()
        trunk = await client.telephony.trunks.create(
            name="My Carrier", sip_url="sip:carrier.net",
            username="u", password="p", source_ips=["203.0.113.10"])
        res = await client.telephony.trunks.attach_numbers(
            trunk.id, number_ids=[n.id for n in nums[:2]])
        print(res.origin_endpoint.ingress)   # point your carrier here

Requires proxy/JWT auth (``JWTAuth(token, org_handle)``) — this plane is
``Org-Handle``-scoped and is not reachable in direct/Bearer supervoice mode.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from unpod.management._http import AsyncHTTPClient, unwrap_data

# ── models ───────────────────────────────────────────────────────────────────


class Number(BaseModel):
    """A telephony number from the org's available pool."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: int | None = None
    number: str | None = None
    state: str | None = None
    country: str | None = None


class Trunk(BaseModel):
    """A SIP carrier trunk (secrets masked on read)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: int
    name: str | None = None
    sip_url: str | None = None
    transport: str | None = None
    port: str | None = None
    auth_username: str | None = None
    auth_password: str | None = None  # masked
    allowed_ips: str | None = None
    active: bool | None = None


class OriginEndpoint(BaseModel):
    """Where the carrier sends inbound calls: one shared ingress host + the DIDs."""

    model_config = ConfigDict(extra="allow")

    ingress: str = ""
    dids: list[str] = Field(default_factory=list)
    accepted_source_ips: list[str] = Field(default_factory=list)
    region: str | None = None


class NumberResult(BaseModel):
    """Per-number outcome of an attach/detach (partial-success)."""

    model_config = ConfigDict(extra="allow")

    number_id: int
    number: str | None = None
    connection_state: str | None = None
    ok: bool = False
    error: str | None = None


class AttachResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    trunk_id: int
    origin_endpoint: OriginEndpoint = Field(default_factory=OriginEndpoint)
    numbers: list[NumberResult] = Field(default_factory=list)
    message: str | None = None


class DetachResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    trunk_id: int
    numbers: list[NumberResult] = Field(default_factory=list)
    message: str | None = None


class NumberOverview(BaseModel):
    """Per-number lifecycle: connection + cross-plane sync state."""

    model_config = ConfigDict(extra="allow")

    number: str | None = None
    number_id: int | None = None
    bridge_slug: str | None = None
    connection_state: str | None = None
    termination_kind: str | None = None
    agent_id: str | None = None
    provider: str | None = None
    sync_state: str | None = None
    in_sync: bool | None = None


# ── resources ────────────────────────────────────────────────────────────────


class NumbersResource:
    """The org's available (unassigned) telephony numbers."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(self) -> list[Number]:
        """List the org's available numbers."""
        resp = unwrap_data(await self._http.get("/telephony/numbers/"))
        return [Number(**item) for item in (resp or [])]


class TrunksResource:
    """SIP carrier trunks + number mapping."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(self) -> list[Trunk]:
        """List the org's SIP trunks (secrets masked)."""
        resp = unwrap_data(await self._http.get("/telephony/trunks/"))
        return [Trunk(**item) for item in (resp or [])]

    async def create(
        self,
        name: str,
        sip_url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        transport: str | None = None,
        port: str | None = None,
        source_ips: Sequence[str] | None = None,
    ) -> Trunk:
        """Create a SIP trunk. ``source_ips`` is the carrier's inbound IP ACL."""
        body: dict = {"name": name, "sip_url": sip_url}
        if username is not None:
            body["auth_username"] = username
        if password is not None:
            body["auth_password"] = password
        if transport is not None:
            body["transport"] = transport
        if port is not None:
            body["port"] = port
        if source_ips is not None:
            body["source_ips"] = list(source_ips)
        resp = unwrap_data(await self._http.post("/telephony/trunks/", json=body))
        return Trunk(**resp)

    async def get(self, trunk_id: int | str) -> Trunk:
        """Fetch one trunk by id."""
        resp = unwrap_data(await self._http.get(f"/telephony/trunks/{trunk_id}/"))
        return Trunk(**resp)

    async def delete(self, trunk_id: int | str) -> None:
        """Delete a trunk."""
        await self._http.delete(f"/telephony/trunks/{trunk_id}/")

    async def attach_numbers(
        self,
        trunk_id: int | str,
        number_ids: Sequence[int],
        *,
        bridge_slug: str | None = None,
        region: str | None = None,
    ) -> AttachResult:
        """Map numbers to a trunk; returns the origin endpoint + per-number results."""
        body: dict = {"number_ids": list(number_ids)}
        if bridge_slug is not None:
            body["bridge_slug"] = bridge_slug
        if region is not None:
            body["region"] = region
        resp = await self._http.post(
            f"/telephony/trunks/{trunk_id}/attach-numbers/", json=body
        )
        return AttachResult(**resp)  # not wrapped in `data`

    async def detach_numbers(
        self, trunk_id: int | str, number_ids: Sequence[int]
    ) -> DetachResult:
        """Unmap numbers from a trunk."""
        resp = await self._http.post(
            f"/telephony/trunks/{trunk_id}/detach-numbers/",
            json={"number_ids": list(number_ids)},
        )
        return DetachResult(**resp)


class TelephonyNamespace:
    """``client.telephony`` — numbers, trunks, and a lifecycle overview."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http
        self.numbers = NumbersResource(http)
        self.trunks = TrunksResource(http)

    async def overview(self) -> list[NumberOverview]:
        """Per-number lifecycle + cross-plane sync state for the org."""
        resp = unwrap_data(await self._http.get("/telephony/overview/"))
        return [NumberOverview(**item) for item in (resp or [])]


__all__ = [
    "AttachResult",
    "DetachResult",
    "Number",
    "NumberOverview",
    "NumberResult",
    "NumbersResource",
    "OriginEndpoint",
    "TelephonyNamespace",
    "Trunk",
    "TrunksResource",
]
