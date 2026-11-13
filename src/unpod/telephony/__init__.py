"""Phone numbers and phone calls on Unpod: ``client.telephony``.

What lives here:

- ``client.telephony.numbers``  list your numbers, attach one to an agent, detach it
- ``client.telephony.overview()``  per-number status (is it linked, which agent)
- ``client.telephony.calls``  control live calls yourself (preview, see ``calls.py``)
- ``client.telephony.inbound``  what happens when someone calls your number (preview)
- ``client.telephony.webhook_secret`` + :func:`parse_call_event`  signed call events
- ``client.telephony.trunks``  bring your own SIP carrier (beta)

Quick start (reads ``UNPOD_BASE_URL``, ``UNPOD_PLATFORM_TOKEN`` and
``UNPOD_ORG_HANDLE`` from the environment or a ``.env`` file)::

    from unpod import Client

    with Client() as client:
        free = [n.number for n in client.telephony.numbers.list()
                if n.status == "not_assigned"]
        res = client.telephony.numbers.attach(free[0], agent_id="sales-agent")
        print(res.numbers[0].ok, res.numbers[0].error)

Auth: telephony is org-scoped, so it needs ``TokenAuth``/``JWTAuth`` with an org
handle (``UNPOD_PLATFORM_TOKEN`` + ``UNPOD_ORG_HANDLE``). A Bearer
``UNPOD_API_KEY`` alone is refused.

Not to be confused with ``client.numbers`` / ``client.trunks``, an older surface
on a different service. Guide: ``docs/08-telephony.md``.
"""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.telephony.calls import (
    CALL_API_PATHS,
    CallList,
    CallsResource,
    InboundConfig,
    InboundResource,
    Leg,
    PlayResult,
    Recording,
    TelephonyAPIError,
    TelephonyCall,
    WebhookSecret,
    WebhookSecretResource,
)
from unpod.telephony.events import (
    CallEvent,
    DigitsRequest,
    EventName,
    WebhookSignatureError,
    parse_call_event,
    parse_digits_request,
    verify_signature,
)


def _as_number_list(numbers: "str | Sequence[str]") -> list[str]:
    """Coerce one E.164 number or a sequence of them to ``list[str]``.

    A bare ``str`` is ONE number, never iterated character-by-character
    (``list("+91…")`` → ``["+", "9", "1", …]`` is the trap this guards).
    """
    if isinstance(numbers, str):
        return [numbers]
    return [str(n) for n in numbers]

# ── models ───────────────────────────────────────────────────────────────────


