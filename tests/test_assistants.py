"""client.assistants: build, deploy and chat with an assistant (no network)."""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from unpod import AssistantValidationError, AsyncClient, Client
from unpod.management.assistants import TURN_TIMEOUT
from unpod.management.flows import JOURNEY_BASE
from tests.test_autonomous import _http_error

A = f"{JOURNEY_BASE}/spaces/s1/assistants"
REPLY = {"message_id": "msg_1", "reply": "Hi Ravi", "status": "succeeded"}


@pytest.fixture()
def client():
    return AsyncClient(api_key="unpod_sk_test", base_url="https://api.example.test")


@pytest.mark.anyio
async def test_create_posts_the_definition_and_returns_the_assistant(client):
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.return_value = {
            "assistant_id": "asst_1",
            "status": "draft",
            "draft": {"name": "Maya"},
        }
        got = await client.assistants.create("s1", {"name": "Maya"})
    post.assert_awaited_once_with(A, json={"name": "Maya"})
    assert got.assistant_id == "asst_1" and got.status == "draft"


@pytest.mark.anyio
async def test_send_always_sends_an_idempotency_key(client):
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.return_value = REPLY
        await client.assistants.send("s1", "asst_1", "thr_1", "hi")
        auto = post.await_args.kwargs["headers"]["Idempotency-Key"]
        await client.assistants.send(
            "s1", "asst_1", "thr_1", "hi", idempotency_key="k-7"
        )
        given = post.await_args.kwargs["headers"]["Idempotency-Key"]
    assert re.fullmatch(r"[0-9a-f]{32}", auto) and given == "k-7"


@pytest.mark.anyio
async def test_send_uses_the_turn_timeout_and_returns_the_reply(client):
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.return_value = REPLY
        reply = await client.assistants.send("s1", "asst_1", "thr_1", "hi")
    assert post.await_args.args[0] == f"{A}/asst_1/threads/thr_1/messages"
    assert post.await_args.kwargs["json"] == {"text": "hi"}
    assert post.await_args.kwargs["timeout"] == TURN_TIMEOUT == 165.0
    assert reply.reply == "Hi Ravi" and not reply.awaiting_approval


@pytest.mark.anyio
async def test_a_paused_turn_exposes_the_approval_and_decide_resumes_it(client):
    paused = {
        **REPLY,
        "reply": "I need your approval",
        "status": "awaiting_approval",
        "approval": {
            "approval_id": "apr_1",
            "tool": "memory.core_append",
            "args": {"label": "user_profile", "text": "Likes tea"},
            "reason": "needs your approval",
        },
    }
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.return_value = paused
        reply = await client.assistants.send("s1", "asst_1", "thr_1", "remember tea")
        assert reply.awaiting_approval and reply.approval is not None
        assert reply.approval.args == {"label": "user_profile", "text": "Likes tea"}
        post.return_value = {**REPLY, "reply": "Saved."}
        done = await client.assistants.decide(
            "s1", "asst_1", "thr_1", "apr_1", approve=True
        )
    assert post.await_args.args[0] == f"{A}/asst_1/threads/thr_1/approvals/apr_1"
    assert post.await_args.kwargs["json"] == {"decision": "approve"}
    assert post.await_args.kwargs["timeout"] == TURN_TIMEOUT
    assert done.reply == "Saved."


@pytest.mark.anyio
async def test_refusals_become_assistant_validation_errors(client):
    detail = [
        {"loc": ["body", "tools", 0], "msg": "unknown builtin tool 'gmail.delete_all'"}
    ]
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.side_effect = _http_error(422, {"detail": detail})
        with pytest.raises(AssistantValidationError) as e:
            await client.assistants.create("s1", {"name": "x"})
    assert e.value.status_code == 422
    assert any("gmail.delete_all" in p for p in e.value.problems)


@pytest.mark.anyio
async def test_opening_a_thread_on_an_off_assistant_is_a_409_error(client):
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.side_effect = _http_error(
            409, {"detail": "assistant is not deployed and on"}
        )
        with pytest.raises(AssistantValidationError) as e:
            await client.assistants.open_thread("s1", "asst_1", "eu_1")
    assert e.value.status_code == 409


@pytest.mark.anyio
async def test_reads_build_models(client):
    with patch.object(client.assistants._http, "get", new_callable=AsyncMock) as get:
        get.return_value = [
            {
                "message_id": "m1",
                "role": "user",
                "content": "hi",
                "ts": "2026-10-17T00:00:00",
            }
        ]
        msgs = await client.assistants.messages("s1", "asst_1", "thr_1")
        get.return_value = [
            {
                "label": "user_profile",
                "value": "Likes tea",
                "limit": 2000,
                "editable_by_agent": True,
            }
        ]
        mem = await client.assistants.memory("s1", "asst_1", "eu_1")
    assert msgs[0].content == "hi" and mem[0].value == "Likes tea"
    assert get.await_args.args[0] == f"{A}/asst_1/end_users/eu_1/memory"


@pytest.mark.anyio
async def test_post_forwards_a_per_call_timeout():
    from unpod.management._auth import BearerAuth
    from unpod.management._http import AsyncHTTPClient

    http = AsyncHTTPClient(
        BearerAuth(api_key="unpod_sk_test"), base_url="https://api.example.test"
    )
    fake = MagicMock()
    fake.post = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"ok": True},
            request=httpx.Request("POST", "https://api.example.test/x"),
        )
    )
    with patch.object(http, "_ensure_client", AsyncMock(return_value=fake)):
        await http.post("/x", json={}, timeout=150.0)
    assert fake.post.await_args.kwargs["timeout"] == 150.0


def test_the_sync_client_exposes_assistants():
    c = Client(api_key="unpod_sk_test", base_url="https://api.example.test")
    assert callable(c.assistants.send)


def test_public_sdk_never_names_the_internal_service():
    root = Path(__file__).resolve().parents[1]
    for path in [*root.joinpath("src").rglob("*.py"), Path(__file__)]:
        text = path.read_text().lower()
        assert "super" + "book" not in text, path


# ---- P5 review fixes ----


@pytest.mark.anyio
async def test_a_timed_out_send_is_retried_with_the_same_key(client):
    # Review I1: a fresh key on the caller's retry ran the turn twice.
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.side_effect = [httpx.ReadTimeout("slow"), _http_error(504, {}), REPLY]
        reply = await client.assistants.send("s1", "asst_1", "thr_1", "hi")
    keys = {c.kwargs["headers"]["Idempotency-Key"] for c in post.await_args_list}
    assert reply.reply == "Hi Ravi" and post.await_count == 3 and len(keys) == 1


@pytest.mark.anyio
async def test_the_sdk_waits_longer_than_the_proxy():
    # Review I2: equal timeouts surfaced a bare client timeout, not the 504.
    from unpod.management.assistants import PROXY_TURN_TIMEOUT

    assert TURN_TIMEOUT > PROXY_TURN_TIMEOUT == 150.0


@pytest.mark.anyio
async def test_decide_sends_an_idempotency_key(client):
    # Review I3: the docstring promised it.
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.return_value = REPLY
        await client.assistants.decide("s1", "asst_1", "thr_1", "apr_1", approve=False)
    assert post.await_args.kwargs["headers"]["Idempotency-Key"] == "apr_1:deny"
