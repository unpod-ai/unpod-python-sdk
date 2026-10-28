"""Flows resource: autonomous-agent campaigns.

Every path is the backend's ``/api/v2/platform/journey/v1`` proxy, which
forwards to the autonomous-agent service with the caller's org and space checked — the
same entry style as ``calls`` (``/api/v2/platform/speech/v1``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any, TypeVar

import httpx

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.management._spaces import SpaceRef, SpaceResolver
from unpod.management.flow_channels import ChannelsResource
from unpod.models import Channel, Flow

if TYPE_CHECKING:
    from unpod.flows import Flow as FlowBuilder

JOURNEY_BASE = "/api/v2/platform/journey/v1"

_T = TypeVar("_T")

_PILOTS_PATH = "/api/v2/platform/agents/"
#: How many journeys ``GET journeys`` returns at most (the service's default).
_JOURNEY_LIST_CAP = 100
_SDK_AGENTS_PATH = "/api/v2/platform/speech/v1/agents"
#: Brains that can answer a message turn; ``endpoint`` and ``runner`` cannot.
_MESSAGE_BRAINS = frozenset({"prompt", "playbook"})
#: Where a flow's leads come from: a Space, a CSV upload, an API push, or a mailbox.
LEAD_SOURCE_TYPES = frozenset({"space", "csv", "api_push", "email"})


class FlowValidationError(Exception):
    """The service refused a flow (422), or a flow's state refused the call (409).

    ``problems`` holds one readable line per issue, e.g.
    ``"step 'step_1': fixed content needs a body or a template"``.
    """

    def __init__(self, status_code: int, problems: list[str]) -> None:
        super().__init__("; ".join(problems) or f"http {status_code}")
        self.status_code = status_code
        self.problems = problems


def _idempotency(key: str | None) -> dict[str, Any]:
    """``post`` kwargs carrying a create's ``Idempotency-Key``, or none."""
    return {"headers": {"Idempotency-Key": key}} if key else {}


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


#: A flow as a caller may hold it: its id, or a flow object carrying one.
FlowRef = str | Flow


def _flow_id(flow: FlowRef) -> str:
    return flow if isinstance(flow, str) else str(flow.flow_id)


def _body(flow: Any) -> dict[str, Any]:
    """A ``Flow`` sends its declared fields only; a builder (``unpod.flows``)
    sends its definition; a dict is sent as given."""
    if hasattr(flow, "to_definition"):
        return flow.to_definition()
    if isinstance(flow, Flow):
        return flow.model_dump(mode="json", exclude_none=True, include=set(Flow.model_fields))
    return flow


