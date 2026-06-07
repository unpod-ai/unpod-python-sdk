"""Tests for HTTP layer and Client/AsyncClient initialization."""

import os

import pytest
from unpod.management._http import AsyncHTTPClient


@pytest.fixture
def http_client() -> AsyncHTTPClient:
    return AsyncHTTPClient(
        api_key="unpod_sk_test",
        base_url="https://api.example.test",
    )


def test_headers(http_client: AsyncHTTPClient) -> None:
    headers = http_client._headers()
    assert headers["Authorization"] == "Bearer unpod_sk_test"
    assert "X-Request-Id" in headers


def test_default_base_url() -> None:
    c = AsyncHTTPClient(api_key="test")
    assert c._base_url == "https://api.unpod.ai"


def test_sync_client_init() -> None:
    from unpod import Client

    c = Client(api_key="unpod_sk_test", base_url="https://api.example.test")
    assert c._api_key == "unpod_sk_test"


def test_async_client_init() -> None:
    from unpod import AsyncClient

    c = AsyncClient(api_key="unpod_sk_test", base_url="https://api.example.test")
    assert c._api_key == "unpod_sk_test"


def test_client_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNPOD_API_KEY", "unpod_sk_env")
    from unpod import Client

    c = Client()
    assert c._api_key == "unpod_sk_env"


def test_client_missing_key_raises() -> None:
    env_key = os.environ.pop("UNPOD_API_KEY", None)
    try:
        from unpod import Client

        with pytest.raises(ValueError, match="api_key"):
            Client()
    finally:
        if env_key:
            os.environ["UNPOD_API_KEY"] = env_key
