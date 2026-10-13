"""client.flows / client.journeys → Unpod /api/v2/platform/journey/v1 (mocked HTTP)."""

import asyncio
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from unpod import AsyncClient, Client, FlowValidationError
from unpod.models import ConversationMessage, Flow, Journey, JourneyStartResult

BASE = "/api/v2/platform/journey/v1"
FLOW = {"flow_id": "f1", "space_id": "s1", "name": "Support", "status": "draft", "steps": []}
J = {"journey_id": "jrn-s1-f1-c1-1", "space_id": "s1", "flow_id": "f1",
     "participant_id": "c1", "status": "active", "inbox_thread_id": "710"}


@pytest.fixture
def client():
    return AsyncClient(api_key="unpod_sk_test", base_url="https://api.example.test")


@pytest.fixture
def org_client():
    """Platform-token mode, as the SDK reaches org-scoped routes (pilots)."""
    from unpod import TokenAuth

    return AsyncClient(auth=TokenAuth("t", org_handle="acme"), base_url="https://api.example.test")


# ── models ────────────────────────────────────────────────────────────────


def test_models_parse_and_keep_unknown_fields():
    flow = Flow(**{**FLOW, "agentic_follow_ups": False, "brand_new_field": 7})
    assert flow.agentic_follow_ups is False and flow.model_extra["brand_new_field"] == 7
    assert Journey(**J).inbox_thread_id == "710"
    assert JourneyStartResult(participant_id=None, result="invalid").journey_id is None
    m = ConversationMessage(direction="outbound", data={"content": "Hi", "subject": "Re: City"})
    assert (m.text, m.subject) == ("Hi", "Re: City")


# ── flows ─────────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_flows_create_and_model_body(client):
    with patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        post.return_value = FLOW
        flow = await client.flows.create("s1", {"name": "Support", "steps": []})
        post.assert_awaited_once_with(f"{BASE}/spaces/s1/flows", json={"name": "Support", "steps": []})
        assert flow.flow_id == "f1"
        await client.flows.create("s1", Flow(**{**FLOW, "brand_new_field": 1}))
        assert "brand_new_field" not in post.await_args.kwargs["json"]


@pytest.mark.anyio
async def test_flows_read_update_validate(client):
    with patch.object(client.flows._http, "put", new_callable=AsyncMock) as put, \
         patch.object(client.flows._http, "get", new_callable=AsyncMock) as get, \
         patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        put.return_value = {**FLOW, "version": 2}
        assert (await client.flows.update("f1", {"name": "v2"})).version == 2
        put.assert_awaited_once_with(f"{BASE}/flows/f1", json={"name": "v2"})
        get.return_value = FLOW
        await client.flows.get("f1", version=2)
        get.assert_awaited_with(f"{BASE}/flows/f1", params={"version": 2})
        get.return_value = [FLOW]
        assert [f.flow_id for f in await client.flows.list("s1")] == ["f1"]
        get.assert_awaited_with(f"{BASE}/spaces/s1/flows", params=None)
        get.return_value = [{"flow_id": "f1", "version": 1}]
        assert len(await client.flows.versions("f1")) == 1
        post.return_value = {"valid": True}
        assert (await client.flows.validate("s1", {"steps": []}))["valid"] is True
        post.assert_awaited_with(f"{BASE}/spaces/s1/flows/validate", json={"steps": []})


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path"), [("activate", "activate"), ("pause", "deactivate"), ("archive", "archive")])
async def test_flows_status_switches(client, method, path):
    with patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        post.return_value = {"flow_id": "f1", "status": "on"}
        assert (await getattr(client.flows, method)("f1"))["flow_id"] == "f1"
    post.assert_awaited_once_with(f"{BASE}/flows/f1/{path}", json=None)


