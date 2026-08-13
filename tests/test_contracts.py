"""Public SDK contract tests shared by docs, SDK, and platform service."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from unpod import AsyncClient, Client
from unpod._protocol import CallStartedEvent
from unpod.connectivity.bridge_server import _context_from_call_started
from unpod.connectivity.runner import AgentRunner


def test_async_client_supports_async_context_manager() -> None:
    async def scenario() -> bool:
        async with AsyncClient(api_key="unpod_sk_test") as client:
            assert client.pipes is not None
            return client._http._client is None

    assert asyncio.run(scenario()) is True


def test_sync_client_resource_methods_are_blocking() -> None:
    client = Client(api_key="unpod_sk_test")
    client.voice_profiles._async._http.get = AsyncMock(return_value=[])

    profiles = client.voice_profiles.list()

    assert profiles == []
    client.voice_profiles._async._http.get.assert_awaited_once_with(
        "/v1/voice-profiles", params=None
    )


def test_runner_accepts_legacy_max_concurrent_calls_alias() -> None:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        return None

    runner = AgentRunner(
        entrypoint=entrypoint,
        agent_id="support-bot",
        api_key="unpod_sk_test",
        max_concurrent_calls=7,
    )

    assert runner.stats().capacity == 7


def test_runner_builds_call_context_from_call_started_metadata() -> None:
    started = CallStartedEvent(
        session_id="sess_1",
        job_id="call_1",
        room_id="sess_1",
        voice_profile_id="vp_1",
        metadata={
            "direction": "outbound",
            "user_number": "+14155550100",
            "to_number": "+14155550100",
            "from_number": "+18005551234",
            "instructions": "Confirm appointment",
            "data": {"customer_id": "C1"},
        },
    )

    ctx = _context_from_call_started(
        started,
        agent_id="support-bot",
        session=object(),  # type: ignore[arg-type]
    )

    assert ctx.session_id == "sess_1"
    assert ctx.call_id == "sess_1"
    assert ctx.agent_id == "support-bot"
    assert ctx.direction == "outbound"
    assert ctx.user_number == "+14155550100"
    assert ctx.instructions == "Confirm appointment"
    assert ctx.data == {"customer_id": "C1", "voice_profile_id": "vp_1"}


def test_routing_identity_survives_a_nested_data_payload() -> None:
    """The regression that made an agent connect and then say nothing.

    supervoice's dispatcher sends the routing identity as TOP-LEVEL metadata
    keys and the caller's payload under ``data``. An empty ``data: {}`` is
    still a dict, so it always won and agent_id/playbook_id never reached the
    entrypoint — the playbook pool then ended the call ``invalid_call_target``
    before the first turn. This is the exact frame a runner-brain call sends.
    """
    started = CallStartedEvent(
        session_id="s-1",
        job_id="j-1",
        room_id="r-1",
        voice_profile_id="vp_1",
        metadata={
            "agent_id": "my-support-agent",
            "playbook_id": None,
            "project_id": "22",
            "org_id": "22",
            "pipe_id": "PIPE_x",
            "data": {"org_handle": "example.co"},
        },
    )

    ctx = _context_from_call_started(
        started, agent_id="my-support-agent", session=object()  # type: ignore[arg-type]
    )

    assert ctx.data["agent_id"] == "my-support-agent"
    assert ctx.data["project_id"] == "22"
    # The caller's own payload is still there.
    assert ctx.data["org_handle"] == "example.co"


def test_request_data_cannot_rename_the_answering_agent() -> None:
    """Server identity outranks caller data — else `data` picks the agent."""
    started = CallStartedEvent(
        session_id="s-1",
        job_id="j-1",
        room_id="r-1",
        metadata={
            "agent_id": "real-agent",
            "data": {"agent_id": "attacker-agent"},
        },
    )

    ctx = _context_from_call_started(
        started, agent_id="real-agent", session=object()  # type: ignore[arg-type]
    )

    assert ctx.data["agent_id"] == "real-agent"


def test_call_started_metadata_dict_is_not_mutated() -> None:
    """The frame's own dict must survive context construction unchanged."""
    payload = {"customer_id": "C1"}
    started = CallStartedEvent(
        session_id="s-1",
        job_id="j-1",
        room_id="r-1",
        voice_profile_id="vp_1",
        metadata={"agent_id": "a", "data": payload},
    )

    _context_from_call_started(
        started, agent_id="a", session=object()  # type: ignore[arg-type]
    )

    assert payload == {"customer_id": "C1"}


def test_call_context_data_keeps_explicit_voice_profile_id() -> None:
    started = CallStartedEvent(
        session_id="sess_1",
        job_id="call_1",
        room_id="sess_1",
        voice_profile_id="vp_frame",
        metadata={"data": {"voice_profile_id": "vp_meta"}},
    )

    ctx = _context_from_call_started(
        started,
        agent_id="support-bot",
        session=object(),  # type: ignore[arg-type]
    )

    assert ctx.data["voice_profile_id"] == "vp_meta"


def test_call_context_data_omits_absent_voice_profile_id() -> None:
    started = CallStartedEvent(
        session_id="sess_1",
        job_id="call_1",
        room_id="sess_1",
        metadata={"data": {"customer_id": "C1"}},
    )

    ctx = _context_from_call_started(
        started,
        agent_id="support-bot",
        session=object(),  # type: ignore[arg-type]
    )

    assert "voice_profile_id" not in ctx.data
