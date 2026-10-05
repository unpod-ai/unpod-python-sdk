"""client.flows / client.journeys → Django /api/v2/platform/journey/v1 → superbook /api/v1."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from unpod import AsyncClient, Client
from unpod.models import ConversationMessage, Flow, Journey, JourneyStartResult

BASE = "/api/v2/platform/journey/v1"
FLOW = {"flow_id": "f1", "space_id": "s1", "name": "Support", "status": "draft", "steps": []}
J = {"journey_id": "jrn-s1-f1-c1-1", "space_id": "s1", "flow_id": "f1",
     "participant_id": "c1", "status": "active", "inbox_thread_id": "710"}


@pytest.fixture
def client():
    return AsyncClient(api_key="unpod_sk_test", base_url="https://api.example.test")


# ── models ────────────────────────────────────────────────────────────────


def test_models_parse_and_keep_unknown_fields():
    flow = Flow(**{**FLOW, "agentic_follow_ups": False, "brand_new_field": 7})
    assert flow.agentic_follow_ups is False and flow.model_extra["brand_new_field"] == 7
    assert Journey(**J).inbox_thread_id == "710"
    assert JourneyStartResult(participant_id=None, result="invalid").journey_id is None
    m = ConversationMessage(**{"direction": "outbound", "data": {"content": "Hi", "subject": "Re: City"}})
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


@pytest.mark.anyio
async def test_flows_create_surfaces_validation_errors(client):
    class Http422(Exception):
        pass

    with patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        post.side_effect = Http422("422: fixed content needs a body or a template")
        with pytest.raises(Http422, match="fixed content needs"):
            await client.flows.create("s1", {"steps": [{"id": "step_1"}]})


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
