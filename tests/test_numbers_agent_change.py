"""client.numbers.agent.change and client.agents.update(number=) (spec §3.4)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from unpod import AsyncClient

pytestmark = pytest.mark.anyio

_ATTACH = "/api/v2/platform/speech/v1/numbers/{}/attach"


@pytest.fixture
def client() -> AsyncClient:
    """Proxy mode: the PUT reassign route is backend-core's."""
    from unpod import TokenAuth

    return AsyncClient(auth=TokenAuth("tok", "org"), base_url="https://api.example.test")


async def test_change_puts_reassign_by_e164(client):
    with patch.object(client.numbers._http, "put", new_callable=AsyncMock) as put:
        put.return_value = {"data": {"ok": True}}
        await client.numbers.agent.change("+14155550100", "AG_dev")
    put.assert_awaited_once_with(
        _ATTACH.format("+14155550100"),
        json={"agent_id": "AG_dev", "number": "+14155550100"},
    )


async def test_change_by_id(client):
    with patch.object(client.numbers._http, "put", new_callable=AsyncMock) as put:
        put.return_value = {"data": {"ok": True}}
        await client.numbers.agent.change("124", "AG_dev")
    put.assert_awaited_once_with(_ATTACH.format("124"), json={"agent_id": "AG_dev"})


def _http_error(status: int, body: dict) -> httpx.HTTPStatusError:
    req = httpx.Request("PUT", "http://x")
    return httpx.HTTPStatusError("err", request=req, response=httpx.Response(status, json=body, request=req))


async def test_update_with_number_reassigns(client):
    with patch.object(client.agents._http, "put", new_callable=AsyncMock) as put, \
         patch.object(client.agents._http, "get", new_callable=AsyncMock) as get:
        put.return_value = get.return_value = {"data": {"agent_id": "AG_dev", "voices": []}}
        with patch.object(client.agents, "_numbers_change", new_callable=AsyncMock) as change:
            await client.agents.update("AG_dev", name="x", number="+14155550100")
    change.assert_awaited_once_with("+14155550100", "AG_dev")


async def test_update_falls_back_to_attach_when_not_attached(client):
    change = AsyncMock(side_effect=_http_error(409, {"code": "not_attached"}))
    attach = AsyncMock(return_value={})
    get = AsyncMock(return_value={"data": {"agent_id": "AG_dev", "voices": []}})
    with patch.object(client.agents, "_numbers_change", change), \
         patch.object(client.agents, "_numbers_attach", attach), \
         patch.object(client.agents._http, "get", get):
        await client.agents.update("AG_dev", number="+14155550100")
    attach.assert_awaited_once_with("+14155550100", "AG_dev")


async def test_update_does_not_attach_on_404(client):
    change = AsyncMock(side_effect=_http_error(404, {"code": "not_found"}))
    attach = AsyncMock()
    get = AsyncMock(return_value={"data": {"agent_id": "AG_dev", "voices": []}})
    with patch.object(client.agents, "_numbers_change", change), \
         patch.object(client.agents, "_numbers_attach", attach), \
         patch.object(client.agents._http, "get", get):
        with pytest.raises(httpx.HTTPStatusError):
            await client.agents.update("AG_dev", number="+19990000000")
    attach.assert_not_awaited()


def test_sync_client_exposes_agent_change():
    from unpod import Client

    c = Client(api_key="unpod_sk_test")
    assert callable(c.numbers.agent.change)


async def test_change_sends_attach_type_when_given(client):
    with patch.object(client.numbers._http, "put", new_callable=AsyncMock) as put:
        put.return_value = {"data": {"ok": True}}
        await client.numbers.agent.change("124", "pilot-1", attach_type="agent")
    put.assert_awaited_once_with(
        _ATTACH.format("124"), json={"agent_id": "pilot-1", "attach_type": "agent"}
    )


# --- direct-to-supervoice mode (Bearer auth, no backend-core proxy) --------


@pytest.fixture
def direct() -> AsyncClient:
    return AsyncClient(api_key="sk_direct", base_url="http://sv.test/platform")


@pytest.fixture
def proxy() -> AsyncClient:
    from unpod import TokenAuth

    return AsyncClient(auth=TokenAuth("tok", "org"), base_url="https://api.example.test")


async def test_direct_change_patches_sv_number(direct):
    with patch.object(direct.numbers._http, "patch", new_callable=AsyncMock) as p, \
         patch.object(direct.numbers._http, "put", new_callable=AsyncMock) as put:
        p.return_value = {"number_id": "NUM_1", "agent_id": "AG_dev"}
        await direct.numbers.agent.change("NUM_1", "AG_dev")
    p.assert_awaited_once_with("/v1/numbers/NUM_1", json={"agent_id": "AG_dev"})
    put.assert_not_awaited()


async def test_direct_change_resolves_e164_to_number_id(direct):
    get = AsyncMock(return_value=[
        {"id": "NUM_9", "number": "+14155550199"},
        {"id": "NUM_1", "number": "+14155550100"},
    ])
    with patch.object(direct.numbers._http, "get", get), \
         patch.object(direct.numbers._http, "patch", new_callable=AsyncMock) as p:
        p.return_value = {}
        await direct.numbers.agent.change("+14155550100", "AG_dev")
    p.assert_awaited_once_with("/v1/numbers/NUM_1", json={"agent_id": "AG_dev"})


async def test_direct_change_unknown_e164_raises(direct):
    get = AsyncMock(return_value=[])
    with patch.object(direct.numbers._http, "get", get):
        with pytest.raises(LookupError):
            await direct.numbers.agent.change("+19990000000", "AG_dev")


async def test_direct_list_delete_release_use_sv_routes(direct):
    http = direct.numbers._http
    with patch.object(http, "get", new_callable=AsyncMock) as get, \
         patch.object(http, "delete", new_callable=AsyncMock) as delete:
        get.return_value = []
        await direct.numbers.list(status="assigned")
        await direct.numbers.delete("NUM_1")
        await direct.numbers.release("NUM_2")
    get.assert_awaited_once_with("/v1/numbers", params={"status_filter": "assigned"})
    assert [c.args for c in delete.await_args_list] == [
        ("/v1/numbers/NUM_1",), ("/v1/numbers/NUM_2",)
    ]


async def test_proxy_list_delete_keep_proxy_routes(proxy):
    http = proxy.numbers._http
    with patch.object(http, "get", new_callable=AsyncMock) as get, \
         patch.object(http, "delete", new_callable=AsyncMock) as delete:
        get.return_value = []
        await proxy.numbers.list(status="assigned")
        await proxy.numbers.delete("124")
    get.assert_awaited_once_with(
        "/api/v2/platform/speech/v1/numbers", params={"status": "assigned"}
    )
    delete.assert_awaited_once_with("/api/v2/platform/speech/v1/numbers/124")


async def test_update_with_number_returns_refetched_agent(client):
    put = AsyncMock(return_value={"data": {"agent_id": "AG_dev", "number": None, "voices": []}})
    get = AsyncMock(return_value={"data": {"agent_id": "AG_dev", "number": "+14155550100", "voices": []}})
    with patch.object(client.agents._http, "put", put), \
         patch.object(client.agents._http, "get", get), \
         patch.object(client.agents, "_numbers_change", new_callable=AsyncMock):
        agent = await client.agents.update("AG_dev", name="x", number="+14155550100")
    assert agent.number == "+14155550100"
    get.assert_awaited_once_with("/api/v2/platform/speech/v1/agents/AG_dev")
