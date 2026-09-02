"""Ambience: the room a caller hears behind the agent, and how loud.

Three knobs that are deliberately independent, and the tests below pin each
distinction because collapsing any two of them is the obvious mistake:

* ``background_sound`` — WHICH room.
* ``background_sound_enabled`` — WHETHER a bed plays. Switching it off keeps
  the chosen room, so turning it back on needs no second decision.
* ``background_sound_volume`` — HOW LOUD, as a gain in 0.0-1.0. ``0.0`` is a
  SILENT bed, not the off switch. Omitting it means the platform's own default
  level, never silence.

Both creation surfaces are covered: ``client.agents.voice`` (``/v1/agents``)
and ``client.agent.voice`` (``/v1/agent/voice``), which are different routes on
the platform and were built from different key lists.
"""

from unittest.mock import AsyncMock

import pytest

from unpod import AsyncClient, BackgroundSound, Prompt

_VOICE = {
    "agent_id": "support",
    "voice_profile_id": "vp_1",
    "name": "support",
    "brain": {"type": "prompt", "ref": "You are helpful."},
    "background_sound": "forest",
    "background_sound_enabled": True,
    "background_sound_volume": 0.45,
}

_PIPE = {
    "pipe_id": "PIPE_1",
    "project_id": "proj_1",
    "name": "support",
    "voice_profile_id": "vp_1",
    "agent_id": "support-abc",
    "recording": False,
    "max_call_duration_s": 3600,
    "background_sound": "office",
    "background_sound_volume": 0.2,
}


def _client() -> AsyncClient:
    return AsyncClient(api_key="test", base_url="https://example.test")


# ── client.agents.voice.create — /v1/agents ────────────────────────────────


@pytest.mark.anyio
async def test_create_sends_the_room_and_its_level() -> None:
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    voice = await client.agents.voice.create(
        "support",
        brain=Prompt("You are helpful."),
        background_sound=BackgroundSound.forest,
        background_sound_volume=0.45,
    )

    body = client.agents.voice._http.post.await_args.kwargs["json"]
    assert body["background_sound"] == "forest"
    assert body["background_sound_volume"] == 0.45
    # The response echoes the row, so the typed fields must be readable back.
    assert voice.background_sound == "forest"
    assert voice.background_sound_volume == 0.45


@pytest.mark.anyio
async def test_the_enum_and_the_plain_string_are_interchangeable() -> None:
    """``BackgroundSound`` is a ``StrEnum`` so existing string callers keep
    working and the four rooms stay discoverable."""
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    await client.agents.voice.create(
        "support", brain=Prompt("hi"), background_sound="forest"
    )

    body = client.agents.voice._http.post.await_args.kwargs["json"]
    assert body["background_sound"] == BackgroundSound.forest == "forest"


@pytest.mark.anyio
async def test_an_unset_level_is_left_out_of_the_request() -> None:
    """Absent means the platform's default. Sending a number the SDK invented
    would keep this agent on today's default after the platform's moves."""
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    await client.agents.voice.create(
        "support", brain=Prompt("hi"), background_sound="office"
    )

    body = client.agents.voice._http.post.await_args.kwargs["json"]
    assert "background_sound_volume" not in body
    assert "background_sound_enabled" not in body


@pytest.mark.anyio
async def test_no_ambience_arguments_send_no_ambience_keys() -> None:
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    await client.agents.voice.create("support", brain=Prompt("hi"))

    body = client.agents.voice._http.post.await_args.kwargs["json"]
    assert not [key for key in body if key.startswith("background_sound")]


# ── the level is checked at the call site ──────────────────────────────────


@pytest.mark.anyio
@pytest.mark.parametrize("bad", [30, 1.5, -0.1])
async def test_a_level_outside_the_range_fails_before_the_request(bad) -> None:
    """A gain, not a percentage: 30 is the mistake worth catching, and catching
    it here beats a 422 on the round trip."""
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    with pytest.raises(ValueError, match="gain, not a percentage"):
        await client.agents.voice.create(
            "support", brain=Prompt("hi"), background_sound_volume=bad
        )

    client.agents.voice._http.post.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("edge", [0.0, 1.0])
async def test_both_ends_of_the_range_are_allowed(edge) -> None:
    """0.0 is a silent bed and 1.0 a loud one — both are the operator's call.
    Ambience is switched OFF with ``background_sound_enabled``."""
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    await client.agents.voice.create(
        "support",
        brain=Prompt("hi"),
        background_sound="office",
        background_sound_volume=edge,
    )

    body = client.agents.voice._http.post.await_args.kwargs["json"]
    assert body["background_sound_volume"] == edge


# ── client.agents.update ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_update_can_change_the_level_alone() -> None:
    client = _client()
    client.agents._http.put = AsyncMock(
        return_value={"agent_id": "support", "voices": []}
    )

    await client.agents.update("support", background_sound_volume=0.15)

    body = client.agents._http.put.await_args.kwargs["json"]
    assert body == {"background_sound_volume": 0.15}


@pytest.mark.anyio
async def test_update_can_switch_the_bed_off_without_losing_the_room() -> None:
    client = _client()
    client.agents._http.put = AsyncMock(
        return_value={"agent_id": "support", "voices": []}
    )

    await client.agents.update("support", background_sound_enabled=False)

    body = client.agents._http.put.await_args.kwargs["json"]
    assert body == {"background_sound_enabled": False}
    assert "background_sound" not in body


@pytest.mark.anyio
async def test_an_unrelated_update_touches_no_ambience_key() -> None:
    client = _client()
    client.agents._http.put = AsyncMock(
        return_value={"agent_id": "support", "voices": []}
    )

    await client.agents.update("support", name="Renamed")

    body = client.agents._http.put.await_args.kwargs["json"]
    assert not [key for key in body if key.startswith("background_sound")]


# ── client.agent.voice.create — /v1/agent/voice ────────────────────────────


@pytest.mark.anyio
async def test_the_unified_create_route_carries_ambience_too() -> None:
    """A different platform route from ``/v1/agents``, built from its own key
    list — and the one the quickstart uses."""
    client = _client()
    client.agent.voice._http.post = AsyncMock(return_value=_PIPE)

    pipe = await client.agent.voice.create(
        name="support",
        prompt="You are helpful.",
        background_sound=BackgroundSound.office,
        background_sound_volume=0.2,
    )

    body = client.agent.voice._http.post.await_args.kwargs["json"]
    assert body["background_sound"] == "office"
    assert body["background_sound_volume"] == 0.2
    assert pipe.background_sound == "office"
    assert pipe.background_sound_volume == 0.2


@pytest.mark.anyio
async def test_the_unified_route_also_checks_the_level(anyio_backend) -> None:
    client = _client()
    client.agent.voice._http.post = AsyncMock(return_value=_PIPE)

    with pytest.raises(ValueError, match="gain, not a percentage"):
        await client.agent.voice.create(
            name="support", prompt="hi", background_sound_volume=45
        )

    client.agent.voice._http.post.assert_not_awaited()
