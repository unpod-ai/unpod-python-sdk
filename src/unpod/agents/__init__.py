"""Voice agents: ``client.agents.*``.

One creation call, four brain sources. A developer holding nothing but a
prompt — or a playbook id — can place a call without first standing up and
publishing an agent runner::

    from unpod import Client, Playbook, Prompt, Endpoint, Runner

    client = Client()

    client.agents.voice.create(                    # managed: deploy nothing
        agent_id="gamestop-support",
        name="Gamestop Support",
        voice_profile="hindi-female-warm-hd",
        brain=Playbook("PB_abc123"),
    )
    client.agents.voice.add(                       # same brain, second language
        "gamestop-support", voice_profile="en-female-warm-hd")

    client.agents.numbers.attach("gamestop-support", "+9180…")

The brain is ONE parameter carrying a typed object, not four mutually
exclusive keyword arguments. Passing two sources is unrepresentable, so there
is no precedence rule to document, no validation error to write, and no way
for the wrong agent to reach production because someone set two fields.

An agent is a group of rows sharing one ``agent_id``, one per voice. The brain
belongs to the agent, so editing it reaches every voice.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from unpod.management._http import AsyncHTTPClient, unwrap_data

_BASE = "/api/v2/platform/speech/v1/agents"


# ── platform tools + ambience ────────────────────────────────────────────────


class BackgroundSound(StrEnum):
    """Continuous ambience mixed under the agent's speech.

    ``none`` is off, and off is the default — an agent that does not ask for a
    bed gets no mixer at all.
    """

    office = "office"
    city = "city"
    forest = "forest"
    crowded_room = "crowded_room"
    none = "none"


class HandoverTool(BaseModel):
    """Escalation to a human.

    ``numbers`` is an ORDERED fallback list: the worker dials the first, and on
    a failure walks to the next. There is no concurrency pool, so two calls
    escalating at once can reach the same person.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    numbers: list[str] = Field(default_factory=list)


class VoicemailTool(BaseModel):
    """The line left on an answering machine before hanging up.

    The tool itself cannot be disabled — a call parked on a voicemail box holds
    a concurrency slot and bills the whole box timeout. Only the wording is
    configurable; unset uses the platform default.
    """

    model_config = ConfigDict(extra="forbid")

    message: str | None = None


class ToolsConfig(BaseModel):
    """Per-agent platform-tool config.

    ``end_call`` and ``voicemail_detector`` are always enabled and are therefore
    not representable here as disabled — by design, not omission.

    ``extra="forbid"`` is the opposite choice from :class:`Agent` on purpose: a
    typo in a REQUEST should fail at the call site, while an unknown field in a
    RESPONSE must never break an older SDK.

    Reachability, which surprises everyone once: a platform tool runs when a
    playbook checkpoint it is attached to is entered. The playbook must declare
    it (``type: python``, matching ``id``) and reference it from an ``on_enter``.
    Enabling a tool here decides whether the implementation exists behind that
    id — it does not, on its own, make the agent use it. Playbook brains only:
    prompt and endpoint brains have no checkpoints.
    """

    model_config = ConfigDict(extra="forbid")

    handover: HandoverTool | None = None
    voicemail: VoicemailTool | None = None

    def as_body(self) -> dict[str, Any]:
        """Only the sections actually set, so a PATCH never blanks a sibling."""
        body: dict[str, Any] = {}
        if self.handover is not None:
            body["handover"] = self.handover.model_dump()
        if self.voicemail is not None:
            body["voicemail"] = self.voicemail.model_dump()
        return body


def _tools_body(tools: "ToolsConfig | dict[str, Any] | None") -> dict[str, Any] | None:
    """Normalise the ``tools=`` argument to a request body."""
    if tools is None:
        return None
    if isinstance(tools, ToolsConfig):
        return tools.as_body()
    return dict(tools)


# ── the brain union ──────────────────────────────────────────────────────────


class Brain(BaseModel):
    """Base for the four brain sources. Not instantiated directly."""

    model_config = ConfigDict(extra="forbid")

    type: str
    ref: str | None = None
    cfg: dict[str, Any] | None = None

    def as_body(self) -> dict[str, Any]:
        body: dict[str, Any] = {"type": self.type}
        if self.ref is not None:
            body["ref"] = self.ref
        if self.cfg is not None:
            body["cfg"] = self.cfg
        return body


class Playbook(Brain):
    """A published playbook, by id. A live reference, not a snapshot.

    Editing the playbook changes the agent on the next call.
    """

    type: str = "playbook"

    def __init__(self, playbook_id: str, **kw: Any) -> None:
        super().__init__(ref=playbook_id, **kw)


class Prompt(Brain):
    """A bare prompt. Runs as a one-node playbook; you deploy nothing."""

    type: str = "prompt"

    def __init__(self, text: str, **kw: Any) -> None:
        super().__init__(ref=text, **kw)


