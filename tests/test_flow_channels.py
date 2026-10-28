"""client.flows.channels: list a space's channels and connect one (mocked HTTP)."""

from unittest.mock import AsyncMock, patch

import pytest

from unpod import AsyncClient, Client, JWTAuth
from unpod.models import Channel

BASE = "/api/v2/platform/journey/v1"
CONNECTIONS = "/api/v1/channels/connections/"
SPACE = "support-dh2dzdja"

ACCOUNT = {
    "account_id": "acc_1",
    "space_id": SPACE,
    "connector_type": "email",
    "connector_id": "gmail",
    "account_ref": "sales@acme.test",
    "status": "connected",
}


def _connection(
    cid: int, *, linked_account: str | None = None, slug: str = SPACE
) -> dict:
    links = []
    if linked_account is not None:
        links.append(
            {
                "id": 90 + cid,
                "slug": slug,
                "type": "general",
                "link_config": {
                    "purpose": "journey",
                    "superbook_account_id": linked_account,
                },
            }
        )
    return {
        "id": cid,
        "app_name": "Gmail",
        "app_slug": "gmail",
        "account": f"s***{cid}@acme.test",
        "status": "active",
        "links": links,
    }


def _routes(connections: list[dict], accounts: list[dict]):
    async def get(path, params=None):
        if path == CONNECTIONS:
            return {"data": connections}
        if path == f"{BASE}/spaces/{SPACE}/connectors":
            return accounts
        if path.startswith("/api/v2/platform/spaces/"):
            return {"message": "not a token"}
        raise AssertionError(f"unexpected GET {path}")

    return get


@pytest.fixture
def client():
    return AsyncClient(
        auth=JWTAuth("jwt", org_handle="acme"), base_url="https://api.example.test"
    )


@pytest.mark.anyio
async def test_calling_channels_still_returns_the_raw_list(client):
    with patch.object(client.flows._http, "get", new_callable=AsyncMock) as get:
        get.side_effect = _routes([], [ACCOUNT])
        assert await client.flows.channels(SPACE) == [ACCOUNT]


@pytest.mark.anyio
async def test_list_merges_connections_and_accounts(client):
    with patch.object(client.flows._http, "get", new_callable=AsyncMock) as get:
        get.side_effect = _routes(
            [_connection(1, linked_account="acc_1"), _connection(2)], [ACCOUNT]
        )
        channels = await client.flows.channels.list(SPACE)
    linked = next(c for c in channels if c.connection_id == 1)
    assert linked.ready and linked.account_id == "acc_1"
    assert (linked.type, linked.connector_type, linked.connector_id) == (
        "gmail",
        "email",
        "gmail",
    )
    assert linked.address == "sales@acme.test" and linked.space == SPACE
    unlinked = next(c for c in channels if c.connection_id == 2)
    assert not unlinked.ready and unlinked.account_id is None
    new = [c for c in channels if c.connection_id is None and c.account_id is None]
    assert [c.type for c in new] == ["gmail"]  # a new mailbox can always be connected


@pytest.mark.anyio
async def test_a_link_to_another_space_does_not_make_a_channel_ready(client):
    with patch.object(client.flows._http, "get", new_callable=AsyncMock) as get:
        get.side_effect = _routes(
            [_connection(1, linked_account="acc_9", slug="other")], []
        )
        channels = await client.flows.channels.list(SPACE)
    assert not next(c for c in channels if c.connection_id == 1).ready


@pytest.mark.anyio
async def test_connect_returns_a_ready_channel_untouched(client):
    ready = Channel(
        type="gmail",
        space=SPACE,
        connection_id=1,
        account_id="acc_1",
        connector_type="email",
        connector_id="gmail",
    )
    with patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        result = await client.flows.channels.connect(ready)
        post.assert_not_awaited()
    assert result.ready and result.channel is ready


@pytest.mark.anyio
async def test_connect_links_a_connection_to_the_space(client):
    http = client.flows._http
    unlinked = Channel(type="gmail", space=SPACE, connection_id=2)
    with (
        patch.object(http, "post", new_callable=AsyncMock) as post,
        patch.object(http, "get", new_callable=AsyncMock) as get,
    ):
        post.return_value = {"data": {"id": 92, "space_slug": SPACE}}
        account = {**ACCOUNT, "account_id": "acc_2"}
        get.side_effect = _routes([_connection(2, linked_account="acc_2")], [account])
        result = await client.flows.channels.connect(unlinked)
        post.assert_awaited_once_with(
            "/api/v1/channels/connections/2/link-space/", json={"space_slug": SPACE}
        )
    assert result.ready and result.channel.account_id == "acc_2"


