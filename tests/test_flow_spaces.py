"""A space given as a handle, a token or a Space object (mocked HTTP)."""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from unpod import AsyncClient, JWTAuth
from unpod.management._spaces import SpaceResolver, requires_proxy_auth
from unpod.models import Space

SPACES = "/api/v2/platform/spaces"


@pytest.fixture
def jwt_http():
    return AsyncClient(
        auth=JWTAuth("jwt", org_handle="acme"), base_url="https://api.example.test"
    ).flows._http


def _not_found() -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://api.example.test/x")
    return httpx.HTTPStatusError(
        "404", request=request, response=httpx.Response(404, request=request)
    )


@pytest.mark.anyio
async def test_a_space_object_gives_its_slug_without_a_call(jwt_http):
    with patch.object(jwt_http, "get", new_callable=AsyncMock) as get:
        assert (
            await SpaceResolver(jwt_http).slug(
                Space(slug="support-dh2dzdja", token="TOK")
            )
            == "support-dh2dzdja"
        )
        get.assert_not_awaited()


@pytest.mark.anyio
async def test_a_token_resolves_to_its_slug(jwt_http):
    with patch.object(jwt_http, "get", new_callable=AsyncMock) as get:
        get.return_value = {"data": {"slug": "support-dh2dzdja", "token": "TOK"}}
        assert await SpaceResolver(jwt_http).slug("TOK") == "support-dh2dzdja"
        get.assert_awaited_once_with(f"{SPACES}/TOK/", params=None)


@pytest.mark.anyio
async def test_a_handle_404s_and_is_used_as_is(jwt_http):
    with patch.object(jwt_http, "get", new_callable=AsyncMock) as get:
        get.side_effect = _not_found()
        assert (
            await SpaceResolver(jwt_http).slug("support-dh2dzdja") == "support-dh2dzdja"
        )


@pytest.mark.anyio
async def test_a_lookup_answer_without_a_slug_means_a_handle(jwt_http):
    """Django's house style answers some misses with a 2xx and a message."""
    with patch.object(jwt_http, "get", new_callable=AsyncMock) as get:
        get.return_value = {"message": "Space not found"}
        assert (
            await SpaceResolver(jwt_http).slug("support-dh2dzdja") == "support-dh2dzdja"
        )


@pytest.mark.anyio
async def test_resolution_is_cached(jwt_http):
    with patch.object(jwt_http, "get", new_callable=AsyncMock) as get:
        get.return_value = {"data": {"slug": "support-dh2dzdja"}}
        resolver = SpaceResolver(jwt_http)
        await resolver.slug("TOK")
        await resolver.slug("TOK")
        assert get.await_count == 1


@pytest.mark.anyio
async def test_without_jwt_auth_nothing_is_looked_up():
    http = AsyncClient(
        api_key="unpod_sk_test", base_url="https://api.example.test"
    ).flows._http
    with patch.object(http, "get", new_callable=AsyncMock) as get:
        assert await SpaceResolver(http).slug("s1") == "s1"
        get.assert_not_awaited()


def test_proxy_auth_is_required_by_name():
    http = AsyncClient(
        api_key="unpod_sk_test", base_url="https://api.example.test"
    ).flows._http
    with pytest.raises(ValueError, match="channels.list needs proxy-mode auth"):
        requires_proxy_auth(http, "channels.list")