class FlowsResource:
    """Create, edit and switch autonomous-agent flows."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http
        self._spaces = SpaceResolver(http)
        #: ``channels(space)`` lists sending accounts as before;
        #: ``channels.list`` / ``channels.connect`` work with ``Channel`` objects.
        self.channels = ChannelsResource(http, self._spaces)

    async def create(
        self, space_id: SpaceRef, flow: Flow | FlowBuilder | dict[str, Any], idempotency_key: str | None = None
    ) -> Flow:
        """Save a new flow, created off. Leave out ``flow_id`` and the service
        picks one (returned on the result). Raises ``FlowValidationError``.

        ``idempotency_key``: retrying with the same key returns the flow the
        first attempt made instead of a second one (use one key per flow, e.g.
        a uuid, when a create may time out and be retried).
        """
        resp = unwrap_data(
            await refused_as_validation(
                self._http.post(
                    f"{JOURNEY_BASE}/spaces/{await self._spaces.slug(space_id)}/flows", json=_body(flow), **_idempotency(idempotency_key)
                )
            )
        )
        return Flow(**resp)

    async def create_from_template(
        self,
        space_id: SpaceRef,
        template_id: str,
        name: str | None = None,
        flow_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> Flow:
        """A new draft flow from one of ``templates()`` (``idempotency_key`` as on ``create``)."""
        body = {"template_id": template_id, "name": name, "flow_id": flow_id}
        resp = unwrap_data(
            await refused_as_validation(
                self._http.post(
                    f"{JOURNEY_BASE}/spaces/{await self._spaces.slug(space_id)}/flows/from-template",
                    json={k: v for k, v in body.items() if v is not None},
                    **_idempotency(idempotency_key),
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

    async def agents(self) -> list[dict[str, Any]]:
        """Every agent a flow step can use: the org's pilots and its SDK agents.

        Each item's ``agent_id`` goes straight into a step's ``agent_id``: a
        pilot's handle, or ``sv:<agent_id>`` for an agent from
        ``client.agents``. SDK agents are listed when their brain can answer a
        message (``prompt`` or ``playbook``) and are not already a pilot.
        ``sv:`` agents answer message steps only, not ``call`` steps.
        """
        if "Org-Handle" not in self._http._headers():
            # Pilots are listed per org; without the header the route 400s.
            raise ValueError("flows.agents needs an org: set UNPOD_ORG_HANDLE (platform-token mode)")
        resp = await self._http.get(_PILOTS_PATH, params=None)
        pilots = resp.get("data", []) if isinstance(resp, dict) else resp
        voice_agents = unwrap_data(await self._http.get(_SDK_AGENTS_PATH, params=None))
        handles = {p["handle"] for p in pilots}
        listed: list[dict[str, Any]] = [
            {"agent_id": p["handle"], "name": p.get("name", ""), "kind": "pilot"} for p in pilots
        ]
        for agent in voice_agents:
            brain = (agent.get("brain") or {}).get("type")
            if brain in _MESSAGE_BRAINS and agent["agent_id"] not in handles:
                listed.append(
                    {"agent_id": f"sv:{agent['agent_id']}", "name": agent.get("name", ""), "kind": "agent", "brain": brain}
                )
        return listed

    async def validate(self, space_id: SpaceRef, flow: Flow | FlowBuilder | dict[str, Any]) -> dict[str, Any]:
        """Check a flow without saving it. Raises ``FlowValidationError``."""
        return unwrap_data(
            await refused_as_validation(
                self._http.post(f"{JOURNEY_BASE}/spaces/{await self._spaces.slug(space_id)}/flows/validate", json=_body(flow))
            )
        )

    async def update(self, flow_id: FlowRef, flow: Flow | FlowBuilder | dict[str, Any]) -> Flow:
        """Save a new version; running journeys keep the version they started on."""
        resp = unwrap_data(
            await refused_as_validation(self._http.put(f"{JOURNEY_BASE}/flows/{_flow_id(flow_id)}", json=_body(flow)))
        )
        return Flow(**resp)

    async def get(self, flow_id: FlowRef, version: int | None = None) -> Flow:
        params = {"version": version} if version is not None else None
        resp = unwrap_data(await self._http.get(f"{JOURNEY_BASE}/flows/{_flow_id(flow_id)}", params=params))
        return Flow(**resp)

    async def list(self, space_id: SpaceRef) -> list[Flow]:
        resp = unwrap_data(await self._http.get(f"{JOURNEY_BASE}/spaces/{await self._spaces.slug(space_id)}/flows", params=None))
        return [Flow(**item) for item in resp]

    async def versions(self, flow_id: FlowRef) -> list[dict[str, Any]]:
        return unwrap_data(await self._http.get(f"{JOURNEY_BASE}/flows/{_flow_id(flow_id)}/versions", params=None))

    async def activate(self, flow_id: FlowRef) -> dict[str, Any]:
        """Turn the flow on. Raises ``FlowValidationError`` if it no longer validates."""
        return unwrap_data(
            await refused_as_validation(self._http.post(f"{JOURNEY_BASE}/flows/{_flow_id(flow_id)}/activate", json=None))
        )

    async def status(self, flow_id: FlowRef) -> dict[str, Any]:
        """``{flow_id, status, version, journeys, truncated}`` for the flow.

        ``status``/``version`` are the latest version's; ``journeys`` counts by
        status the flow's journeys of every version, newest first, up to the
        list's cap of ``_JOURNEY_LIST_CAP`` -- ``truncated`` is True when the
        cap was reached and the counts are a lower bound.
        """
        flow = await self.get(flow_id)
        journeys = unwrap_data(
            await self._http.get(
                f"{JOURNEY_BASE}/journeys", params={"space_id": flow.space_id, "flow_id": flow.flow_id}
            )
        )
        # ponytail: counts the rows the list returns; a per-flow count endpoint if flows outgrow it.
        return {
            "flow_id": flow.flow_id,
            "status": flow.status,
            "version": flow.version,
            "journeys": dict(Counter(j.get("status", "") for j in journeys)),
            "truncated": len(journeys) >= _JOURNEY_LIST_CAP,
        }

    async def pause(self, flow_id: FlowRef) -> dict[str, Any]:
        """Turn the flow off: no new journeys start; running ones finish."""
        return unwrap_data(await self._http.post(f"{JOURNEY_BASE}/flows/{_flow_id(flow_id)}/deactivate", json=None))

    async def archive(self, flow_id: FlowRef) -> dict[str, Any]:
        return unwrap_data(await self._http.post(f"{JOURNEY_BASE}/flows/{_flow_id(flow_id)}/archive", json=None))

    # ── lead sources: where a flow's contacts come from ───────────────────

    async def lead_sources(self, flow_id: FlowRef) -> list[dict[str, Any]]:
        return unwrap_data(await self._http.get(f"{JOURNEY_BASE}/flows/{_flow_id(flow_id)}/lead-sources", params=None))

    async def add_lead_source(
        self,
        flow_id: FlowRef,
        type: str,
        mode: str = "manual",
        account_id: str | None = None,
        label: str | None = None,
    ) -> dict[str, Any]:
        """Add a lead source. An ``api_push`` source returns its push token ONCE: keep it.

        ``email`` needs the mailbox's ``account_id`` (see ``channels``).
        """
        if type not in LEAD_SOURCE_TYPES:
            raise ValueError(f"type must be one of: {', '.join(sorted(LEAD_SOURCE_TYPES))}")
        body: dict[str, Any] = {"type": type, "mode": mode}
        if account_id:
            body["account_id"] = account_id
        if label:
            body["label"] = label
        return unwrap_data(await self._http.post(f"{JOURNEY_BASE}/flows/{_flow_id(flow_id)}/lead-sources", json=body))

    async def enroll_leads(self, flow_id: FlowRef, source_id: str, leads: list[dict[str, Any]]) -> dict[str, Any]:
        """Start the flow for a batch of leads (rows of a CSV), e.g. ``[{"email": ..., "name": ...}]``."""
        return unwrap_data(
            await refused_as_validation(
                self._http.post(
                    f"{JOURNEY_BASE}/flows/{_flow_id(flow_id)}/lead-sources/{source_id}/enroll", json={"leads": leads}
                )
            )
        )

    async def _lead_source_action(self, flow_id: FlowRef, source_id: str, action: str) -> dict[str, Any]:
        return unwrap_data(
            await refused_as_validation(
                self._http.post(f"{JOURNEY_BASE}/flows/{_flow_id(flow_id)}/lead-sources/{source_id}/{action}", json=None)
            )
        )

    async def fetch_leads_now(self, flow_id: FlowRef, source_id: str) -> dict[str, Any]:
        """Read a Space source's contacts now instead of waiting for its schedule."""
        return await self._lead_source_action(flow_id, source_id, "fetch-now")

    async def pause_lead_source(self, flow_id: FlowRef, source_id: str) -> dict[str, Any]:
        return await self._lead_source_action(flow_id, source_id, "pause")

    async def regenerate_lead_source_token(self, flow_id: FlowRef, source_id: str) -> dict[str, Any]:
        """A new push token for an ``api_push`` source (returned once; the old one stops working)."""
        return await self._lead_source_action(flow_id, source_id, "regenerate-token")

    async def delete_lead_source(self, flow_id: FlowRef, source_id: str) -> None:
        await self._http.delete(f"{JOURNEY_BASE}/flows/{_flow_id(flow_id)}/lead-sources/{source_id}")

    async def test_connection(self, space_id: SpaceRef, account_id: str) -> dict[str, Any]:
        """Check a connected account (mailbox, number) live: ``{ok, probed, status|reason}``.

        A passing check also clears an account the service marked as failing,
        so flows that use it can be turned on again.
        """
        return unwrap_data(
            await self._http.post(f"{JOURNEY_BASE}/spaces/{await self._spaces.slug(space_id)}/connectors/{account_id}/test", json=None)
        )

    async def link(self, flow_id: FlowRef, channel: Channel) -> Flow:
        """Wire ``channel`` into the flow, in one call.

        The flow's agent steps on that channel's kind send from it, its agent
        may use the channel's connector, and new mail to it starts journeys
        (an ``email`` lead source). Safe to repeat: what is already wired is
        left alone. Everything that could refuse is checked before anything
        is written: a channel from another space, or a flow already listening
        on another mailbox (a flow listens on one), changes nothing.
        """
        if not channel.ready:
            raise ValueError("connect the channel first with client.flows.channels.connect(channel)")
        flow = await self.get(flow_id)
        if flow.space_id and channel.space and flow.space_id != channel.space:
            raise ValueError(f"the channel belongs to space {channel.space!r}, the flow to {flow.space_id!r}")
        add_source = channel.connector_type == "email" and await self._needs_email_source(
            flow, channel.account_id
        )
        definition = _author_definition(flow)
        changed = False
        agent_steps = [
            step
            for step in definition["steps"]
            if (step.get("agentic") or {}).get("enabled") and step.get("action") == channel.connector_type
        ]
        for step in agent_steps:
            if step.get("connector_account_ids") != [channel.account_id]:
                step["connector_account_ids"] = [channel.account_id]
                changed = True
        tools = list(definition.get("agentic_tools") or [])
        if agent_steps and channel.connector_id and channel.connector_id not in tools:
            # The agent's tools are an allowlist that fails closed: without
            # the connector, the steps above would send nothing.
            definition["agentic_tools"] = [*tools, channel.connector_id]
            changed = True
        if changed:
            flow = await self.update(flow, definition)
        if add_source:
            await self.add_lead_source(flow, "email", account_id=channel.account_id)
        return flow

    async def _needs_email_source(self, flow: Flow, account_id: str | None) -> bool:
        """False when the flow already listens on this mailbox; refuses another."""
        for source in await self.lead_sources(flow):
            if source.get("type") != "email":
                continue
            current = (source.get("config") or {}).get("account_id") or source.get("account_id")
            if current == account_id:
                return False
            raise ValueError(
                f"flow {flow.flow_id!r} already listens on another mailbox ({current}); "
                "remove that email lead source first"
            )
        return True


#: What a flow's author writes, i.e. what an update may carry. A stored flow
#: comes back with more (storage stamps, the lead sources the service carries
#: forward itself) and the service refuses unknown keys on a save; the id,
#: space and version are set by the service. ``status`` is kept: a definition
#: without one is read as a draft, which would switch a live flow off.
_AUTHOR_KEYS = (
    "name",
    "description",
    "status",
    "steps",
    "triggers",
    "exit_outcomes",
    "agentic_profile",
    "agentic_tools",
    "agentic_plan",
    "agentic_follow_ups",
    "agentic_review_sensitive",
    "agentic_review_topics",
)


def _author_definition(flow: Flow) -> dict[str, Any]:
    """The stored flow as an update body, keeping fields this SDK does not declare."""
    stored = flow.model_dump(mode="json", exclude_none=True)
    definition = {key: stored[key] for key in _AUTHOR_KEYS if key in stored}
    definition["steps"] = [dict(step) for step in definition.get("steps") or []]
    return definition