def _http_error(status: int, body: object) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.example.test/x")
    response = httpx.Response(status, json=body, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "body", "problems"),
    [
        (422, {"detail": ["step 'a': fixed content needs a body or a template", "no steps"]},
         ["step 'a': fixed content needs a body or a template", "no steps"]),
        (422, {"detail": "agentic_tools references unknown connector(s): ['x']"},
         ["agentic_tools references unknown connector(s): ['x']"]),
        (422, {"detail": [{"loc": ["body", "steps", 0, "id"], "msg": "Field required"}]},
         ["body.steps.0.id: Field required"]),
        (409, {"detail": "flow 'f1' exists; PUT /flows/f1 to create a new version"},
         ["flow 'f1' exists; PUT /flows/f1 to create a new version"]),
    ],
)
async def test_flow_refusals_become_typed_errors(client, status, body, problems):
    with patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        post.side_effect = _http_error(status, body)
        with pytest.raises(FlowValidationError) as caught:
            await client.flows.create("s1", {"steps": []})
    assert caught.value.status_code == status
    assert caught.value.problems == problems


@pytest.mark.anyio
async def test_other_http_errors_pass_through(client):
    with patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        post.side_effect = _http_error(503, {"detail": "down"})
        with pytest.raises(httpx.HTTPStatusError):
            await client.flows.activate("f1")


@pytest.mark.anyio
async def test_start_on_an_inactive_flow_is_a_typed_error(client):
    with patch.object(client.journeys._http, "post", new_callable=AsyncMock) as post:
        post.side_effect = _http_error(409, {"detail": "flow is 'off', not active"})
        with pytest.raises(FlowValidationError, match="not active"):
            await client.journeys.start("f1", "c1")


@pytest.mark.anyio
async def test_templates_profiles_and_channels(client):
    with patch.object(client.flows._http, "get", new_callable=AsyncMock) as get, \
         patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        get.return_value = [{"template_id": "t1", "name": "Intro"}]
        assert (await client.flows.templates())[0]["template_id"] == "t1"
        get.assert_awaited_with(f"{BASE}/flows/templates", params=None)
        get.return_value = [{"profile_id": "sdr", "name": "sdr", "description": "Sales"}]
        assert (await client.flows.agentic_profiles())[0]["profile_id"] == "sdr"
        get.assert_awaited_with(f"{BASE}/agentic-profiles", params=None)
        get.return_value = [{"account_id": "a1", "connector_type": "email", "status": "connected"}]
        assert (await client.flows.channels("s1"))[0]["account_id"] == "a1"
        get.assert_awaited_with(f"{BASE}/spaces/s1/connectors", params=None)
        post.return_value = FLOW
        flow = await client.flows.create_from_template("s1", "t1", name="Support")
        post.assert_awaited_once_with(
            f"{BASE}/spaces/s1/flows/from-template", json={"template_id": "t1", "name": "Support"}
        )
        assert flow.flow_id == "f1"


@pytest.mark.anyio
async def test_flow_status_counts_its_journeys(client):
    with patch.object(client.flows._http, "get", new_callable=AsyncMock) as get:
        get.side_effect = [
            {**FLOW, "status": "on", "version": 3},
            [J, {**J, "journey_id": "j2"}, {**J, "journey_id": "j3", "status": "completed"}],
        ]
        status = await client.flows.status("f1")
    assert status == {
        "flow_id": "f1", "status": "on", "version": 3,
        "journeys": {"active": 2, "completed": 1}, "truncated": False,
    }
    assert get.await_args_list[1].args == (f"{BASE}/journeys",)
    assert get.await_args_list[1].kwargs == {"params": {"space_id": "s1", "flow_id": "f1"}}


def test_a_flow_may_leave_its_id_to_the_server():
    assert Flow(name="New").flow_id is None


# ── journeys ──────────────────────────────────────────────────────────────


@pytest.mark.anyio
async def test_journeys_start_one_and_many(client):
    with patch.object(client.journeys._http, "post", new_callable=AsyncMock) as post:
        post.return_value = {"results": [{"participant_id": "c1", "journey_id": J["journey_id"], "result": "started"}]}
        [r] = await client.journeys.start("f1", participant_id="c1", contact={"email": "a@b.com", "name": "Ankit"})
        post.assert_awaited_once_with(
            f"{BASE}/flows/f1/journeys",
            json={"participants": [{"participant_id": "c1", "email": "a@b.com", "name": "Ankit"}]},
        )
        assert r.result == "started"
        post.return_value = {"results": [{"participant_id": "c1", "journey_id": "j1", "result": "started"},
                                         {"participant_id": None, "result": "invalid"}]}
        results = await client.journeys.start("f1", participants=[{"participant_id": "c1"}, {"participant_id": "Bad"}])
        assert [x.result for x in results] == ["started", "invalid"]


