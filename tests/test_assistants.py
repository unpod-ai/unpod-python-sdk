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
async def test_approvals_lists_a_threads_pending_approvals(client):
    with patch.object(client.assistants._http, "get", new_callable=AsyncMock) as get:
        get.return_value = [
            {
                "approval_id": "apr_2",
                "tool": "memory.core_replace",
                "args": {"label": "user_profile", "value": "Likes coffee"},
                "reason": "rewrites core memory",
            }
        ]
        pending = await client.assistants.approvals("s1", "asst_1", "thr_1")
    assert get.await_args.args[0] == f"{A}/asst_1/threads/thr_1/approvals"
    assert get.await_args.kwargs["params"] == {"status": "pending"}
    assert [a.approval_id for a in pending] == ["apr_2"]
    assert pending[0].tool == "memory.core_replace"
    assert pending[0].args["value"] == "Likes coffee"


@pytest.mark.anyio
async def test_approvals_is_empty_when_nothing_is_pending(client):
    with patch.object(client.assistants._http, "get", new_callable=AsyncMock) as get:
        get.return_value = []
        assert await client.assistants.approvals("s1", "asst_1", "thr_1") == []


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
    assert callable(c.assistants.approvals)


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


# ---- routines ----


@pytest.mark.anyio
async def test_run_routine_is_a_turn_with_a_key_the_timeout_and_retries(client):
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.side_effect = [httpx.ReadTimeout("slow"), {**REPLY, "message_id": "rt:1"}]
        reply = await client.assistants.run_routine("s1", "asst_1", "eu_1", "daily")
    first, second = post.await_args_list
    assert first.args[0] == f"{A}/asst_1/end_users/eu_1/routines/daily/run"
    assert first.kwargs["timeout"] == TURN_TIMEOUT
    keys = {c.kwargs["headers"]["Idempotency-Key"] for c in (first, second)}
    assert len(keys) == 1 and reply.message_id == "rt:1"


@pytest.mark.anyio
async def test_routine_runs_passes_since_and_limit_and_keeps_extra_fields(client):
    run = {
        "run_id": "rt:daily:eu_1:x",
        "end_user_id": "eu_1",
        "thread_id": "thr_1",
        "routine_id": "daily",
        "trigger": "schedule",
        "status": "awaiting_approval",
        "reply": "May I?",
        "approval": {"approval_id": "apr_1", "tool": "t", "args": {}, "reason": "r"},
        "ts": "2026-10-19T09:00:00Z",
        "new_field": 1,
    }
    with patch.object(client.assistants._http, "get", new_callable=AsyncMock) as get:
        get.return_value = [run]
        runs = await client.assistants.routine_runs(
            "s1", "asst_1", since="2026-10-19T08:00:00Z", limit=5
        )
        await client.assistants.routine_runs("s1", "asst_1")
    first, second = get.await_args_list
    assert first.args[0] == f"{A}/asst_1/routine_runs"
    assert first.kwargs["params"] == {"since": "2026-10-19T08:00:00Z", "limit": "5"}
    assert second.kwargs["params"] == {"limit": "100"}
    assert runs[0].approval is not None and runs[0].approval.approval_id == "apr_1"
    assert runs[0].awaiting_approval and runs[0].new_field == 1


# ---- builder UI reads + tool test ----


@pytest.mark.anyio
async def test_version_gets_one_frozen_version_with_its_definition(client):
    with patch.object(client.assistants._http, "get", new_callable=AsyncMock) as get:
        get.return_value = {"version": 2, "definition": {"name": "Maya"}}
        v = await client.assistants.version("s1", "asst_1", 2)
    get.assert_awaited_once_with(f"{A}/asst_1/versions/2")
    assert v.version == 2 and v.definition == {"name": "Maya"}


@pytest.mark.anyio
async def test_end_users_lists_the_assistants_end_users(client):
    with patch.object(client.assistants._http, "get", new_callable=AsyncMock) as get:
        get.return_value = [{"end_user_id": "eu_2", "external_id": "u2"}]
        got = await client.assistants.end_users("s1", "asst_1")
    get.assert_awaited_once_with(f"{A}/asst_1/end_users")
    assert [e.end_user_id for e in got] == ["eu_2"]


