"""AgentRunner registers with the platform token, not a supervoice API key.

The orchestrator verifies ``Authorization: Token`` + ``Org-Handle`` by asking
Django, so a developer sets the one credential they already have. Both are
required: without the org handle the token cannot be tied to an org.
"""

from __future__ import annotations

import pytest

from unpod.connectivity.runner import AgentRunner


async def _noop(_ctx) -> None:
    return None


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("UNPOD_PLATFORM_TOKEN", "UNPOD_ORG_HANDLE", "UNPOD_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_it_sends_the_token_and_org_handle() -> None:
    runner = AgentRunner(
        entrypoint=_noop, agent_id="bot", platform_token="tok", org_handle="recalll.co"
    )

    assert runner._auth_headers() == {
        "Authorization": "Token tok",
        "Org-Handle": "recalll.co",
    }


def test_both_are_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("UNPOD_PLATFORM_TOKEN", "env-tok")
    monkeypatch.setenv("UNPOD_ORG_HANDLE", "recalll.co")

    runner = AgentRunner(entrypoint=_noop, agent_id="bot")

    assert runner._auth_headers() == {
        "Authorization": "Token env-tok",
        "Org-Handle": "recalll.co",
    }


def test_a_missing_token_fails_at_construction(monkeypatch) -> None:
    monkeypatch.setenv("UNPOD_ORG_HANDLE", "recalll.co")

    with pytest.raises(ValueError, match="UNPOD_PLATFORM_TOKEN"):
        AgentRunner(entrypoint=_noop, agent_id="bot")


def test_a_missing_org_handle_fails_at_construction(monkeypatch) -> None:
    monkeypatch.setenv("UNPOD_PLATFORM_TOKEN", "tok")

    with pytest.raises(ValueError, match="UNPOD_ORG_HANDLE"):
        AgentRunner(entrypoint=_noop, agent_id="bot")


def test_an_api_key_alone_no_longer_runs_a_runner(monkeypatch) -> None:
    """Token-only: UNPOD_API_KEY is not a fallback any more."""
    monkeypatch.setenv("UNPOD_API_KEY", "sk_legacy")

    with pytest.raises(ValueError):
        AgentRunner(entrypoint=_noop, agent_id="bot")


def test_api_key_is_no_longer_a_parameter() -> None:
    with pytest.raises(TypeError):
        AgentRunner(  # type: ignore[call-arg]
            entrypoint=_noop,
            agent_id="bot",
            api_key="sk",
            platform_token="tok",
            org_handle="recalll.co",
        )
