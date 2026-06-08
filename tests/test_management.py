"""Tests for management resource modules."""

from unittest.mock import AsyncMock, patch

import pytest
from unpod import AsyncClient
from unpod.management.sessions import SessionsResource


@pytest.fixture
def client():
    return AsyncClient(api_key="unpod_sk_test", base_url="https://api.example.test")


@pytest.mark.anyio
async def test_numbers_list(client: AsyncClient):
    with patch.object(client.numbers._http, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "data": [
                {
                    "id": "num_1",
                    "number": "+919800000001",
                    "country": "IN",
                    "capabilities": ["voice"],
                    "status": "active",
                }
            ]
        }
        numbers = await client.numbers.list()
        assert len(numbers) == 1
        assert numbers[0].number == "+919800000001"
        mock_get.assert_called_once_with("/v1/numbers", params=None)


@pytest.mark.anyio
async def test_numbers_purchase(client: AsyncClient):
    with patch.object(
        client.numbers._http, "post", new_callable=AsyncMock
    ) as mock_post:
        mock_post.return_value = {
            "id": "num_1",
            "number": "+919800000001",
            "country": "IN",
            "capabilities": ["voice"],
            "status": "active",
        }
        num = await client.numbers.purchase(country="IN", capabilities=["voice"])
        assert num.id == "num_1"


@pytest.mark.anyio
async def test_numbers_release(client: AsyncClient):
    with patch.object(
        client.numbers._http, "delete", new_callable=AsyncMock
    ) as mock_del:
        await client.numbers.release("num_1")
        mock_del.assert_called_once_with("/v1/numbers/num_1")


