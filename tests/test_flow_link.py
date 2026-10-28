"""flows.link, flow objects in place of ids, and builder flows (mocked HTTP)."""

from unittest.mock import AsyncMock, patch

import pytest

from unpod import AsyncClient, Client, JWTAuth
from unpod.flows import AgentStep
from unpod.flows import Flow as FlowBuilder
from unpod.models import Channel, Flow

BASE = "/api/v2/platform/journey/v1"
SPACE = "support-dh2dzdja"
GMAIL = Channel(
    type="gmail",
    space=SPACE,
    connection_id=1,
    account_id="acc_1",
    connector_type="email",
    connector_id="gmail",
)
AGENT_STEP = {
    "id": "step_1",
    "order": 1,
    "action": "email",
    "connector_account_ids": [],
    "agentic": {"enabled": True},
}
STORED = {
    "flow_id": "f1",
    "space_id": SPACE,
    "name": "Support",
    "status": "draft",
    "steps": [AGENT_STEP],
}


@pytest.fixture
def client():
    return AsyncClient(api_key="unpod_sk_test", base_url="https://api.example.test")


@pytest.mark.anyio
async def test_create_accepts_a_builder_flow(client):
    builder = FlowBuilder(name="Support", steps=[AgentStep(channel=GMAIL)])
    with patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        post.return_value = STORED
        await client.flows.create(SPACE, builder)
        post.assert_awaited_once_with(
            f"{BASE}/spaces/{SPACE}/flows", json=builder.to_definition()
        )


@pytest.mark.anyio
async def test_validate_and_update_accept_a_builder_flow(client):
    builder = FlowBuilder(name="Support", steps=[AgentStep(channel=GMAIL)])
    with (
        patch.object(client.flows._http, "post", new_callable=AsyncMock) as post,
        patch.object(client.flows._http, "put", new_callable=AsyncMock) as put,
    ):
        post.return_value = {"valid": True}
        put.return_value = STORED
        await client.flows.validate(SPACE, builder)
        await client.flows.update("f1", builder)
        assert post.await_args.kwargs["json"] == builder.to_definition()
        assert put.await_args.kwargs["json"] == builder.to_definition()


@pytest.mark.anyio
@pytest.mark.parametrize(
    "method", ["activate", "pause", "archive", "versions", "lead_sources"]
)
async def test_flow_methods_accept_a_flow_object(client, method):
    with (
        patch.object(client.flows._http, "post", new_callable=AsyncMock) as post,
        patch.object(client.flows._http, "get", new_callable=AsyncMock) as get,
    ):
        post.return_value = {}
        get.return_value = []
        await getattr(client.flows, method)(Flow(**STORED))
        path = (post.await_args or get.await_args).args[0]
        assert path.startswith(f"{BASE}/flows/f1/")


@pytest.mark.anyio
async def test_a_space_token_is_resolved_on_create():
    client = AsyncClient(
        auth=JWTAuth("jwt", org_handle="acme"), base_url="https://api.example.test"
    )
    http = client.flows._http
    with (
        patch.object(http, "get", new_callable=AsyncMock) as get,
        patch.object(http, "post", new_callable=AsyncMock) as post,
    ):
        get.return_value = {"data": {"slug": SPACE, "token": "TOK"}}
        post.return_value = STORED
        await client.flows.create("TOK", {"name": "Support", "steps": []})
        assert post.await_args.args[0] == f"{BASE}/spaces/{SPACE}/flows"


