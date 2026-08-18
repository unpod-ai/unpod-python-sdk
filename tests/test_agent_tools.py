"""Per-agent platform-tool config and background ambience.

Two tools are always on and are therefore NOT representable as disabled:
``end_call`` and ``voicemail_detector``. Only ``handover`` is toggleable, and
``voicemail`` appears purely to carry its callback line.

Everything rides the EXISTING agent routes. There is deliberately no
``/agents/{id}/tools`` endpoint: the Django speech proxy enumerates agent routes
explicitly and its passthrough excludes ``agents``, so a sub-route would 404 in
proxy mode while working against supervoice directly.
"""

from unittest.mock import AsyncMock

import pytest

from unpod import AsyncClient
from unpod.agents import (
    _BASE,
    BackgroundSound,
    HandoverTool,
    Playbook,
    ToolsConfig,
    VoicemailTool,
)

_AGENT = {
    "agent_id": "support",
    "name": "Support",
    "brain": {"type": "playbook", "ref": "PB_1"},
    "brain_execution": "embedded",
    "voices": [],
    "tools": {"handover": {"enabled": True, "numbers": ["+919876543210"]}},
    "background_sound": "office",
}

_VOICE = {
    "agent_id": "support",
    "voice_profile_id": "vp_1",
    "name": "Support",
    "brain": {"type": "playbook", "ref": "PB_1"},
    "brain_execution": "embedded",
    "is_default": True,
    "tools": {"handover": {"enabled": True}},
    "background_sound": "city",
}


def _client() -> AsyncClient:
    return AsyncClient(api_key="test", base_url="https://example.test")


# ── typed config ─────────────────────────────────────────────────────────────


def test_background_sound_offers_the_four_beds_and_off():
    assert {b.value for b in BackgroundSound} == {
        "office",
        "city",
        "forest",
        "crowded_room",
        "none",
    }


def test_tools_config_serializes_only_what_was_set():
    body = ToolsConfig(
        handover=HandoverTool(enabled=True, numbers=["+91900"])
    ).as_body()

    assert body == {"handover": {"enabled": True, "numbers": ["+91900"]}}


def test_a_voicemail_message_can_be_set_without_touching_handover():
    body = ToolsConfig(voicemail=VoicemailTool(message="I'll call back")).as_body()

    assert body == {"voicemail": {"message": "I'll call back"}}


def test_handover_numbers_are_an_ordered_fallback_list():
    tool = HandoverTool(enabled=True, numbers=["+91900", "+91901"])

    assert tool.numbers == ["+91900", "+91901"]


def test_an_unknown_tool_field_is_rejected_at_the_call_site():
    """Requests fail loudly on a typo; responses stay permissive."""
    with pytest.raises(Exception):
        ToolsConfig(handver={"enabled": True})


def test_end_call_is_not_configurable():
    """Always on. Offering a knob would imply it can be turned off."""
    with pytest.raises(Exception):
        ToolsConfig(end_call={"enabled": False})


# ── create ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_create_sends_tools_and_background_sound():
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    await client.agents.voice.create(
        "support",
        brain=Playbook("PB_1"),
        tools=ToolsConfig(handover=HandoverTool(enabled=True, numbers=["+91900"])),
        background_sound=BackgroundSound.office,
    )

    body = client.agents.voice._http.post.await_args.kwargs["json"]
    assert body["tools"] == {"handover": {"enabled": True, "numbers": ["+91900"]}}
    assert body["background_sound"] == "office"


@pytest.mark.anyio
async def test_create_omits_both_fields_when_not_given():
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    await client.agents.voice.create("support", brain=Playbook("PB_1"))

    body = client.agents.voice._http.post.await_args.kwargs["json"]
    assert "tools" not in body
    assert "background_sound" not in body


