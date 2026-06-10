"""Supervoice URL resolution for the playground harness.

A fresh clone must be runnable independently. The harness resolves its speech
backend the same way the rest of the SDK does (``_base_url.ws_base``):

1. an explicit ``SUPERVOICE_URL`` always wins;
2. else ``UNPOD_BASE_URL`` derives the hosted ``wss://<host>`` speech URL — so a
   fresh clone needs no local supervoice;
3. else fall back to the local dev service on ``ws://127.0.0.1:9000``.
"""

from __future__ import annotations

import pytest
from playground.harness.api import build_app


def _supervoice_url(monkeypatch: pytest.MonkeyPatch, **env: str) -> str:
    """Build the app under a clean env and return the resolved speech URL."""
    for key in ("SUPERVOICE_URL", "UNPOD_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return build_app().state.supervoice_url


def test_hosted_default_derives_wss_from_unpod_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """UNPOD_BASE_URL alone → hosted wss://<host> (no local supervoice needed)."""
    assert (
        _supervoice_url(monkeypatch, UNPOD_BASE_URL="api.unpod.ai")
        == "wss://api.unpod.ai"
    )


def test_explicit_supervoice_url_overrides_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit SUPERVOICE_URL wins over the UNPOD_BASE_URL derivation."""
    assert (
        _supervoice_url(
            monkeypatch,
            SUPERVOICE_URL="ws://127.0.0.1:9000",
            UNPOD_BASE_URL="api.unpod.ai",
        )
        == "ws://127.0.0.1:9000"
    )


def test_localhost_fallback_when_nothing_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With neither var set, fall back to the local dev speech service."""
    assert _supervoice_url(monkeypatch) == "ws://127.0.0.1:9000"


def test_insecure_base_url_stays_ws(monkeypatch: pytest.MonkeyPatch) -> None:
    """A ws:// base stays insecure (local-dev convenience), not forced to wss."""
    assert (
        _supervoice_url(monkeypatch, UNPOD_BASE_URL="ws://localhost:8000")
        == "ws://localhost:8000"
    )