def test_journeys_start_and_list_validate_inputs(client):
    with pytest.raises(ValueError, match="participant"):
        asyncio.run(client.journeys.start("f1"))
    with pytest.raises(ValueError, match="space_id"):
        asyncio.run(client.journeys.list(""))


@pytest.mark.anyio
async def test_journeys_get_list_cancel_event(client):
    with patch.object(client.journeys._http, "get", new_callable=AsyncMock) as get, \
         patch.object(client.journeys._http, "post", new_callable=AsyncMock) as post:
        get.return_value = J
        assert (await client.journeys.get(J["journey_id"])).status == "active"
        get.return_value = [J]
        await client.journeys.list("s1", flow_id="f1", status="active")
        get.assert_awaited_with(f"{BASE}/journeys", params={"space_id": "s1", "flow_id": "f1", "status": "active"})
        post.return_value = {"journey_id": J["journey_id"], "status": "cancelled"}
        await client.journeys.cancel(J["journey_id"])
        post.assert_awaited_with(f"{BASE}/journeys/{J['journey_id']}/cancel", json=None)
        post.return_value = {"ok": True}
        await client.journeys.send_event(J["journey_id"], "reply", outcome={"status": "replied"},
                                         summary="yes", content={"body": "yes"}, channel="email")
        post.assert_awaited_with(
            f"{BASE}/journeys/{J['journey_id']}/events",
            json={"type": "reply", "outcome": {"status": "replied"}, "summary": "yes",
                  "content": {"body": "yes"}, "channel": "email"},
        )


@pytest.mark.anyio
async def test_conversation_and_empty_thread(client):
    with patch.object(client.journeys._http, "get", new_callable=AsyncMock) as get:
        get.return_value = [{"direction": "inbound", "data": {"content": "places?"}},
                            {"direction": "outbound", "data": {"content": "Hi Ankit"}}]
        msgs = await client.journeys.conversation(Journey(**J))
        get.assert_awaited_once_with(f"{BASE}/spaces/s1/threads/710/blocks", params={"limit": 200})
        assert [(m.direction, m.text) for m in msgs] == [("inbound", "places?"), ("outbound", "Hi Ankit")]
        get.reset_mock()
        assert await client.journeys.conversation(Journey(**{**J, "inbox_thread_id": None})) == []
        get.assert_not_awaited()


@pytest.mark.anyio
async def test_ping_and_approval(client):
    with patch.object(client.journeys._http, "get", new_callable=AsyncMock) as get, \
         patch.object(client.journeys._http, "post", new_callable=AsyncMock) as post:
        get.return_value = {"ok": True, "checks": []}
        assert (await client.journeys.ping())["ok"] is True
        get.assert_awaited_once_with("/api/v2/platform/journey/connection/", params=None)
        post.return_value = {"status_code": 200, "message": "ok",
                             "data": {"space_id": "s1", "approval": "autonomous", "previous": None}}
        result = await client.journeys.set_approval("s1", "autonomous")
        post.assert_awaited_once_with("/api/v1/autonomous-agents/spaces/s1/agent-approval/",
                                      json={"approval": "autonomous"})
        assert result["approval"] == "autonomous"
    with pytest.raises(ValueError, match="human_review"):
        await client.journeys.set_approval("s1", "yolo")


def test_sync_client_exposes_both():
    c = Client(api_key="unpod_sk_test", base_url="https://api.example.test")
    for name in ("create", "activate", "pause"):
        assert hasattr(c.flows, name)
    for name in ("start", "conversation", "set_approval", "ping"):
        assert hasattr(c.journeys, name)