@pytest.mark.anyio
async def test_threads_lists_an_end_users_threads(client):
    with patch.object(client.assistants._http, "get", new_callable=AsyncMock) as get:
        get.return_value = [{"thread_id": "thr_2", "end_user_id": "eu_1", "version": 1}]
        got = await client.assistants.threads("s1", "asst_1", "eu_1")
    get.assert_awaited_once_with(f"{A}/asst_1/end_users/eu_1/threads")
    assert [t.thread_id for t in got] == ["thr_2"]
    assert got[0].summary is None


@pytest.mark.anyio
async def test_threads_carry_the_rolling_summary(client):
    with patch.object(client.assistants._http, "get", new_callable=AsyncMock) as get:
        get.return_value = [{"thread_id": "thr_2", "summary": "Ravi moved to Delhi."}]
        got = await client.assistants.threads("s1", "asst_1", "eu_1")
    assert got[0].summary == "Ravi moved to Delhi."


@pytest.mark.anyio
async def test_pending_approvals_lists_across_threads_with_where_they_wait(client):
    with patch.object(client.assistants._http, "get", new_callable=AsyncMock) as get:
        get.return_value = [
            {
                "approval_id": "apr_1",
                "tool": "run_flow",
                "args": {"email": "a@b.co"},
                "reason": "run_flow needs your approval",
                "thread_id": "thr_1",
                "end_user_id": "eu_1",
                "created": "2026-10-19T06:00:00Z",
            }
        ]
        got = await client.assistants.pending_approvals("s1", "asst_1")
    assert get.await_args.args[0] == f"{A}/asst_1/approvals"
    assert get.await_args.kwargs["params"] == {"status": "pending"}
    assert got[0].thread_id == "thr_1" and got[0].end_user_id == "eu_1"
    assert got[0].created == "2026-10-19T06:00:00Z"


@pytest.mark.anyio
async def test_test_tool_posts_args_and_returns_the_result(client):
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.return_value = {"ok": True, "content": '{"temp": 31}'}
        got = await client.assistants.test_tool(
            "s1", "asst_1", "get_weather", {"city": "Pune"}
        )
    post.assert_awaited_once_with(
        f"{A}/asst_1/tools/get_weather/test", json={"args": {"city": "Pune"}}
    )
    assert got.ok is True and got.content == '{"temp": 31}'


@pytest.mark.anyio
async def test_test_tool_bad_args_raise_validation_error(client):
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.side_effect = _http_error(422, {"detail": "city: required"})
        with pytest.raises(AssistantValidationError) as e:
            await client.assistants.test_tool("s1", "asst_1", "get_weather", {})
    assert e.value.status_code == 422


@pytest.mark.anyio
async def test_discover_mcp_lists_the_servers_tools(client):
    from unpod.models.assistants import McpToolInfo

    tools = [
        {
            "name": "get_weather",
            "description": "Weather.",
            "input_schema": {"type": "object"},
            "annotations": {"x": 1},
        }
    ]
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.return_value = tools
        got = await client.assistants.discover_mcp("s1", "asst_1", "crm")
    post.assert_awaited_once_with(f"{A}/asst_1/mcp/crm/discover")
    assert got == [McpToolInfo(**tools[0])]
    assert got[0].name == "get_weather" and got[0].input_schema == {"type": "object"}


@pytest.mark.anyio
async def test_discover_mcp_missing_secret_raises_validation_error(client):
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.side_effect = _http_error(409, {"detail": "secret 'k' is not configured"})
        with pytest.raises(AssistantValidationError):
            await client.assistants.discover_mcp("s1", "asst_1", "crm")


def test_sync_client_has_discover_mcp():
    c = Client(api_key="unpod_sk_test", base_url="https://api.example.test")
    assert callable(c.assistants.discover_mcp)


FU = {
    "fu_id": "fu_1",
    "thread_id": "thr_1",
    "at": "2026-10-24T08:30:00Z",
    "note": "ask about the hotel",
    "status": "scheduled",
    "created": "2026-10-23T10:00:00Z",
    "tainted": False,
    "new_server_field": 1,
}


