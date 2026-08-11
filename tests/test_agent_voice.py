from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from unpod import AsyncClient, Client


_PIPE = {
    "pipe_id": "PIPE_1",
    "project_id": "proj_1",
    "name": "support",
    "voice_profile_id": "vp_1",
    "agent_id": "support-abc",
    "agent_endpoint": None,
    "playbook_id": None,
    "prompt": "You are helpful.",
    "metadata": {"brain_execution": "embedded"},
    "recording": False,
    "max_call_duration_s": 3600,
    "created": "2026-01-01T00:00:00Z",
    "modified": "2026-01-01T00:00:00Z",
}


@pytest.mark.anyio
async def test_agent_voice_prompt_posts_unified_source() -> None:
    client = AsyncClient(api_key="test", base_url="https://example.test")
    client.agent.voice._http.post = AsyncMock(return_value=_PIPE)

    pipe = await client.agent.voice.create(
        name="support",
        voice_profile="vp_1",
        prompt="You are helpful.",
    )

    assert pipe.playbook_id is None
    client.agent.voice._http.post.assert_awaited_once()
    body = client.agent.voice._http.post.await_args.kwargs["json"]
    assert body["prompt"] == "You are helpful."
    assert body["voice_profile"] == "vp_1"


@pytest.mark.anyio
async def test_agent_voice_reads_path(tmp_path: Path) -> None:
    source = "persona: Bot\njourneys:\n  main:\n    checkpoints:\n      - id: chat\n"
    path = tmp_path / "flow.yml"
    path.write_text(source, encoding="utf-8")
    client = AsyncClient(api_key="test", base_url="https://example.test")
    client.agent.voice._http.post = AsyncMock(return_value=_PIPE)

    await client.agent.voice.create(name="flow", playbook=path)

    body = client.agent.voice._http.post.await_args.kwargs["json"]
    assert body["playbook"] == source


@pytest.mark.anyio
async def test_agent_voice_requires_exactly_one_source() -> None:
    client = AsyncClient(api_key="test", base_url="https://example.test")

    with pytest.raises(ValueError, match="received none"):
        await client.agent.voice.create(name="empty")
    with pytest.raises(ValueError, match="agent_id.*prompt"):
        await client.agent.voice.create(
            name="conflict", agent_id="a1", prompt="hello"
        )


def test_sync_agent_voice_namespace_is_available() -> None:
    client = Client(api_key="test", base_url="https://example.test")
    assert client.agent.voice is not None
