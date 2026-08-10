"""Tests for the analytics resource (client.analytics).

These pin the wire contract: which path each method hits, what body it sends,
and how the response parses. A block runs automatically on every session its
attached agents finish, so "attach" is the only verb between authoring a block
and getting results — there is deliberately no "run".
"""

from unittest.mock import AsyncMock, patch

import pytest
from unpod import AsyncClient

_BLOCK = {
    "block_id": "ABK_1",
    "name": "Lead Qualification",
    "prompt": "Assess whether the caller is a qualified lead.",
    "fields": [{"name": "budget", "type": "int", "description": ""}],
    "version": 1,
    "state": "active",
}

_RESULT = {
    "result_id": "ANR_1",
    "block_id": "ABK_1",
    "name": "Lead Qualification",
    "session_id": "s-1",
    "call_id": "SCL_1",
    "agent_id": "agent-a",
    "status": "ok",
    "summary": "Caller has budget and wants a demo.",
    "data": {"budget": 5000},
    "block_version": 1,
}


@pytest.fixture
def client():
    return AsyncClient(api_key="unpod_sk_test", base_url="https://api.example.test")


@pytest.mark.anyio
async def test_create_sends_name_prompt_and_fields(client: AsyncClient):
    with patch.object(
        client.analytics._http, "post", new_callable=AsyncMock
    ) as mock_post:
        mock_post.return_value = {"data": _BLOCK}
        block = await client.analytics.create(
            name="Lead Qualification",
            prompt="Assess whether the caller is a qualified lead.",
            fields=[{"name": "budget", "type": "int"}],
        )
        assert block.block_id == "ABK_1"
        assert block.fields_spec[0]["name"] == "budget"
        path, body = mock_post.call_args[0]
        assert path == "/speech/v1/analytics-blocks"
        assert body["fields"] == [{"name": "budget", "type": "int"}]


@pytest.mark.anyio
async def test_create_omits_optional_keys_it_was_not_given(client: AsyncClient):
    """Sending model=None would override a server default with nothing."""
    with patch.object(
        client.analytics._http, "post", new_callable=AsyncMock
    ) as mock_post:
        mock_post.return_value = {"data": _BLOCK}
        await client.analytics.create(name="X", prompt="Y")
        _, body = mock_post.call_args[0]
        assert "model" not in body
        assert "summary_description" not in body
        assert body["fields"] == []


@pytest.mark.anyio
async def test_create_passes_summary_description_and_model(client: AsyncClient):
    with patch.object(
        client.analytics._http, "post", new_callable=AsyncMock
    ) as mock_post:
        mock_post.return_value = {"data": _BLOCK}
        await client.analytics.create(
            name="X",
            prompt="Y",
            summary_description="Two sentences.",
            model="anthropic/claude-x",
        )
        _, body = mock_post.call_args[0]
        assert body["summary_description"] == "Two sentences."
        assert body["model"] == "anthropic/claude-x"