class Runner(Brain):
    """Your own agent worker, registered under this agent's id.

    ``agent_id`` is optional: a runner normally registers under the agent's own
    id. Pass one only when agent ``sales-bot`` is served by a runner registered
    as ``my-brain-v2``.
    """

    type: str = "runner"

    def __init__(self, agent_id: str | None = None, **kw: Any) -> None:
        super().__init__(ref=agent_id, **kw)


class Endpoint(Brain):
    """A remote OpenAI-compatible chat-completions URL that we call.

    ``api_key`` is stored on the agent and redacted on every read path; it is
    never echoed back by the API.
    """

    type: str = "endpoint"

    def __init__(
        self,
        url: str,
        *,
        model: str | None = None,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
        query: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        max_retries: int | None = None,
        **kw: Any,
    ) -> None:
        cfg = {
            k: v
            for k, v in {
                "model": model,
                "api_key": api_key,
                "headers": headers,
                "query": query,
                "extra_body": extra_body,
                "timeout_s": timeout_s,
                "max_retries": max_retries,
            }.items()
            if v is not None
        }
        super().__init__(ref=url, cfg=cfg or None, **kw)


# ── responses ────────────────────────────────────────────────────────────────


class AgentVoice(BaseModel):
    """One voice of an agent."""

    model_config = ConfigDict(extra="allow")

    agent_id: str
    voice_profile_id: str | None = None
    name: str = ""
    brain: dict[str, Any] = Field(default_factory=dict)
    brain_execution: str = "bridge"
    is_default: bool = False
    tools: dict[str, Any] = Field(default_factory=dict)
    background_sound: str | None = None


class Agent(BaseModel):
    """An agent: its brain, and every voice it speaks with."""

    model_config = ConfigDict(extra="allow")

    agent_id: str
    name: str = ""
    brain: dict[str, Any] = Field(default_factory=dict)
    brain_execution: str = "bridge"
    voices: list[AgentVoice] = Field(default_factory=list)
    tools: dict[str, Any] = Field(default_factory=dict)
    background_sound: str | None = None


# ── resources ────────────────────────────────────────────────────────────────


class AgentVoiceResource:
    """``client.agents.voice`` — create an agent and manage its voices."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def create(
        self,
        agent_id: str,
        *,
        brain: Brain,
        name: str | None = None,
        voice_profile: str | None = None,
        greeting: str | None = None,
        recording: bool = False,
        max_call_duration_s: int = 3600,
        max_concurrent: int = 1,
        brain_execution: str | None = None,
        tools: ToolsConfig | dict[str, Any] | None = None,
        background_sound: BackgroundSound | str | None = None,
    ) -> AgentVoice:
        """Create an agent with its first voice."""
        body: dict[str, Any] = {
            "agent_id": agent_id,
            "name": name or agent_id,
            "brain": brain.as_body(),
            "recording": recording,
            "max_call_duration_s": max_call_duration_s,
            "max_concurrent": max_concurrent,
        }
        for key, value in (
            ("voice_profile", voice_profile),
            ("greeting", greeting),
            ("brain_execution", brain_execution),
            ("tools", _tools_body(tools)),
            ("background_sound", str(background_sound) if background_sound else None),
        ):
            if value is not None:
                body[key] = value
        return AgentVoice(**unwrap_data(await self._http.post(_BASE, json=body)))

    async def add(
        self, agent_id: str, *, voice_profile: str, greeting: str | None = None
    ) -> AgentVoice:
        """Add a voice. It inherits the agent's brain unchanged."""
        body: dict[str, Any] = {"voice_profile": voice_profile}
        if greeting is not None:
            body["greeting"] = greeting
        resp = await self._http.post(f"{_BASE}/{agent_id}/voices", json=body)
        return AgentVoice(**unwrap_data(resp))

    async def remove(self, agent_id: str, voice_profile_id: str) -> None:
        """Remove one voice. The agent keeps the rest."""
        await self._http.delete(f"{_BASE}/{agent_id}/voices/{voice_profile_id}")


