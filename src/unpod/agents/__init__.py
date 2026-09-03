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


class BackgroundSound(StrEnum):
    """Ambience mixed under the agent's speech for the whole call.

    A ``StrEnum``, so the plain strings keep working and the four rooms the
    platform actually ships are discoverable. The server refuses anything else
    rather than substituting a default: a typo plays silence, which is
    noticeable, instead of confidently the wrong room.
    """

    office = "office"
    city = "city"
    forest = "forest"
    crowded_room = "crowded_room"
    none = "none"


class NoiseCancellation(StrEnum):
    """Which inbound noise canceller runs on this agent's calls.

    Cleans the CALLER's audio before it reaches STT, so it changes what the
    agent hears, never what it says. A ``StrEnum``, so plain strings keep
    working.

    ``hush`` and ``rnnoise`` run inside the media pipeline and work on any
    transport; ``bvc`` / ``bvc_telephony`` are LiveKit Cloud's own canceller and
    apply at the room layer instead. ``none`` runs none at all.

    Omitting the field is NOT the same as ``none``: unset leaves the
    deployment's own default in charge, while ``none`` is this agent asking for
    raw audio. A backend whose model or licence is missing on the server
    degrades to no filtering rather than failing the call, so a value accepted
    here is not by itself proof the model loaded — the worker logs which one it
    resolved.
    """

    rnnoise = "rnnoise"
    hush = "hush"
    aic = "aic"
    krisp = "krisp"
    bvc = "bvc"
    bvc_telephony = "bvc-telephony"
    none = "none"


#: Level bounds for that bed — a GAIN, not a percentage. Checked here so a
#: slider sending 30 fails at the call site rather than on a round trip.
VOLUME_MIN = 0.0
VOLUME_MAX = 1.0


def check_background_volume(value: float | None) -> float | None:
    """*value* if it is a usable level, else raise. ``None`` passes through.

    Public because the two management resources that also take a level
    (``client.agent.voice.create``, ``client.pipes.create``) import it — one
    range, checked identically wherever a level is accepted.

    ``None`` means "the platform default" (a bed plays at the server's own
    level), NOT silence, and not "unchanged" — for that, omit the argument.
    """
    if value is None:
        return None
    level = float(value)
    if not VOLUME_MIN <= level <= VOLUME_MAX:
        raise ValueError(
            f"background_sound_volume must be between {VOLUME_MIN} and "
            f"{VOLUME_MAX} (a gain, not a percentage); got {value!r}"
        )
    return level


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
    # The profile's display name, joined server-side. ``name`` below is the
    # AGENT's name — the two are unrelated and were easy to confuse when only
    # the opaque id came back.
    voice_profile_name: str | None = None
    name: str = ""
    brain: dict[str, Any] = Field(default_factory=dict)
    brain_execution: str = "bridge"
    is_default: bool = False
    #: The domain dictionary this voice speaks with (``client.domain_dictionaries``),
    #: or None. This is the tag the runtime resolves — an agent created without
    #: one applies no dictionary, whatever dictionaries exist in the project.
    domain: str | None = None
    #: Which inbound noise canceller this agent's calls run. ``None`` means the
    #: deployment's own default, not "no cancellation".
    noise_cancellation: str | None = None
    #: Ambience under the agent's speech: which room, whether it plays, and how
    #: loud. ``background_sound_volume`` of None means the platform's own
    #: default level, not silence — ambience is switched off with
    #: ``background_sound_enabled``.
    background_sound: str | None = None
    background_sound_enabled: bool = True
    background_sound_volume: float | None = None


class Agent(BaseModel):
    """An agent: its brain, and every voice it speaks with."""

    model_config = ConfigDict(extra="allow")

    agent_id: str
    name: str = ""
    brain: dict[str, Any] = Field(default_factory=dict)
    brain_execution: str = "bridge"
    voices: list[AgentVoice] = Field(default_factory=list)
    background_sound: str | None = None
    background_sound_enabled: bool = True
    background_sound_volume: float | None = None
    #: Which inbound noise canceller this agent's calls run. ``None`` means the
    #: deployment's own default, not "no cancellation".
    noise_cancellation: str | None = None


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
        domain: str | None = None,
        background_sound: BackgroundSound | str | None = None,
        background_sound_enabled: bool | None = None,
        background_sound_volume: float | None = None,
        noise_cancellation: NoiseCancellation | str | None = None,
    ) -> AgentVoice:
        """Create an agent with its first voice.

        ``domain`` names the dictionary this agent speaks with — author it with
        ``client.domain_dictionaries.upsert()``, or use a bundled seed
        (``banking``, ``real_estate``, ``hospital``) which needs no rows of your
        own. The tag is stamped on the agent row, which is what the call path
        resolves; a voice added later inherits it.

        ``noise_cancellation`` picks the canceller that cleans the CALLER's
        audio before it reaches STT (:class:`NoiseCancellation`). Omit it to
        leave the deployment's default in charge — that is not the same as
        ``"none"``, which asks for no cancellation on this agent.

        ``background_sound`` picks the room the caller hears behind the agent
        (:class:`BackgroundSound`); ``background_sound_volume`` is its gain,
        0.0-1.0, defaulting to the platform's level when omitted. The two are
        separate from ``background_sound_enabled`` on purpose: switching
        ambience off keeps the chosen room, so turning it back on needs no
        second decision, and a volume of 0.0 is a SILENT bed rather than the
        off switch.
        """
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
            ("domain", domain),
            # Sent only when named. An omitted level means the platform's
            # default, and pinning today's number into the request would keep
            # this agent on it after the default moves.
            ("background_sound", background_sound),
            ("background_sound_enabled", background_sound_enabled),
            ("background_sound_volume", check_background_volume(background_sound_volume)),
            ("noise_cancellation", noise_cancellation),
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
        domain: str | None = None,
        background_sound: BackgroundSound | str | None = None,
        background_sound_enabled: bool | None = None,
        background_sound_volume: float | None = None,
        noise_cancellation: NoiseCancellation | str | None = None,
        **fields: Any,
    ) -> Agent:
        """Update the agent. A brain change reaches every voice.

        ``domain`` retags which dictionary the agent speaks with and reaches
        every voice; pass ``""`` to detach it from its dictionary entirely.
        Omitting it leaves the current tag alone.

        The three ``background_sound*`` arguments change the ambience the same
        way: omitted means unchanged. There is no way to send the LEVEL back to
        the platform default once set — write the level you want. ``0.0`` is a
        silent bed, not the off switch; that is
        ``background_sound_enabled=False``, which keeps the chosen room.

        ``noise_cancellation`` behaves the same: omitted means unchanged. Once
        set there is no way back to the deployment default through this call —
        name the backend you want, or ``"none"`` for no cancellation at all.
        """
        body: dict[str, Any] = dict(fields)
        if brain is not None:
            body["brain"] = brain.as_body()
        for key, value in (
            ("name", name),
            ("greeting", greeting),
            ("brain_execution", brain_execution),
            ("domain", domain),
            ("background_sound", background_sound),
            ("background_sound_enabled", background_sound_enabled),
            ("background_sound_volume", check_background_volume(background_sound_volume)),
            ("noise_cancellation", noise_cancellation),
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
    "NoiseCancellation",
    "BackgroundSound",
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