class Number(BaseModel):
    """A phone number and whether you can attach it.

    ``number`` is E.164 (``+919876543210``); every method on
    ``client.telephony.numbers`` takes this, never an internal id.

    ``status``:

    - ``"not_assigned"``  free in the pool, attachable
    - ``"assigned"``      attached to your org
    - ``"closed"``        not attachable (for example, inactive)

    ``not_assigned`` always means an attach can succeed: a free but inactive
    number reports ``closed``.
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    number: str | None = None
    status: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _derive_status(cls, data):
        """Derive ``status`` from the API's ``state`` + ``active`` fields.

        The API sends ``state`` as ``"ASSIGNED"`` / ``"NOT_ASSIGNED"``.
        """
        if not isinstance(data, dict) or "status" in data:
            return data
        state = str(data.get("state") or "").upper()
        active = data.get("active", True)
        if state == "ASSIGNED":
            data["status"] = "assigned"
        elif state == "NOT_ASSIGNED" and active:
            data["status"] = "not_assigned"
        else:
            data["status"] = "closed"
        return data


class Trunk(BaseModel):
    """Your own SIP carrier trunk (beta). Secrets come back masked.

    Not needed for Unpod numbers: use ``client.telephony.numbers.attach``.
    """

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
    """Where your carrier sends inbound calls: the ingress host and the numbers."""

    model_config = ConfigDict(extra="allow")

    ingress: str = ""
    dids: list[str] = Field(default_factory=list)
    accepted_source_ips: list[str] = Field(default_factory=list)
    region: str | None = None


class NumberResult(BaseModel):
    """Outcome for one number of an attach/detach.

    Attach and detach are partial-success: check ``ok`` and ``error`` on every
    item, since one number can fail while another succeeds.
    """

    model_config = ConfigDict(extra="allow")

    number_id: int
    number: str | None = None
    connection_state: str | None = None
    agent_id: str | None = None  # set when the number is attached to an agent
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
    """Result of :meth:`NumbersResource.attach`; one ``NumberResult`` per number."""

    model_config = ConfigDict(extra="allow")

    agent_id: str | None = None
    numbers: list[NumberResult] = Field(default_factory=list)
    message: str | None = None


class AgentDetachResult(BaseModel):
    """Result of :meth:`NumbersResource.detach`; one ``NumberResult`` per number."""

    model_config = ConfigDict(extra="allow")

    numbers: list[NumberResult] = Field(default_factory=list)
    message: str | None = None


class NumberOverview(BaseModel):
    """Status of one number: is it linked, what answers it, is it in sync.

    A number whose ``connection_state`` is ``"NOT_LINKED"`` cannot place calls yet.
    """

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
    """``client.telephony.numbers``: list, attach and detach your numbers."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(self) -> list[Number]:
        """The free pool plus your org's own numbers (never another org's).

        Pick ``status == "not_assigned"`` to find a number you can attach.
        """
        resp = unwrap_data(
            await self._http.get(
                "/telephony/numbers/", params={"include_assigned": "true"}
            )
        )
        return [Number(**item) for item in (resp or [])]

    async def attach(
        self,
        numbers: "str | Sequence[str]",
        *,
        agent_id: str | None = None,
        attach_type: str | None = None,
        pipe_id: str | None = None,
        bridge_slug: str | None = None,
        region: str | None = None,
    ) -> AgentAttachResult:
        """Attach one or more numbers so calls to them are answered.

        Args:
            numbers: One E.164 number (``"+919876543210"``) or a list of them.
            agent_id: The agent that answers (for ``attach_type="agent"``).
            attach_type: Who answers the number:
                ``"agent"`` (default) your AI agent;
                ``"pipeline"`` a speech pipe, needs ``pipe_id``;
                ``"human_call"`` no agent, your app drives calls through
                ``client.telephony.calls`` / ``inbound`` (omit ``agent_id``).
            pipe_id: Required when ``attach_type="pipeline"``.
            bridge_slug, region: Advanced; leave unset.

        Partial-success: check ``ok``/``error`` on each item of ``result.numbers``.
        A request the server rejects outright raises ``httpx.HTTPStatusError``;
        the reason is in ``e.response.text``.
        """
        body: dict = {"numbers": _as_number_list(numbers)}
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

    async def detach(self, numbers: "str | Sequence[str]") -> AgentDetachResult:
        """Detach one or more E.164 numbers; the inverse of :meth:`attach`.

        Only the numbers are needed. A detached number is released, not deleted,
        so it can be attached again later. Partial-success, like ``attach``.
        """
        resp = await self._http.post(
            "/telephony/numbers/detach/", json={"numbers": _as_number_list(numbers)}
        )
        return AgentDetachResult(**unwrap_data(resp))


class TrunksResource:
    """``client.telephony.trunks``: bring your own SIP carrier (beta).

    Not needed for Unpod numbers: to wire a number to an agent use
    ``client.telephony.numbers.attach``. Trunk methods take numeric
    ``number_ids``, not E.164 numbers.
    """

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
    """``client.telephony``: ``numbers``, ``calls``, ``inbound``,
    ``webhook_secret``, ``trunks`` (beta) and :meth:`overview`."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http
        self.numbers = NumbersResource(http)
        self.trunks = TrunksResource(http)
        self.calls = CallsResource(http)
        self.inbound = InboundResource(http)
        self.webhook_secret = WebhookSecretResource(http)

    async def overview(self) -> list[NumberOverview]:
        """One :class:`NumberOverview` per number of your org."""
        resp = unwrap_data(await self._http.get("/telephony/overview/"))
        return [NumberOverview(**item) for item in (resp or [])]


__all__ = [
    "CALL_API_PATHS",
    "AgentAttachResult",
    "AgentDetachResult",
    "AttachResult",
    "CallEvent",
    "CallList",
    "CallsResource",
    "DetachResult",
    "DigitsRequest",
    "EventName",
    "InboundConfig",
    "InboundResource",
    "Leg",
    "Number",
    "NumberOverview",
    "NumberResult",
    "NumbersResource",
    "OriginEndpoint",
    "PlayResult",
    "Recording",
    "TelephonyAPIError",
    "TelephonyCall",
    "TelephonyNamespace",
    "Trunk",
    "TrunksResource",
    "WebhookSecret",
    "WebhookSecretResource",
    "WebhookSignatureError",
    "parse_call_event",
    "parse_digits_request",
    "verify_signature",
]