class AgentNumbersResource:
    """``client.agents.numbers`` — reach."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def attach(
        self,
        agent_id: str,
        number: str,
        *,
        number_id: str | None = None,
        inbound_trunk_id: str | None = None,
        outbound_trunk_id: str | None = None,
    ) -> dict[str, Any]:
        """Attach an E.164 number directly to an sv_agents agent.

        This intentionally calls Supervoice's speech management plane rather
        than backend-core's separate telephony agent registry. Supervoice
        validates ``agent_id`` in ``sv_agents`` and upserts ``sv_numbers``.

        ``number_id`` is the upstream (postgres) id. Supervoice matches an
        EXISTING row by the E.164 in the body, so omitting it works for any
        number already synced — but on the upsert branch the path id is stored
        as ``unpod_number_id``, the cross-plane back-reference. Passing the
        real id there keeps that reference meaningful instead of recording a
        phone number as a database id.
        """
        body: dict[str, Any] = {"number": number, "agent_id": agent_id}
        if inbound_trunk_id is not None:
            body["inbound_trunk_id"] = inbound_trunk_id
        if outbound_trunk_id is not None:
            body["outbound_trunk_id"] = outbound_trunk_id
        resp = await self._http.post(
            f"/api/v2/platform/speech/v1/numbers/{number_id or number}/attach",
            json=body,
        )
        return unwrap_data(resp)

    async def detach(self, number_id: str) -> dict[str, Any]:
        """Release a number from whatever agent holds it.

        DELETE on ``/attach``, not POST on ``/detach``: the platform models
        detaching as removing the attachment, and no ``/detach`` route exists.
        """
        resp = await self._http.delete_with_response(
            f"/api/v2/platform/speech/v1/numbers/{number_id}/attach"
        )
        return unwrap_data(resp)


class AgentToolsResource:
    """``client.agents.tools`` — read and modify one agent's tool config.

    Implemented over GET + PUT of the agent itself. There is no
    ``/agents/{id}/tools`` endpoint, and there must not be one: the Django
    speech proxy enumerates agent routes explicitly and its passthrough
    excludes ``agents``, so a sub-route would 404 through the proxy while
    working against supervoice directly. The proxy has already been bitten by
    this once, for call analytics.
    """

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def get(self, agent_id: str) -> ToolsConfig:
        """This agent's tool config, typed."""
        resp = unwrap_data(await self._http.get(f"{_BASE}/{agent_id}"))
        raw = (resp or {}).get("tools") or {}
        return ToolsConfig(**raw)

    async def set(
        self,
        agent_id: str,
        *,
        handover: HandoverTool | None = None,
        voicemail: VoicemailTool | None = None,
    ) -> Agent:
        """Change the given sections, leaving the rest as they are.

        Read-modify-write, and a MERGE rather than a replace: setting
        ``voicemail`` must not silently wipe a configured ``handover``.
        """
        resp = unwrap_data(await self._http.get(f"{_BASE}/{agent_id}"))
        merged: dict[str, Any] = dict((resp or {}).get("tools") or {})
        if handover is not None:
            merged["handover"] = handover.model_dump()
        if voicemail is not None:
            merged["voicemail"] = voicemail.model_dump()
        put = await self._http.put(f"{_BASE}/{agent_id}", json={"tools": merged})
        return Agent(**unwrap_data(put))


class AgentsNamespace:
    """``client.agents`` — configure, voice, and reach an agent.

    Three verbs replacing the old five-concern publish: the playbook says the
    content is ready, ``agents.voice`` says it can speak, ``agents.numbers``
    says it can be reached.
    """

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http
        self.voice = AgentVoiceResource(http)
        self.tools = AgentToolsResource(http)
        self.numbers = AgentNumbersResource(http)

    async def create(self, agent_id: str, *, brain: Brain, **kwargs: Any) -> AgentVoice:
        """Backward-compatible alias for ``client.agents.voice.create``.

        Older SDK examples used ``client.agents.create``. Keep that spelling
        while routing through the typed brain serializer, so Brain objects
        never leak into the JSON encoder.
        """
        return await self.voice.create(agent_id, brain=brain, **kwargs)

    async def list(self) -> list[Agent]:
        """Every agent in the project, each with all its voices."""
        return [Agent(**item) for item in unwrap_data(await self._http.get(_BASE))]

    async def get(self, agent_id: str) -> Agent:
        """One agent: its brain, and every voice it speaks with."""
        return Agent(**unwrap_data(await self._http.get(f"{_BASE}/{agent_id}")))

    async def update(
        self,
        agent_id: str,
        *,
        brain: Brain | None = None,
        name: str | None = None,
        greeting: str | None = None,
        brain_execution: str | None = None,
        tools: ToolsConfig | dict[str, Any] | None = None,
        background_sound: BackgroundSound | str | None = None,
        **fields: Any,
    ) -> Agent:
        """Update the agent. A brain change reaches every voice."""
        body: dict[str, Any] = dict(fields)
        if brain is not None:
            body["brain"] = brain.as_body()
        for key, value in (
            ("name", name),
            ("greeting", greeting),
            ("brain_execution", brain_execution),
            ("tools", _tools_body(tools)),
            ("background_sound", str(background_sound) if background_sound else None),
        ):
            if value is not None:
                body[key] = value
        resp = await self._http.put(f"{_BASE}/{agent_id}", json=body)
        return Agent(**unwrap_data(resp))

    async def delete(self, agent_id: str) -> None:
        """Delete the agent and every voice it speaks with."""
        await self._http.delete(f"{_BASE}/{agent_id}")


__all__ = [
    "Agent",
    "AgentNumbersResource",
    "AgentToolsResource",
    "AgentVoice",
    "AgentVoiceResource",
    "AgentsNamespace",
    "BackgroundSound",
    "Brain",
    "Endpoint",
    "HandoverTool",
    "Playbook",
    "Prompt",
    "Runner",
    "ToolsConfig",
    "VoicemailTool",
]
