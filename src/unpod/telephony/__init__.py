"""The backend-core telephony plane: ``client.telephony.*``.

A surface over backend-core's ``/api/v2/platform/telephony/*`` (distinct from the
supervoice management plane at ``client.numbers`` / ``client.trunks``). The
PRIMARY flow attaches a number to an agent — the Leg-B termination
(SuperSBC → agent/LiveKit):

    async with AsyncClient(auth=JWTAuth(token, org_handle="acme")) as client:
        nums = await client.telephony.numbers.list()
        res  = await client.telephony.numbers.attach(
            [n.id for n in nums[:2]], agent_id="asst_sales")
        print(res.numbers[0].connection_state)

``client.telephony.trunks.*`` is the FUTURE/BETA BYO-carrier path (Leg A: your
own SIP carrier → SuperSBC). Leg A is the hardcoded SuperSBC default today, so
the trunk surface is not yet the primary production flow — prefer
``numbers.attach`` for wiring numbers to agents.

Requires proxy/JWT auth (``JWTAuth(token, org_handle)``) — this plane is
``Org-Handle``-scoped and is not reachable in direct/Bearer supervoice mode.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field

from unpod.management._http import AsyncHTTPClient, unwrap_data


def _as_id_list(number_ids: "int | str | Sequence[int]") -> list[int]:
    """Coerce a single id or a sequence of ids to ``list[int]``.

    Callers reach for ``attach(123, ...)`` as often as ``attach([123], ...)``, so
    a bare int/str is wrapped rather than rejected. A str is treated as ONE id —
    never iterated char-by-char (``list("12")`` → ``["1", "2"]`` is the trap this
    guards). Each id is coerced to int so ``"123"`` and ``123`` behave the same.
    """
    if isinstance(number_ids, (int, str)):
        return [int(number_ids)]
    return [int(n) for n in number_ids]

# ── models ───────────────────────────────────────────────────────────────────


class Number(BaseModel):
    """A telephony number from the org's available pool."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    id: int | None = None
    number: str | None = None
    state: str | None = None
    country: str | None = None


class Trunk(BaseModel):
    """A SIP carrier trunk (secrets masked on read). Leg A / BYO-carrier —
    future/beta; the primary flow is ``numbers.attach`` (Leg B)."""

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
    agent_id: str | None = None  # set by the agent-attach (Leg-B) path
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


class AgentAttachResult(BaseModel):
    """Result of attaching numbers to an agent (Leg B). No carrier origin
    endpoint — that belongs to the Leg-A / BYO-carrier (trunks) path."""

    model_config = ConfigDict(extra="allow")

    agent_id: str | None = None
    numbers: list[NumberResult] = Field(default_factory=list)
    message: str | None = None


class AgentDetachResult(BaseModel):
    """Result of detaching numbers. No carrier origin endpoint — that belongs to
    the Leg-A / BYO-carrier (trunks) path."""

    model_config = ConfigDict(extra="allow")

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
    """The org's telephony numbers — list, and attach to an agent (Leg B)."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(self) -> list[Number]:
        """List the org's available numbers."""
        resp = unwrap_data(await self._http.get("/telephony/numbers/"))
        return [Number(**item) for item in (resp or [])]

    async def attach(
        self,
        number_ids: "int | str | Sequence[int]",
        *,
        agent_id: str | None = None,
        attach_type: str | None = None,
        pipe_id: str | None = None,
        bridge_slug: str | None = None,
        region: str | None = None,
    ) -> AgentAttachResult:
        """Attach numbers to an agent — the primary Leg-B flow.

        Maps each number to the agent termination (SuperSBC → agent/LiveKit); the
        bridge is auto-resolved (hidden). ``agent_id`` is optional: when set the
        number routes to that agent, when omitted the number is wired for agent
        use without binding one yet. ``attach_type`` selects the termination
        path — ``"agent"`` (default) or ``"pipeline"``; ``pipe_id`` names the
        supervoice pipe the number routes to and is REQUIRED when
        ``attach_type="pipeline"``. Returns the per-number lifecycle (no carrier
        origin endpoint — that is the Leg-A / BYO-carrier trunks path).
        Partial-success: each number reports ok/error independently.
        """
        body: dict = {"number_ids": _as_id_list(number_ids)}
        if agent_id is not None:
            body["agent_id"] = agent_id
        if attach_type is not None:
            body["attach_type"] = attach_type
        if pipe_id is not None:
            body["pipe_id"] = pipe_id
        if bridge_slug is not None:
            body["bridge_slug"] = bridge_slug
        if region is not None:
            body["region"] = region
        resp = await self._http.post("/telephony/numbers/attach/", json=body)
        return AgentAttachResult(**unwrap_data(resp))

    async def detach(
        self, number_ids: "int | str | Sequence[int]"
    ) -> AgentDetachResult:
        """Detach numbers — the inverse of :meth:`attach`, for either termination.

        Deletes each number's LiveKit trunks (one still carrying other numbers is
        updated instead), tears down its SBC routes/DID, releases it in supervoice,
        and returns it to the pool. The termination, agent and pipe are read from
        the stored record, so they need not be restated. The supervoice number
        record is RELEASED, not deleted — it stays available for a later attach.
        Partial-success: each number reports ok/error independently.
        """
        resp = await self._http.post(
            "/telephony/numbers/detach/", json={"number_ids": _as_id_list(number_ids)}
        )
        return AgentDetachResult(**unwrap_data(resp))


class TrunksResource:
    """SIP carrier trunks + number mapping. Leg A / BYO-carrier — FUTURE/BETA;
    the primary flow for wiring numbers to agents is ``numbers.attach`` (Leg B)."""

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
        return AttachResult(**unwrap_data(resp))

    async def detach_numbers(
        self, trunk_id: int | str, number_ids: Sequence[int]
    ) -> DetachResult:
        """Unmap numbers from a trunk."""
        resp = await self._http.post(
            f"/telephony/trunks/{trunk_id}/detach-numbers/",
            json={"number_ids": list(number_ids)},
        )
        return DetachResult(**unwrap_data(resp))


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
    "AgentAttachResult",
    "AgentDetachResult",
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
