"""Noise cancellation: which canceller cleans the CALLER's audio before STT.

One knob, and the distinction every test here pins is UNSET vs ``"none"``:

* omitted — the deployment's own ``SUPERVOICE_NOISE_BACKEND`` stays in charge.
  Sending a value the SDK invented would pin today's default onto the agent.
* ``"none"`` — this agent asks for raw audio. A real value, and it must reach
  the wire.

All three creation surfaces are covered, plus the update path, because they are
different routes built from different key lists: ``client.agents.voice``
(``/v1/agents``), ``client.agent.voice`` (``/v1/agent/voice``) and
``client.pipes`` (``/v1/pipes``). A field only one of them accepts is a field
two thirds of agents cannot set.
"""

from unittest.mock import AsyncMock

import pytest

from unpod import AsyncClient, NoiseCancellation, Prompt

_VOICE = {
    "agent_id": "support",
    "voice_profile_id": "vp_1",
    "name": "support",
    "brain": {"type": "prompt", "ref": "You are helpful."},
    "noise_cancellation": "hush",
}

_PIPE = {
    "pipe_id": "PIPE_1",
    "project_id": "proj_1",
    "name": "support",
    "voice_profile_id": "vp_1",
    "agent_id": "support-abc",
    "recording": False,
    "max_call_duration_s": 3600,
    "noise_cancellation": "hush",
}


def _client() -> AsyncClient:
    return AsyncClient(api_key="test", base_url="https://example.test")


# ── client.agents.voice.create — /v1/agents ────────────────────────────────


@pytest.mark.anyio
async def test_create_sends_the_backend_and_reads_it_back() -> None:
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    voice = await client.agents.voice.create(
        "support",
        brain=Prompt("You are helpful."),
        noise_cancellation=NoiseCancellation.hush,
    )

    body = client.agents.voice._http.post.await_args.kwargs["json"]
    assert body["noise_cancellation"] == "hush"
    # The response echoes the row, so the typed field must be readable back.
    assert voice.noise_cancellation == "hush"


@pytest.mark.anyio
async def test_the_enum_and_the_plain_string_are_interchangeable() -> None:
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    await client.agents.voice.create(
        "support", brain=Prompt("hi"), noise_cancellation="hush"
    )

    body = client.agents.voice._http.post.await_args.kwargs["json"]
    assert body["noise_cancellation"] == NoiseCancellation.hush == "hush"


@pytest.mark.anyio
async def test_the_wire_name_is_hyphenated_for_bvc_telephony() -> None:
    """The member is ``bvc_telephony``; the platform spells it with a hyphen."""
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    await client.agents.voice.create(
        "support",
        brain=Prompt("hi"),
        noise_cancellation=NoiseCancellation.bvc_telephony,
    )

    body = client.agents.voice._http.post.await_args.kwargs["json"]
    assert body["noise_cancellation"] == "bvc-telephony"


@pytest.mark.anyio
async def test_an_unset_backend_is_left_out_of_the_request() -> None:
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    await client.agents.voice.create("support", brain=Prompt("hi"))

    body = client.agents.voice._http.post.await_args.kwargs["json"]
    assert "noise_cancellation" not in body


@pytest.mark.anyio
async def test_none_is_a_value_not_an_absence() -> None:
    """``"none"`` switches cancellation off for this agent. Dropping it here
    would silently leave the deployment's canceller running instead."""
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    await client.agents.voice.create(
        "support", brain=Prompt("hi"), noise_cancellation=NoiseCancellation.none
    )

    body = client.agents.voice._http.post.await_args.kwargs["json"]
    assert body["noise_cancellation"] == "none"


# ── client.agents.update — retag a live agent ──────────────────────────────


@pytest.mark.anyio
async def test_update_sends_the_backend() -> None:
    client = _client()
    client.agents._http.put = AsyncMock(return_value={"agent_id": "support"})

    await client.agents.update("support", noise_cancellation="rnnoise")

    body = client.agents._http.put.await_args.kwargs["json"]
    assert body["noise_cancellation"] == "rnnoise"


@pytest.mark.anyio
async def test_update_leaves_it_alone_when_unset() -> None:
    client = _client()
    client.agents._http.put = AsyncMock(return_value={"agent_id": "support"})

    await client.agents.update("support", name="Support")

    body = client.agents._http.put.await_args.kwargs["json"]
    assert "noise_cancellation" not in body


# ── client.agent.voice.create — /v1/agent/voice ────────────────────────────


@pytest.mark.anyio
async def test_the_older_surface_sends_it_too() -> None:
    client = _client()
    client.agent.voice._http.post = AsyncMock(return_value=_PIPE)

    pipe = await client.agent.voice.create(
        name="support", prompt="hi", noise_cancellation="hush"
    )

    body = client.agent.voice._http.post.await_args.kwargs["json"]
    assert body["noise_cancellation"] == "hush"
    assert pipe.noise_cancellation == "hush"


# ── client.pipes.create — /v1/pipes (deprecated, writes a complete row) ────


@pytest.mark.anyio
async def test_the_pipes_surface_sends_it_too() -> None:
    client = _client()
    client.pipes._http.post = AsyncMock(return_value=_PIPE)

    await client.pipes.create(
        name="support", voice_profile="vp_1", noise_cancellation="krisp"
    )

    body = client.pipes._http.post.await_args.kwargs["json"]
    assert body["noise_cancellation"] == "krisp"