@pytest.mark.anyio
async def test_connect_starts_gmail_sign_in(client):
    new = Channel(type="gmail", space=SPACE)
    with patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        post.return_value = {
            "redirect_url": "https://accounts.google.com/o/oauth2/auth?x=1"
        }
        result = await client.flows.channels.connect(new)
        post.assert_awaited_once_with(
            "/api/v1/channels/google-auth/", json={"name": "Gmail", "space": SPACE}
        )
    assert not result.ready
    assert result.sign_in_url == "https://accounts.google.com/o/oauth2/auth?x=1"


@pytest.mark.anyio
async def test_a_refused_sign_in_says_why(client):
    with patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        post.return_value = {"message": "You do not have write access to this space"}
        with pytest.raises(ValueError, match="write access"):
            await client.flows.channels.connect(Channel(type="gmail", space=SPACE))


@pytest.mark.anyio
async def test_connect_waits_for_the_new_mailbox(client):
    http = client.flows._http
    seen = [_connection(1, linked_account="acc_1")]
    after = [*seen, _connection(3, linked_account="acc_3")]
    lists = iter([seen, seen, after])  # before sign-in, one poll, then signed in

    async def get(path, params=None):
        if path == CONNECTIONS:
            return {"data": next(lists)}
        return [
            ACCOUNT,
            {**ACCOUNT, "account_id": "acc_3", "account_ref": "new@acme.test"},
        ]

    with (
        patch.object(http, "post", new_callable=AsyncMock) as post,
        patch.object(http, "get", new_callable=AsyncMock) as getter,
        patch("unpod.management.flow_channels.asyncio.sleep", new_callable=AsyncMock),
    ):
        post.return_value = {"redirect_url": "https://accounts.google.com/x"}
        getter.side_effect = get
        result = await client.flows.channels.connect(
            Channel(type="gmail", space=SPACE),
            wait=True,
            poll_interval=0,
            on_sign_in_url=lambda url: None,
        )
    assert result.ready and result.channel.account_id == "acc_3"


@pytest.mark.anyio
async def test_connect_only_touches_the_channel_passed(client):
    http = client.flows._http
    with (
        patch.object(http, "post", new_callable=AsyncMock) as post,
        patch.object(http, "get", new_callable=AsyncMock) as get,
    ):
        post.return_value = {"data": {"id": 95}}
        get.side_effect = _routes(
            [_connection(4), _connection(5, linked_account="acc_5")],
            [{**ACCOUNT, "account_id": "acc_5"}],
        )
        await client.flows.channels.connect(
            Channel(type="gmail", space=SPACE, connection_id=5)
        )
        assert [c.args[0] for c in post.await_args_list] == [
            "/api/v1/channels/connections/5/link-space/"
        ]


@pytest.mark.anyio
async def test_wait_without_a_way_to_show_the_url_is_refused(client):
    with pytest.raises(ValueError, match="on_sign_in_url"):
        await client.flows.channels.connect(
            Channel(type="gmail", space=SPACE), wait=True
        )


@pytest.mark.anyio
async def test_other_types_need_the_app(client):
    with pytest.raises(ValueError, match="connect whatsapp in the Unpod app"):
        await client.flows.channels.connect(Channel(type="whatsapp", space=SPACE))


@pytest.mark.anyio
async def test_channels_need_proxy_auth():
    key_client = AsyncClient(
        api_key="unpod_sk_test", base_url="https://api.example.test"
    )
    with pytest.raises(ValueError, match="channels.list needs proxy-mode auth"):
        await key_client.flows.channels.list(SPACE)


def test_sync_client_channels_block():
    client = Client(
        auth=JWTAuth("jwt", org_handle="acme"), base_url="https://api.example.test"
    )
    http = client._async_client.flows._http
    with patch.object(http, "get", new_callable=AsyncMock) as get:
        get.side_effect = _routes([_connection(1, linked_account="acc_1")], [ACCOUNT])
        assert client.flows.channels(SPACE) == [ACCOUNT]
        channels = client.flows.channels.list(SPACE)
    assert any(c.ready for c in channels)


# ── final-review fixes ───────────────────────────────────────────────────


def _envelope(data, status_code=200, message="Success"):
    """What Django's UnpodJSONRenderer actually sends."""
    return {"status_code": status_code, "message": message, "data": data}


@pytest.mark.anyio
async def test_sign_in_url_is_read_from_the_real_envelope(client):
    with patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        post.return_value = _envelope({"redirect_url": "https://accounts.google.com/x"})
        result = await client.flows.channels.connect(Channel(type="gmail", space=SPACE))
    assert result.sign_in_url == "https://accounts.google.com/x"


