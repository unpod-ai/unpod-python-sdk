"""Tests for the unified UNPOD_BASE_URL configuration."""

from __future__ import annotations

import pytest
from unpod import _base_url


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "UNPOD_BASE_URL",
        "UNPOD_SERVICE_BASE_URL",
        "UNPOD_ORCHESTRATOR_BASE_URL",
        "UNPOD_ORCHESTRATOR_URL",
    ):
        monkeypatch.delenv(var, raising=False)


def test_unset_returns_none() -> None:
    assert _base_url.host() is None
    assert _base_url.http_base() is None
    assert _base_url.ws_base() is None
    assert _base_url.service_base() is None


def test_empty_or_blank_treated_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNPOD_BASE_URL", "   ")
    assert _base_url.host() is None


def test_bare_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNPOD_BASE_URL", "api.unpod.ai")
    assert _base_url.http_base() == "https://api.unpod.ai"
    assert _base_url.ws_base() == "wss://api.unpod.ai"
    assert _base_url.service_base() == "https://api.unpod.ai/platform"


def test_https_url_with_trailing_slash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNPOD_BASE_URL", "https://api.unpod.ai/")
    assert _base_url.http_base() == "https://api.unpod.ai"
    assert _base_url.ws_base() == "wss://api.unpod.ai"


def test_insecure_http_stays_insecure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNPOD_BASE_URL", "http://127.0.0.1:8000")
    assert _base_url.http_base() == "http://127.0.0.1:8000"
    assert _base_url.ws_base() == "ws://127.0.0.1:8000"
    assert _base_url.service_base() == "http://127.0.0.1:8000/platform"


def test_ws_scheme_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNPOD_BASE_URL", "wss://api.unpod.ai")
    assert _base_url.ws_base() == "wss://api.unpod.ai"
    assert _base_url.http_base() == "https://api.unpod.ai"


def test_client_derives_service_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unpod import AsyncClient

    monkeypatch.setenv("UNPOD_BASE_URL", "example.dev")
    client = AsyncClient(api_key="k")
    assert client._base_url == "https://example.dev/platform"


def test_client_service_override_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unpod import AsyncClient

    monkeypatch.setenv("UNPOD_BASE_URL", "example.dev")
    monkeypatch.setenv("UNPOD_SERVICE_BASE_URL", "https://other.dev/platform")
    client = AsyncClient(api_key="k")
    assert client._base_url == "https://other.dev/platform"


def test_client_default_without_any_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unpod import AsyncClient

    client = AsyncClient(api_key="k")
    assert client._base_url == "https://api.unpod.ai/platform"


def test_auth_base_derives_bare_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNPOD_BASE_URL", "api.unpod.ai")
    assert _base_url.auth_base() == "https://api.unpod.ai"


def test_auth_base_respects_insecure_scheme(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNPOD_BASE_URL", "http://localhost:8000")
    assert _base_url.auth_base() == "http://localhost:8000"


def test_auth_base_none_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("UNPOD_BASE_URL", raising=False)
    assert _base_url.auth_base() is None


async def _noop(ctx: object) -> None:  # pragma: no cover - never called
    return None


def test_runner_derives_ws_base(monkeypatch: pytest.MonkeyPatch) -> None:
    from unpod import AgentRunner

    monkeypatch.setenv("UNPOD_BASE_URL", "example.dev")
    runner = AgentRunner(entrypoint=_noop, agent_id="a", api_key="k")
    assert runner._orchestrator_url.startswith("wss://example.dev")


def test_runner_orchestrator_override_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unpod import AgentRunner

    monkeypatch.setenv("UNPOD_BASE_URL", "example.dev")
    monkeypatch.setenv("UNPOD_ORCHESTRATOR_URL", "wss://other.dev")
    runner = AgentRunner(entrypoint=_noop, agent_id="a", api_key="k")
    assert runner._orchestrator_url.startswith("wss://other.dev")


def test_runner_default_without_any_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unpod import AgentRunner

    runner = AgentRunner(entrypoint=_noop, agent_id="a", api_key="k")
    assert runner._orchestrator_url.startswith("wss://api.unpod.ai")