@pytest.mark.anyio
async def test_followups_lists_an_end_users_followups(client):
    with patch.object(client.assistants._http, "get", new_callable=AsyncMock) as get:
        get.return_value = [FU]
        got = await client.assistants.followups("s1", "asst_1", "eu_1")
    get.assert_awaited_once_with(f"{A}/asst_1/end_users/eu_1/followups")
    (fu,) = got
    assert (fu.fu_id, fu.status, fu.tainted, fu.thread_id) == (
        "fu_1",
        "scheduled",
        False,
        "thr_1",
    )
    assert fu.model_extra == {"new_server_field": 1}


@pytest.mark.anyio
async def test_cancel_followup_posts_and_returns_it(client):
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.return_value = {**FU, "status": "cancelled"}
        fu = await client.assistants.cancel_followup("s1", "asst_1", "eu_1", "fu_1")
    assert post.await_args.args[0] == f"{A}/asst_1/end_users/eu_1/followups/fu_1/cancel"
    assert fu.status == "cancelled"


@pytest.mark.anyio
async def test_cancelling_a_fired_followup_is_a_validation_error(client):
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.side_effect = _http_error(409, {"detail": "follow-up fu_1 already fired"})
        with pytest.raises(AssistantValidationError):
            await client.assistants.cancel_followup("s1", "asst_1", "eu_1", "fu_1")


@pytest.mark.anyio
async def test_tasks_lists_a_threads_plan(client):
    with patch.object(client.assistants._http, "get", new_callable=AsyncMock) as get:
        get.return_value = [{"id": "hotel", "title": "Book hotel", "status": "doing"}]
        got = await client.assistants.tasks("s1", "asst_1", "thr_1")
    get.assert_awaited_once_with(f"{A}/asst_1/threads/thr_1/tasks")
    assert [(t.id, t.title, t.status) for t in got] == [
        ("hotel", "Book hotel", "doing")
    ]


@pytest.mark.anyio
async def test_end_user_passes_a_timezone_only_when_given(client):
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.return_value = {"end_user_id": "eu_1", "timezone": "Asia/Kolkata"}
        eu = await client.assistants.end_user(
            "s1", "asst_1", "u1", timezone="Asia/Kolkata"
        )
        assert post.await_args.kwargs["json"] == {
            "external_id": "u1",
            "name": "",
            "timezone": "Asia/Kolkata",
        }
        assert eu.timezone == "Asia/Kolkata"
        await client.assistants.end_user("s1", "asst_1", "u1")
        assert "timezone" not in post.await_args.kwargs["json"]


def test_sync_client_has_the_harness_reads():
    c = Client(api_key="unpod_sk_test", base_url="https://api.example.test")
    assert callable(c.assistants.followups)
    assert callable(c.assistants.cancel_followup)
    assert callable(c.assistants.tasks)


RUN = {
    "run_id": "run_msg_1",
    "thread_id": "thr_1",
    "status": "succeeded",
    "trigger": "chat",
    "harness_version": 3,
    "tokens": {"in": 5, "out": 2},
    "trace": [
        {"kind": "llm", "name": "assistant_llm_call", "decision": "tools"},
        {"kind": "tool", "name": "lookup", "args_redacted": "{}", "new": 1},
    ],
    "new_server_field": 1,
}
EVAL = {
    "eval_id": "ev_1",
    "name": "books",
    "turns": [{"user": "book it", "approve": True}],
    "checks": [{"type": "reply_contains", "value": "booked"}],
}
EVAL_RUN = {
    "run_id": "evr_1",
    "target": "draft",
    "status": "running",
    "eval_ids": ["ev_1"],
    "results": [],
    "draft_hash": "abc",
}


@pytest.mark.anyio
async def test_runs_and_run_read_the_inspector(client):
    with patch.object(client.assistants._http, "get", new_callable=AsyncMock) as get:
        get.return_value = [RUN]
        (run,) = await client.assistants.runs("s1", "asst_1", "thr_1", limit=5)
        get.assert_awaited_once_with(
            f"{A}/asst_1/threads/thr_1/runs", params={"limit": "5"}
        )
        assert [s.kind for s in run.trace] == ["llm", "tool"]
        assert run.tokens == {"in": 5, "out": 2}
        assert run.model_extra == {"new_server_field": 1}
        get.return_value = RUN
        one = await client.assistants.run("s1", "asst_1", "run_msg_1")
        assert get.await_args.args[0] == f"{A}/asst_1/runs/run_msg_1"
        assert one.harness_version == 3