@pytest.mark.anyio
async def test_a_refused_connections_list_raises_with_django_s_message(client):
    async def get(path, params=None):
        if path == CONNECTIONS:
            return _envelope(
                {}, 206, "You don't have permission to manage integrations"
            )
        return []

    with patch.object(client.flows._http, "get", new_callable=AsyncMock) as getter:
        getter.side_effect = get
        with pytest.raises(ValueError, match="permission to manage integrations"):
            await client.flows.channels.list(SPACE)


@pytest.mark.anyio
async def test_a_refused_link_space_raises(client):
    with patch.object(client.flows._http, "post", new_callable=AsyncMock) as post:
        post.return_value = _envelope(
            {}, 206, "You do not have write access to this space"
        )
        with pytest.raises(ValueError, match="write access"):
            await client.flows.channels.connect(
                Channel(type="gmail", space=SPACE, connection_id=2)
            )


@pytest.mark.anyio
async def test_a_link_that_leaves_the_channel_unready_raises(client):
    http = client.flows._http
    with (
        patch.object(http, "post", new_callable=AsyncMock) as post,
        patch.object(http, "get", new_callable=AsyncMock) as get,
    ):
        post.return_value = _envelope({"id": 92, "space_slug": SPACE})
        get.side_effect = _routes([_connection(2)], [])  # provisioning did not land
        with pytest.raises(ValueError, match="not ready"):
            await client.flows.channels.connect(
                Channel(type="gmail", space=SPACE, connection_id=2)
            )


@pytest.mark.anyio
async def test_wait_hands_over_the_sign_in_url_before_waiting(client):
    http = client.flows._http
    seen = [_connection(1, linked_account="acc_1")]
    after = [*seen, _connection(3, linked_account="acc_3")]
    lists = iter([seen, after])  # the snapshot before sign-in, then one poll
    urls = []

    async def get(path, params=None):
        if path == CONNECTIONS:
            return {"data": next(lists)}
        return [ACCOUNT, {**ACCOUNT, "account_id": "acc_3"}]

    async def sleep(_seconds):
        assert urls, "waited before the sign-in URL was handed over"

    with (
        patch.object(http, "post", new_callable=AsyncMock) as post,
        patch.object(http, "get", new_callable=AsyncMock) as getter,
        patch("unpod.management.flow_channels.asyncio.sleep", side_effect=sleep),
    ):
        post.return_value = _envelope({"redirect_url": "https://accounts.google.com/x"})
        getter.side_effect = get
        result = await client.flows.channels.connect(
            Channel(type="gmail", space=SPACE),
            wait=True,
            poll_interval=0,
            on_sign_in_url=urls.append,
        )
    assert (
        urls == ["https://accounts.google.com/x"]
        and result.channel.account_id == "acc_3"
    )


@pytest.mark.anyio
async def test_wait_sees_a_reconnected_mailbox_that_becomes_ready(client):
    """Re-authorizing a known mailbox keeps its connection id."""
    http = client.flows._http
    lists = iter([[_connection(1)], [_connection(1, linked_account="acc_1")]])

    async def get(path, params=None):
        if path == CONNECTIONS:
            return {"data": next(lists)}
        return [ACCOUNT]

    with (
        patch.object(http, "post", new_callable=AsyncMock) as post,
        patch.object(http, "get", new_callable=AsyncMock) as getter,
        patch("unpod.management.flow_channels.asyncio.sleep", new_callable=AsyncMock),
    ):
        post.return_value = _envelope({"redirect_url": "https://accounts.google.com/x"})
        getter.side_effect = get
        result = await client.flows.channels.connect(
            Channel(type="gmail", space=SPACE),
            wait=True,
            poll_interval=0,
            on_sign_in_url=lambda url: None,
        )
    assert result.ready and result.channel.connection_id == 1


def test_an_account_in_error_is_not_ready():
    assert not Channel(
        type="gmail", space=SPACE, account_id="acc_1", status="error"
    ).ready
    assert Channel(
        type="gmail", space=SPACE, account_id="acc_1", status="connected"
    ).ready


@pytest.mark.anyio
async def test_list_carries_the_account_status(client):
    with patch.object(client.flows._http, "get", new_callable=AsyncMock) as get:
        get.side_effect = _routes([], [{**ACCOUNT, "status": "error"}])
        channels = await client.flows.channels.list(SPACE)
    orphan = next(c for c in channels if c.account_id == "acc_1")
    assert orphan.status == "error" and not orphan.ready