@pytest.mark.anyio
async def test_link_sets_the_account_and_adds_an_email_source(client):
    http = client.flows._http
    with (
        patch.object(http, "get", new_callable=AsyncMock) as get,
        patch.object(http, "put", new_callable=AsyncMock) as put,
        patch.object(http, "post", new_callable=AsyncMock) as post,
    ):
        get.side_effect = lambda path, params=None: (
            STORED if path == f"{BASE}/flows/f1" else []
        )
        put.return_value = {**STORED, "version": 2}
        post.return_value = {"source_id": "src_1", "type": "email"}
        flow = await client.flows.link("f1", GMAIL)
    sent = put.await_args.kwargs["json"]
    assert sent["steps"][0]["connector_account_ids"] == ["acc_1"]
    assert "flow_id" not in sent and "version" not in sent
    # superbook reads a missing status as "draft": linking must not turn a flow off.
    assert sent["status"] == "draft"
    post.assert_awaited_once_with(
        f"{BASE}/flows/f1/lead-sources",
        json={"type": "email", "mode": "manual", "account_id": "acc_1"},
    )
    assert flow.version == 2


@pytest.mark.anyio
async def test_link_keeps_an_active_flow_on(client):
    http = client.flows._http
    active = {**STORED, "status": "on"}
    with (
        patch.object(http, "get", new_callable=AsyncMock) as get,
        patch.object(http, "put", new_callable=AsyncMock) as put,
        patch.object(http, "post", new_callable=AsyncMock) as post,
    ):
        get.side_effect = lambda path, params=None: (
            active if path == f"{BASE}/flows/f1" else []
        )
        put.return_value = active
        post.return_value = {}
        await client.flows.link("f1", GMAIL)
    assert put.await_args.kwargs["json"]["status"] == "on"


@pytest.mark.anyio
async def test_link_twice_changes_nothing(client):
    http = client.flows._http
    linked = {
        **STORED,
        "agentic_tools": ["gmail", "human"],
        "steps": [{**AGENT_STEP, "connector_account_ids": ["acc_1"]}],
    }
    source = {"source_id": "src_1", "type": "email", "config": {"account_id": "acc_1"}}
    with (
        patch.object(http, "get", new_callable=AsyncMock) as get,
        patch.object(http, "put", new_callable=AsyncMock) as put,
        patch.object(http, "post", new_callable=AsyncMock) as post,
    ):
        get.side_effect = lambda path, params=None: (
            linked if path == f"{BASE}/flows/f1" else [source]
        )
        await client.flows.link(Flow(**linked), GMAIL)
        put.assert_not_awaited()
        post.assert_not_awaited()


@pytest.mark.anyio
async def test_link_refuses_a_flow_already_listening_on_another_mailbox(client):
    http = client.flows._http
    linked = {
        **STORED,
        "agentic_tools": ["gmail", "human"],
        "steps": [{**AGENT_STEP, "connector_account_ids": ["acc_1"]}],
    }
    other = {"source_id": "src_9", "type": "email", "config": {"account_id": "acc_9"}}
    with patch.object(http, "get", new_callable=AsyncMock) as get:
        get.side_effect = lambda path, params=None: (
            linked if path == f"{BASE}/flows/f1" else [other]
        )
        with pytest.raises(ValueError, match="another mailbox"):
            await client.flows.link("f1", GMAIL)


@pytest.mark.anyio
async def test_link_needs_a_ready_channel(client):
    with pytest.raises(ValueError, match="channels.connect"):
        await client.flows.link(
            "f1", Channel(type="gmail", space=SPACE, connection_id=2)
        )


@pytest.mark.anyio
async def test_link_leaves_steps_on_other_channels_alone(client):
    http = client.flows._http
    whatsapp_step = {
        **AGENT_STEP,
        "id": "step_2",
        "order": 2,
        "action": "whatsapp",
        "connector_account_ids": ["acc_w"],
    }
    flow = {**STORED, "steps": [AGENT_STEP, whatsapp_step]}
    with (
        patch.object(http, "get", new_callable=AsyncMock) as get,
        patch.object(http, "put", new_callable=AsyncMock) as put,
        patch.object(http, "post", new_callable=AsyncMock) as post,
    ):
        get.side_effect = lambda path, params=None: (
            flow if path == f"{BASE}/flows/f1" else []
        )
        put.return_value = flow
        post.return_value = {}
        await client.flows.link("f1", GMAIL)
    steps = put.await_args.kwargs["json"]["steps"]
    assert steps[1]["connector_account_ids"] == ["acc_w"]