@pytest.mark.anyio
async def test_flow_agents_lists_pilots_and_sdk_agents_in_one(org_client):
    client = org_client
    pilots = {"message": "ok", "count": 2, "data": [
        {"handle": "sales-bot", "name": "Sales Bot", "type": "Pilot"},
        {"handle": "agt_synced", "name": "Synced", "type": "Pilot"},
    ]}
    sdk_agents = [
        {"agent_id": "agt_1", "name": "Asha", "brain": {"type": "prompt", "ref": "You are Asha."}},
        {"agent_id": "agt_2", "name": "Book", "brain": {"type": "playbook", "ref": "PB_1"}},
        {"agent_id": "agt_3", "name": "Relay", "brain": {"type": "endpoint", "ref": "https://x"}},
        {"agent_id": "agt_synced", "name": "Synced", "brain": {"type": "prompt", "ref": "x"}},
    ]
    with patch.object(client.flows._http, "get", new_callable=AsyncMock) as get:
        get.side_effect = [pilots, sdk_agents]
        agents = await client.flows.agents()
    assert [c.args[0] for c in get.await_args_list] == [
        "/api/v2/platform/agents/", "/api/v2/platform/speech/v1/agents",
    ]
    assert agents == [
        {"agent_id": "sales-bot", "name": "Sales Bot", "kind": "pilot"},
        {"agent_id": "agt_synced", "name": "Synced", "kind": "pilot"},
        {"agent_id": "sv:agt_1", "name": "Asha", "kind": "agent", "brain": "prompt"},
        {"agent_id": "sv:agt_2", "name": "Book", "kind": "agent", "brain": "playbook"},
    ]


@pytest.mark.anyio
async def test_a_create_can_carry_an_idempotency_key(client):
    with patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        post.return_value = FLOW
        await client.flows.create("s1", {"name": "New"}, idempotency_key="idem-1")
        post.assert_awaited_once_with(
            f"{BASE}/spaces/s1/flows", json={"name": "New"}, headers={"Idempotency-Key": "idem-1"}
        )
        post.reset_mock()
        await client.flows.create_from_template("s1", "t1", idempotency_key="idem-2")
        assert post.await_args.kwargs["headers"] == {"Idempotency-Key": "idem-2"}


@pytest.mark.anyio
async def test_http_post_merges_extra_headers():
    from unpod.management._auth import BearerAuth
    from unpod.management._http import AsyncHTTPClient

    http = AsyncHTTPClient(BearerAuth("unpod_sk_test"), base_url="https://api.example.test")
    fake = AsyncMock()
    fake.post.return_value = httpx.Response(201, json={}, request=httpx.Request("POST", "https://x"))
    with patch.object(http, "_ensure_client", AsyncMock(return_value=fake)):
        await http.post("/api/x", json={}, headers={"Idempotency-Key": "k"})
    sent = fake.post.await_args.kwargs["headers"]
    assert sent["Idempotency-Key"] == "k" and "Authorization" in sent



@pytest.mark.anyio
async def test_flow_status_says_when_the_count_hit_the_list_cap(client):
    with patch.object(client.flows._http, "get", new_callable=AsyncMock) as get:
        get.side_effect = [FLOW, [{**J, "journey_id": f"j{i}"} for i in range(100)]]
        status = await client.flows.status("f1")
    assert status["truncated"] is True and status["journeys"] == {"active": 100}


@pytest.mark.anyio
async def test_flow_agents_needs_an_org_handle():
    bare = AsyncClient(api_key="unpod_sk_test", base_url="https://api.example.test")
    with patch.object(bare.flows._http, "get", new_callable=AsyncMock) as get, \
         pytest.raises(ValueError, match="UNPOD_ORG_HANDLE"):
        await bare.flows.agents()
    get.assert_not_awaited()


@pytest.mark.anyio
async def test_extra_headers_never_replace_auth():
    from unpod.management._auth import BearerAuth
    from unpod.management._http import AsyncHTTPClient

    http = AsyncHTTPClient(BearerAuth("unpod_sk_test"), base_url="https://api.example.test")
    fake = AsyncMock()
    fake.post.return_value = httpx.Response(201, json={}, request=httpx.Request("POST", "https://x"))
    with patch.object(http, "_ensure_client", AsyncMock(return_value=fake)):
        await http.post("/api/x", json={}, headers={"Authorization": "Bearer evil", "Idempotency-Key": "k"})
    sent = fake.post.await_args.kwargs["headers"]
    assert sent["Authorization"] == "Bearer unpod_sk_test" and sent["Idempotency-Key"] == "k"