@pytest.mark.anyio
async def test_voice_profiles_list(client: AsyncClient):
    with patch.object(
        client.voice_profiles._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {
            "data": [
                {
                    "id": "vp_1",
                    "persona": "hindi-female-warm-hd",
                    "language": "hi",
                    "quality_tier": "hd",
                    "price_per_minute": 2.5,
                    "latency_p95_ms": 240,
                }
            ]
        }
        profiles = await client.voice_profiles.list(language="hi")
        assert profiles[0].persona == "hindi-female-warm-hd"


@pytest.mark.anyio
async def test_voice_profiles_delete(client: AsyncClient):
    with patch.object(
        client.voice_profiles._http, "delete", new_callable=AsyncMock
    ) as mock_del:
        await client.voice_profiles.delete("vp_1")
        mock_del.assert_called_once_with("/v1/voice-profiles/vp_1")


@pytest.mark.anyio
async def test_pipes_create(client: AsyncClient):
    with patch.object(client.pipes._http, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {
            "pipe_id": "pipe_1",
            "project_id": "proj_1",
            "name": "kyc-bot",
            "voice_profile_id": "vp_1",
            "number": "+919800000001",
            "agent_id": "kyc-bot",
            "agent_endpoint": None,
            "recording": True,
            "created": "2025-05-25T10:00:00Z",
            "modified": "2025-05-25T10:00:00Z",
        }
        pipe = await client.pipes.create(
            name="kyc-bot",
            voice_profile="vp_1",
            agent_id="kyc-bot",
        )
        assert pipe.pipe_id == "pipe_1"


@pytest.mark.anyio
async def test_pipes_update(client: AsyncClient):
    with patch.object(client.pipes._http, "put", new_callable=AsyncMock) as mock_put:
        mock_put.return_value = {
            "pipe_id": "pipe_1",
            "project_id": "proj_1",
            "name": "kyc-bot",
            "voice_profile_id": "vp_2",
            "number": "+919800000001",
            "agent_id": "kyc-bot",
            "agent_endpoint": None,
            "recording": True,
            "created": "2025-05-25T10:00:00Z",
            "modified": "2025-05-25T10:00:00Z",
        }
        pipe = await client.pipes.update("pipe_1", voice_profile="vp_2")
        assert pipe.voice_profile_id == "vp_2"


@pytest.mark.anyio
async def test_pipes_delete(client: AsyncClient):
    with patch.object(client.pipes._http, "delete", new_callable=AsyncMock) as mock_del:
        await client.pipes.delete("pipe_1")
        mock_del.assert_called_once_with("/v1/pipes/pipe_1")


@pytest.mark.anyio
async def test_calls_create(client: AsyncClient):
    with patch.object(client.calls._http, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {
            "id": "call_1",
            "pipe_id": "pipe_1",
            "user_number": "+919800000002",
            "status": "ringing",
            "direction": "outbound",
            "duration_s": None,
            "started_at": "2025-05-25T10:00:00Z",
            "ended_at": None,
        }
        call = await client.calls.create(agent="agt_1", user_number="+919800000002")
        assert call.status == "ringing"


@pytest.mark.anyio
async def test_calls_list(client: AsyncClient):
    with patch.object(client.calls._http, "get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {
            "data": [
                {
                    "id": "call_1",
                    "pipe_id": "pipe_1",
                    "user_number": "+919800000002",
                    "status": "in_flight",
                    "direction": "outbound",
                    "duration_s": None,
                    "started_at": "2025-05-25T10:00:00Z",
                    "ended_at": None,
                }
            ]
        }
        calls = await client.calls.list(status="in_flight")
        assert len(calls) == 1


@pytest.mark.anyio
async def test_calls_hangup(client: AsyncClient):
    with patch.object(client.calls._http, "post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {}
        await client.calls.hangup("call_1")
        mock_post.assert_called_once_with("/v1/calls/call_1/hangup", json=None)


@pytest.mark.anyio
async def test_recordings_list(client: AsyncClient):
    # GET /v1/recordings returns sessions (with recording_url), not Recordings.
    with patch.object(
        client.recordings._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {
            "data": [
                {
                    "session_id": "sess_1",
                    "call_id": "call_1",
                    "duration_s": 42,
                    "status": "completed",
                    "recording_url": "https://rec.example/1.wav",
                }
            ]
        }
        recordings = await client.recordings.list(call_id="call_1")
        assert recordings[0].session_id == "sess_1"
        assert recordings[0].recording_url == "https://rec.example/1.wav"
        mock_get.assert_called_once_with("/v1/recordings", params={"call_id": "call_1"})


@pytest.mark.anyio
async def test_transcripts_list(client: AsyncClient):
    # GET /v1/transcripts returns sessions (with a transcript), not Transcripts.
    with patch.object(
        client.transcripts._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {
            "data": [
                {
                    "session_id": "sess_1",
                    "transcript": [{"role": "user", "content": "hi"}],
                }
            ]
        }
        sessions = await client.transcripts.list()
        assert sessions[0].session_id == "sess_1"
        assert sessions[0].transcript[0].content == "hi"
        mock_get.assert_called_once_with("/v1/transcripts")


@pytest.mark.anyio
async def test_transcripts_get(client: AsyncClient):
    # There is no per-transcript endpoint; get() reads the session.
    with patch.object(
        client.transcripts._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {
            "session_id": "sess_1",
            "transcript": [{"role": "agent", "content": "Hello"}],
        }
        session = await client.transcripts.get("sess_1")
        assert session.session_id == "sess_1"
        assert session.transcript[0].content == "Hello"
        mock_get.assert_called_once_with("/v1/sessions/sess_1")


@pytest.mark.anyio
async def test_sessions_create_token(client: AsyncClient):
    """5.1 — create_token posts pipe_id and parses the token response."""
    with patch.object(
        client.sessions._http, "post", new_callable=AsyncMock
    ) as mock_post:
        mock_post.return_value = {
            "token": "tok-abc",
            "expires_at": "2025-05-25T10:01:00Z",
        }
        token = await client.sessions.create_token(pipe_id="PIPE_001")
        assert token.token == "tok-abc"
        mock_post.assert_called_once_with(
            "/v1/sessions/token", json={"pipe_id": "PIPE_001"}
        )


@pytest.mark.anyio
async def test_sessions_create_token_with_metadata(client: AsyncClient):
    """5.1 (edge) — metadata is included in the request body when provided."""
    with patch.object(
        client.sessions._http, "post", new_callable=AsyncMock
    ) as mock_post:
        mock_post.return_value = {
            "token": "tok-meta",
            "expires_at": "2025-05-25T10:01:00Z",
        }
        await client.sessions.create_token(
            pipe_id="PIPE_001", metadata={"user_id": "u42"}
        )
        mock_post.assert_called_once_with(
            "/v1/sessions/token",
            json={"pipe_id": "PIPE_001", "metadata": {"user_id": "u42"}},
        )


def test_sessions_create_token_uses_pipe_id_argument():
    """5.2 — regression guard: the argument is pipe_id, not the old agent_id."""
    import inspect

    sig = inspect.signature(SessionsResource.create_token)
    assert "pipe_id" in sig.parameters
    assert "agent_id" not in sig.parameters


class _FakePost:
    """Fake HTTP client capturing the last POST path and body."""

    def __init__(self, response: dict):
        self._response = response
        self.path: str | None = None
        self.json: dict | None = None

    async def post(self, path: str, json: dict | None = None) -> dict:
        self.path = path
        self.json = json
        return self._response


@pytest.mark.anyio
async def test_sessions_end_posts_to_orchestrator():
    fake = _FakePost({"data": {"session_id": "s1", "state": "ended"}})
    res = SessionsResource(http=fake, orch_http=fake)
    result = await res.end("s1")
    assert fake.path == "/v1/sessions/s1/end"
    assert result.state == "ended"


@pytest.mark.anyio
async def test_sessions_transfer_sends_target_body():
    fake = _FakePost(
        {
            "data": {
                "session_id": "s1",
                "added_participant_id": "p2",
                "removed_participant_id": None,
                "mode": "cold",
            }
        }
    )
    res = SessionsResource(http=fake, orch_http=fake)
    result = await res.transfer("s1", to_type="sip", to_config={"number": "+1"})
    assert fake.path == "/v1/sessions/s1/transfer"
    assert fake.json is not None
    assert fake.json["to"] == {"type": "sip", "config": {"number": "+1"}}
    assert result.added_participant_id == "p2"


@pytest.mark.anyio
async def test_sessions_merge_posts_secondaries():
    fake = _FakePost(
        {
            "data": {
                "primary_session_id": "p",
                "outcomes": [
                    {
                        "session_id": "x",
                        "status": "merged",
                        "moved_participant_ids": ["p1"],
                        "error": None,
                    }
                ],
            }
        }
    )
    res = SessionsResource(http=fake, orch_http=fake)
    result = await res.merge("p", ["x"])
    assert fake.path == "/v1/sessions/merge"
    assert fake.json is not None
    assert fake.json["secondary_session_ids"] == ["x"]
    assert result.outcomes[0].status == "merged"


def test_sync_client_has_resources():
    from unpod import Client

    c = Client(api_key="unpod_sk_test")
    assert hasattr(c, "numbers")
    assert hasattr(c, "voice_profiles")
    assert hasattr(c, "pipes")
    assert hasattr(c, "calls")
    assert hasattr(c, "recordings")
    assert hasattr(c, "transcripts")
    assert hasattr(c, "sessions")
    assert hasattr(c, "api_keys")


@pytest.mark.anyio
async def test_api_keys_create_posts_body():
    """create() POSTs to /v1/api-keys and parses ApiKey from response data."""
    from unpod.management.api_keys import ApiKeysResource

    fake = _FakePost(
        {
            "data": {
                "key_id": "AK_x",
                "name": "ci",
                "org_id": "o1",
                "project_id": "o1",
                "status": "active",
                "raw_key": "sk_xxx",
            }
        }
    )
    res = ApiKeysResource(http=fake)  # type: ignore[arg-type]
    key = await res.create(name="ci", org_id="o1")
    assert fake.path == "/v1/api-keys"
    assert fake.json == {"name": "ci", "org_id": "o1"}
    assert key.raw_key == "sk_xxx"
    assert key.key_id == "AK_x"


@pytest.mark.anyio
async def test_api_keys_create_includes_project_id_when_provided():
    """create() includes project_id in the body only when explicitly given."""
    from unpod.management.api_keys import ApiKeysResource

    fake = _FakePost(
        {
            "data": {
                "key_id": "AK_y",
                "name": "ci",
                "org_id": "o1",
                "project_id": "p1",
                "status": "active",
                "raw_key": "sk_yyy",
            }
        }
    )
    res = ApiKeysResource(http=fake)  # type: ignore[arg-type]
    key = await res.create(name="ci", org_id="o1", project_id="p1")
    assert fake.json == {"name": "ci", "org_id": "o1", "project_id": "p1"}
    assert key.project_id == "p1"


@pytest.mark.anyio
async def test_calls_create_contract_unchanged():
    """Regression guard: calls.create still POSTs the same body to /v1/calls.

    The platform now enqueues calls asynchronously via the orchestration
    queue, but the wire contract is unchanged. If this fails, the contract
    drifted — reconcile before shipping.
    """
    from unpod.management.calls import CallsResource

    fake = _FakePost({"data": {"id": "c1", "status": "queued"}})
    res = CallsResource(http=fake)  # type: ignore[arg-type]
    await res.create(pipe_id="pipe-1", to_number="+15551230000")
    assert fake.path == "/v1/calls"
    assert fake.json is not None
    assert fake.json["pipe_id"] == "pipe-1"
    assert fake.json["to_number"] == "+15551230000"