@pytest.mark.anyio
async def test_eval_crud(client):
    http = client.assistants._http
    with (
        patch.object(http, "get", new_callable=AsyncMock) as get,
        patch.object(http, "post", new_callable=AsyncMock) as post,
        patch.object(http, "put", new_callable=AsyncMock) as put,
        patch.object(http, "delete", new_callable=AsyncMock) as delete,
    ):
        get.return_value = [EVAL]
        post.return_value = EVAL
        put.return_value = {**EVAL, "name": "renamed"}
        body = {k: EVAL[k] for k in ("name", "turns", "checks")}
        assert [e.eval_id for e in await client.assistants.evals("s1", "asst_1")] == [
            "ev_1"
        ]
        created = await client.assistants.create_eval("s1", "asst_1", body)
        assert post.await_args.args[0] == f"{A}/asst_1/evals"
        assert post.await_args.kwargs["json"] == body and created.eval_id == "ev_1"
        changed = await client.assistants.update_eval("s1", "asst_1", "ev_1", body)
        assert put.await_args.args[0] == f"{A}/asst_1/evals/ev_1"
        assert changed.name == "renamed"
        assert await client.assistants.delete_eval("s1", "asst_1", "ev_1") is None
        delete.assert_awaited_once_with(f"{A}/asst_1/evals/ev_1")


@pytest.mark.anyio
async def test_create_eval_refused_is_a_validation_error(client):
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.side_effect = _http_error(
            409, {"detail": "an assistant has at most 50 evals"}
        )
        with pytest.raises(AssistantValidationError):
            await client.assistants.create_eval("s1", "asst_1", {})


@pytest.mark.anyio
async def test_run_evals_and_read_runs(client):
    http = client.assistants._http
    with (
        patch.object(http, "post", new_callable=AsyncMock) as post,
        patch.object(http, "get", new_callable=AsyncMock) as get,
    ):
        post.return_value = EVAL_RUN
        run = await client.assistants.run_evals("s1", "asst_1")
        assert post.await_args.args[0] == f"{A}/asst_1/evals/run"
        assert post.await_args.kwargs["json"] == {"target": "draft"}
        assert run.status == "running" and not run.done
        await client.assistants.run_evals(
            "s1", "asst_1", target="version", eval_ids=["ev_1"]
        )
        assert post.await_args.kwargs["json"] == {
            "target": "version",
            "eval_ids": ["ev_1"],
        }
        get.return_value = [{**EVAL_RUN, "status": "passed"}]
        (done,) = await client.assistants.eval_runs("s1", "asst_1", limit=3)
        get.assert_awaited_once_with(f"{A}/asst_1/eval_runs", params={"limit": "3"})
        assert done.done
        get.return_value = EVAL_RUN
        await client.assistants.eval_run("s1", "asst_1", "evr_1")
        assert get.await_args.args[0] == f"{A}/asst_1/eval_runs/evr_1"


def test_sync_client_has_the_eval_methods():
    c = Client(api_key="unpod_sk_test", base_url="https://api.example.test")
    for name in (
        "runs",
        "run",
        "evals",
        "create_eval",
        "update_eval",
        "delete_eval",
        "run_evals",
        "eval_runs",
        "eval_run",
    ):
        assert callable(getattr(c.assistants, name))


@pytest.mark.anyio
async def test_mark_memory_reviewed_posts_and_returns_the_blocks(client):
    with patch.object(client.assistants._http, "post", new_callable=AsyncMock) as post:
        post.return_value = [
            {
                "label": "user_profile",
                "value": "x",
                "limit": 2000,
                "editable_by_agent": True,
            }
        ]
        blocks = await client.assistants.mark_memory_reviewed("s1", "asst_1", "eu_1")
    assert post.await_args.args[0] == f"{A}/asst_1/end_users/eu_1/memory/reviewed"
    assert [b.label for b in blocks] == ["user_profile"]
    c = Client(api_key="unpod_sk_test", base_url="https://api.example.test")
    assert callable(c.assistants.mark_memory_reviewed)
