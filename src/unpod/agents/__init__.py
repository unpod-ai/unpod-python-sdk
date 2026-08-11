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

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from unpod.management._http import AsyncHTTPClient, unwrap_data

_BASE = "/api/v2/platform/speech/v1/agents"


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


class Agent(BaseModel):
    """An agent: its brain, and every voice it speaks with."""

    model_config = ConfigDict(extra="allow")

    agent_id: str
    name: str = ""
    brain: dict[str, Any] = Field(default_factory=dict)
    brain_execution: str = "bridge"
    voices: list[AgentVoice] = Field(default_factory=list)


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

    async def attach(self, agent_id: str, number_id: str) -> dict[str, Any]:
        """Point a number at an agent."""
        resp = await self._http.post(
            f"/api/v2/platform/speech/v1/numbers/{number_id}/attach",
            json={"agent_id": agent_id},
        )
        return unwrap_data(resp)

    async def detach(self, number_id: str) -> dict[str, Any]:
        """Release a number from whatever agent holds it."""
        resp = await self._http.post(
            f"/api/v2/platform/speech/v1/numbers/{number_id}/detach", json={}
        )
        return unwrap_data(resp)


class AgentsNamespace:
    """``client.agents`` — configure, voice, and reach an agent.

    Three verbs replacing the old five-concern publish: the playbook says the
    content is ready, ``agents.voice`` says it can speak, ``agents.numbers``
    says it can be reached.
    """

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http
        self.voice = AgentVoiceResource(http)
        self.numbers = AgentNumbersResource(http)

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
    "AgentVoice",
    "AgentVoiceResource",
    "AgentsNamespace",
    "Brain",
    "Endpoint",
    "Playbook",
    "Prompt",
    "Runner",
]