def test_sync_link_blocks():
    client = Client(api_key="unpod_sk_test", base_url="https://api.example.test")
    http = client._async_client.flows._http
    linked = {
        **STORED,
        "agentic_tools": ["gmail", "human"],
        "steps": [{**AGENT_STEP, "connector_account_ids": ["acc_1"]}],
    }
    source = {"source_id": "src_1", "type": "email", "config": {"account_id": "acc_1"}}
    with patch.object(http, "get", new_callable=AsyncMock) as get:
        get.side_effect = lambda path, params=None: (
            linked if path == f"{BASE}/flows/f1" else [source]
        )
        assert client.flows.link("f1", GMAIL).flow_id == "f1"


# ── final-review fixes ───────────────────────────────────────────────────


@pytest.mark.anyio
async def test_link_keeps_fields_the_sdk_model_does_not_declare(client):
    http = client.flows._http
    stored = {
        **STORED,
        "agentic_tools": ["gmail", "human"],
        "agentic_review_sensitive": True,
        "agentic_review_topics": ["refunds"],
        "description": "inbox",
        "lead_sources": [{"source_id": "s"}],  # the service's, carried forward
        "created_at": "2026-10-28T00:00:00Z",  # storage, refused on a save
    }
    with (
        patch.object(http, "get", new_callable=AsyncMock) as get,
        patch.object(http, "put", new_callable=AsyncMock) as put,
        patch.object(http, "post", new_callable=AsyncMock) as post,
    ):
        get.side_effect = lambda path, params=None: (
            stored if path == f"{BASE}/flows/f1" else []
        )
        put.return_value = stored
        post.return_value = {}
        await client.flows.link("f1", GMAIL)
    sent = put.await_args.kwargs["json"]
    assert sent["agentic_review_sensitive"] is True
    assert sent["agentic_review_topics"] == ["refunds"]
    assert sent["description"] == "inbox"
    assert "lead_sources" not in sent and "created_at" not in sent


@pytest.mark.anyio
async def test_link_lets_the_agent_send_on_the_channel(client):
    http = client.flows._http
    stored = {**STORED, "agentic_tools": ["human"]}
    with (
        patch.object(http, "get", new_callable=AsyncMock) as get,
        patch.object(http, "put", new_callable=AsyncMock) as put,
        patch.object(http, "post", new_callable=AsyncMock) as post,
    ):
        get.side_effect = lambda path, params=None: (
            stored if path == f"{BASE}/flows/f1" else []
        )
        put.return_value = stored
        post.return_value = {}
        await client.flows.link("f1", GMAIL)
    assert put.await_args.kwargs["json"]["agentic_tools"] == ["human", "gmail"]


@pytest.mark.anyio
async def test_link_refuses_before_writing_anything(client):
    http = client.flows._http
    other = {"source_id": "src_9", "type": "email", "config": {"account_id": "acc_9"}}
    with (
        patch.object(http, "get", new_callable=AsyncMock) as get,
        patch.object(http, "put", new_callable=AsyncMock) as put,
    ):
        get.side_effect = lambda path, params=None: (
            STORED if path == f"{BASE}/flows/f1" else [other]
        )
        with pytest.raises(ValueError, match="another mailbox"):
            await client.flows.link("f1", GMAIL)
        put.assert_not_awaited()


@pytest.mark.anyio
async def test_link_refuses_a_channel_from_another_space(client):
    http = client.flows._http
    elsewhere = GMAIL.model_copy(update={"space": "other-space"})
    with (
        patch.object(http, "get", new_callable=AsyncMock) as get,
        patch.object(http, "put", new_callable=AsyncMock) as put,
    ):
        get.side_effect = lambda path, params=None: (
            STORED if path == f"{BASE}/flows/f1" else []
        )
        with pytest.raises(ValueError, match="other-space"):
            await client.flows.link("f1", elsewhere)
        put.assert_not_awaited()
