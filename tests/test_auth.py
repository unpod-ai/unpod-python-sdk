"""Tests for dual-mode auth (direct Bearer vs proxy JWT + Org-Handle)."""

import pytest

from unpod import AsyncClient, BearerAuth, JWTAuth


def test_bearer_auth_headers():
    assert BearerAuth("sk_x").headers() == {"Authorization": "Bearer sk_x"}


def test_jwt_auth_headers_with_org_handle():
    assert JWTAuth("tok", org_handle="acme").headers() == {
        "Authorization": "JWT tok",
        "Org-Handle": "acme",
    }


def test_jwt_auth_headers_without_org_handle():
    headers = JWTAuth("tok").headers()
    assert headers == {"Authorization": "JWT tok"}
    assert "Org-Handle" not in headers


def test_client_default_auth_is_bearer():
    client = AsyncClient(api_key="sk_x", base_url="https://api.example.test")
    headers = client._http._headers()
    assert headers["Authorization"] == "Bearer sk_x"


def test_client_proxy_mode_sends_jwt_and_org_handle():
    client = AsyncClient(
        base_url="https://app.example.test/api/v2/platform/speech",
        auth=JWTAuth(token="jwt_abc", org_handle="acme"),
    )
    headers = client._http._headers()
    assert headers["Authorization"] == "JWT jwt_abc"
    assert headers["Org-Handle"] == "acme"
    # The same auth is used for the (unused-in-proxy) orchestrator client too.
    assert client._orch_http._headers()["Authorization"] == "JWT jwt_abc"


def test_client_requires_auth_or_api_key(monkeypatch):
    monkeypatch.delenv("UNPOD_API_KEY", raising=False)
    with pytest.raises(ValueError):
        AsyncClient(base_url="https://api.example.test")