@pytest.mark.anyio
async def test_list_and_include_archived(client: AsyncClient):
    with patch.object(
        client.analytics._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {"data": [_BLOCK]}
        blocks = await client.analytics.list()
        assert len(blocks) == 1
        mock_get.assert_called_with("/speech/v1/analytics-blocks")

        await client.analytics.list(include_archived=True)
        mock_get.assert_called_with("/speech/v1/analytics-blocks?include_archived=true")


@pytest.mark.anyio
async def test_get_returns_the_attached_agents(client: AsyncClient):
    with patch.object(
        client.analytics._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {"data": {**_BLOCK, "agent_ids": ["a", "b"]}}
        block = await client.analytics.get("ABK_1")
        assert block.agent_ids == ["a", "b"]
        mock_get.assert_called_once_with("/speech/v1/analytics-blocks/ABK_1")


@pytest.mark.anyio
async def test_update_sends_only_what_changed(client: AsyncClient):
    with patch.object(
        client.analytics._http, "patch", new_callable=AsyncMock
    ) as mock_patch:
        mock_patch.return_value = {"data": {**_BLOCK, "version": 2}}
        block = await client.analytics.update("ABK_1", prompt="New prompt")
        assert block.version == 2
        path, body = mock_patch.call_args[0]
        assert path == "/speech/v1/analytics-blocks/ABK_1"
        assert body == {"prompt": "New prompt"}


@pytest.mark.anyio
async def test_delete_archives(client: AsyncClient):
    with patch.object(
        client.analytics._http, "delete", new_callable=AsyncMock
    ) as mock_del:
        await client.analytics.delete("ABK_1")
        mock_del.assert_called_once_with("/speech/v1/analytics-blocks/ABK_1")


@pytest.mark.anyio
async def test_attach_and_detach(client: AsyncClient):
    with patch.object(
        client.analytics._http, "post", new_callable=AsyncMock
    ) as mock_post:
        await client.analytics.attach("ABK_1", "agent-a")
        mock_post.assert_called_once_with(
            "/speech/v1/analytics-blocks/ABK_1/attach", {"agent_id": "agent-a"}
        )
    with patch.object(
        client.analytics._http, "delete", new_callable=AsyncMock
    ) as mock_del:
        await client.analytics.detach("ABK_1", "agent-a")
        mock_del.assert_called_once_with("/speech/v1/analytics-blocks/ABK_1/attach/agent-a")


@pytest.mark.anyio
async def test_one_plan_attaches_to_many_agents(client: AsyncClient):
    """The reason blocks live in their own collection."""
    with patch.object(
        client.analytics._http, "post", new_callable=AsyncMock
    ) as mock_post:
        for agent in ("agent-a", "agent-b"):
            await client.analytics.attach("ABK_1", agent)
        assert [c[0][1]["agent_id"] for c in mock_post.call_args_list] == [
            "agent-a",
            "agent-b",
        ]


@pytest.mark.anyio
async def test_results_by_plan_carry_summary_and_typed_data(client: AsyncClient):
    with patch.object(
        client.analytics._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {"data": [_RESULT]}
        results = await client.analytics.results("ABK_1")
        assert results[0].summary == "Caller has budget and wants a demo."
        assert results[0].data == {"budget": 5000}
        mock_get.assert_called_once_with(
            "/speech/v1/analytics-blocks/ABK_1/results?limit=100&skip=0"
        )


@pytest.mark.anyio
async def test_results_paging(client: AsyncClient):
    with patch.object(
        client.analytics._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {"data": []}
        await client.analytics.results("ABK_1", limit=10, skip=30)
        mock_get.assert_called_once_with(
            "/speech/v1/analytics-blocks/ABK_1/results?limit=10&skip=30"
        )


@pytest.mark.anyio
async def test_results_for_a_session_return_every_plan(client: AsyncClient):
    with patch.object(
        client.analytics._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {
            "data": [_RESULT, {**_RESULT, "block_id": "ABK_2", "result_id": "ANR_2"}]
        }
        results = await client.analytics.for_session("s-1")
        assert [r.block_id for r in results] == ["ABK_1", "ABK_2"]
        mock_get.assert_called_once_with("/speech/v1/sessions/s-1/analytics")


@pytest.mark.anyio
async def test_results_for_a_call(client: AsyncClient):
    with patch.object(
        client.analytics._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {"data": [_RESULT]}
        results = await client.analytics.for_call("SCL_1")
        assert results[0].call_id == "SCL_1"
        mock_get.assert_called_once_with("/speech/v1/calls/SCL_1/analytics")


@pytest.mark.anyio
async def test_failed_and_skipped_results_parse(client: AsyncClient):
    """A run that failed or had nothing to read is still a readable result."""
    with patch.object(
        client.analytics._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {
            "data": [
                {**_RESULT, "status": "failed", "error": "model timeout", "data": {}},
                {**_RESULT, "status": "skipped", "error": "no transcript", "data": {}},
            ]
        }
        results = await client.analytics.for_session("s-1")
        assert [r.status for r in results] == ["failed", "skipped"]
        assert results[0].error == "model timeout"


def test_analytics_rides_the_proxy_client_never_supervoice_directly():
    """Boundary rule: all developer HTTP goes through backend-core, which
    resolves the org and injects its supervoice key. A resource bound to the
    direct client would bypass that."""
    c = AsyncClient(api_key="k", base_url="https://x.test/platform")
    assert c.analytics._http is c._platform_http
    assert c.analytics._http is not c._http
    assert c._platform_http._base_url.endswith("/api/v2/platform")


def test_analytics_is_exposed_on_both_clients():
    from unpod import Client

    assert hasattr(AsyncClient(api_key="k", base_url="https://x.test"), "analytics")
    assert hasattr(Client(api_key="k", base_url="https://x.test"), "analytics")


# ── templates, success, condition, listing (G1/G2/G3/G5/G6) ──────────────────


@pytest.mark.anyio
async def test_templates_are_fetchable(client: AsyncClient):
    with patch.object(
        client.analytics._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {"data": [{"template_id": "success"}]}
        out = await client.analytics.templates()
        assert out[0]["template_id"] == "success"
        mock_get.assert_called_once_with("/speech/v1/analytics-blocks/templates")


@pytest.mark.anyio
async def test_create_can_fork_a_template(client: AsyncClient):
    with patch.object(
        client.analytics._http, "post", new_callable=AsyncMock
    ) as mock_post:
        mock_post.return_value = {"data": _BLOCK}
        await client.analytics.create(
            name="Mine", prompt="p", from_template="success"
        )
        path, _ = mock_post.call_args[0]
        assert path == "/speech/v1/analytics-blocks?from_template=success"


@pytest.mark.anyio
async def test_create_passes_success_and_condition(client: AsyncClient):
    with patch.object(
        client.analytics._http, "post", new_callable=AsyncMock
    ) as mock_post:
        mock_post.return_value = {"data": _BLOCK}
        await client.analytics.create(
            name="X",
            prompt="Y",
            success_type="enum",
            success_choices=["Yes", "No"],
            condition={"min_turns": 4},
        )
        _, body = mock_post.call_args[0]
        assert body["success_choices"] == ["Yes", "No"]
        assert body["condition"] == {"min_turns": 4}


@pytest.mark.anyio
async def test_success_evaluation_parses_off_the_result(client: AsyncClient):
    """Top-level, not inside data."""
    with patch.object(
        client.analytics._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {
            "data": [{**_RESULT, "success_evaluation": "Partial"}]
        }
        results = await client.analytics.for_session("s-1")
        assert results[0].success_evaluation == "Partial"


@pytest.mark.anyio
async def test_list_results_filters_by_agent(client: AsyncClient):
    with patch.object(
        client.analytics._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {"data": [_RESULT]}
        await client.analytics.list_results(agent_id="agent-a", status="ok")
        (path,) = mock_get.call_args[0]
        assert path.startswith("/speech/v1/analytics-results?")
        assert "agent_id=agent-a" in path and "status=ok" in path


@pytest.mark.anyio
async def test_results_table_requests_the_flat_projection(client: AsyncClient):
    with patch.object(
        client.analytics._http, "get", new_callable=AsyncMock
    ) as mock_get:
        mock_get.return_value = {"data": {"columns": [], "rows": []}}
        out = await client.analytics.results_table("ABK_1")
        (path,) = mock_get.call_args[0]
        assert "block_id=ABK_1" in path and "format=flat" in path
        assert out == {"columns": [], "rows": []}
