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
        started, agent_id="support-bot", session=object()  # type: ignore[arg-type]
    )

    assert ctx.session_id == "sess_1"
    assert ctx.call_id == "sess_1"
    assert ctx.agent_id == "support-bot"
    assert ctx.direction == "outbound"
    assert ctx.user_number == "+14155550100"
    assert ctx.instructions == "Confirm appointment"
    assert ctx.data == {"customer_id": "C1"}