@pytest.mark.anyio
async def test_a_plain_dict_is_accepted_for_tools():
    client = _client()
    client.agents.voice._http.post = AsyncMock(return_value=_VOICE)

    await client.agents.voice.create(
        "support", brain=Playbook("PB_1"), tools={"handover": {"enabled": True}}
    )

    body = client.agents.voice._http.post.await_args.kwargs["json"]
    assert body["tools"] == {"handover": {"enabled": True}}


# ── update ───────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_update_sends_both_fields_on_the_existing_agent_route():
    client = _client()
    client.agents._http.put = AsyncMock(return_value=_AGENT)

    await client.agents.update(
        "support",
        tools=ToolsConfig(handover=HandoverTool(enabled=False)),
        background_sound="none",
    )

    call = client.agents._http.put.await_args
    assert call.args[0] == f"{_BASE}/support"
    # A section is sent whole, so setting it replaces it — same semantic the
    # merge test below relies on.
    assert call.kwargs["json"]["tools"] == {
        "handover": {"enabled": False, "numbers": []}
    }
    assert call.kwargs["json"]["background_sound"] == "none"


@pytest.mark.anyio
async def test_reads_expose_both_fields():
    client = _client()
    client.agents._http.get = AsyncMock(return_value=_AGENT)

    agent = await client.agents.get("support")

    assert agent.tools["handover"]["numbers"] == ["+919876543210"]
    assert agent.background_sound == "office"


@pytest.mark.anyio
async def test_an_agent_response_without_the_fields_still_parses():
    """An older supervoice must not break a newer SDK."""
    client = _client()
    client.agents._http.get = AsyncMock(
        return_value={"agent_id": "support", "name": "Support"}
    )

    agent = await client.agents.get("support")

    assert agent.tools == {}
    assert agent.background_sound is None


# ── the tools convenience resource ───────────────────────────────────────────


@pytest.mark.anyio
async def test_tools_get_reads_the_agent_and_returns_typed_config():
    client = _client()
    client.agents._http.get = AsyncMock(return_value=_AGENT)

    cfg = await client.agents.tools.get("support")

    assert cfg.handover.enabled is True
    assert cfg.handover.numbers == ["+919876543210"]
    client.agents._http.get.assert_awaited_once_with(f"{_BASE}/support")


@pytest.mark.anyio
async def test_tools_set_merges_rather_than_replacing():
    """Setting voicemail must not silently wipe a configured handover."""
    client = _client()
    client.agents._http.get = AsyncMock(return_value=_AGENT)
    client.agents._http.put = AsyncMock(return_value=_AGENT)

    await client.agents.tools.set(
        "support", voicemail=VoicemailTool(message="ring back")
    )

    sent = client.agents._http.put.await_args.kwargs["json"]["tools"]
    assert sent["voicemail"] == {"message": "ring back"}
    assert sent["handover"] == {"enabled": True, "numbers": ["+919876543210"]}


@pytest.mark.anyio
async def test_tools_set_overwrites_the_section_it_is_given():
    client = _client()
    client.agents._http.get = AsyncMock(return_value=_AGENT)
    client.agents._http.put = AsyncMock(return_value=_AGENT)

    await client.agents.tools.set("support", handover=HandoverTool(enabled=False))

    sent = client.agents._http.put.await_args.kwargs["json"]["tools"]
    assert sent["handover"] == {"enabled": False, "numbers": []}


@pytest.mark.anyio
async def test_the_tools_resource_never_calls_a_sub_route():
    """The regression guard for the Django proxy: agents/{id}/tools would 404."""
    client = _client()
    client.agents._http.get = AsyncMock(return_value=_AGENT)
    client.agents._http.put = AsyncMock(return_value=_AGENT)

    await client.agents.tools.set("support", handover=HandoverTool(enabled=True))

    paths = [c.args[0] for c in client.agents._http.get.await_args_list]
    paths += [c.args[0] for c in client.agents._http.put.await_args_list]
    assert paths == [f"{_BASE}/support", f"{_BASE}/support"]
    assert all(not p.endswith("/tools") for p in paths)
