"""Tests for the browser_playground example helpers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MOD_DIR = Path(__file__).resolve().parents[2] / "examples" / "browser_playground"


def _load(name: str):
    if str(_MOD_DIR) not in sys.path:
        sys.path.insert(0, str(_MOD_DIR))
    spec = importlib.util.spec_from_file_location(
        f"browser_playground.{name}", _MOD_DIR / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_audio_ws_url_from_ws() -> None:
    urls = _load("_urls")
    assert urls.audio_ws_url("ws://127.0.0.1:9000") == "ws://127.0.0.1:9000/ws/audio"


def test_audio_ws_url_from_wss() -> None:
    urls = _load("_urls")
    assert urls.audio_ws_url("wss://sv.example.com") == "wss://sv.example.com/ws/audio"


def test_audio_ws_url_strips_trailing_slash() -> None:
    urls = _load("_urls")
    assert urls.audio_ws_url("ws://h:9000/") == "ws://h:9000/ws/audio"


def test_connect_http_url_ws_to_http() -> None:
    urls = _load("_urls")
    assert (
        urls.connect_http_url("ws://127.0.0.1:9000") == "http://127.0.0.1:9000/connect"
    )


def test_connect_http_url_wss_to_https() -> None:
    urls = _load("_urls")
    assert (
        urls.connect_http_url("wss://sv.example.com")
        == "https://sv.example.com/connect"
    )


def test_pick_llm_prefers_openai(monkeypatch) -> None:
    agent = _load("agent")
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert agent._pick_llm() == "openai/gpt-4.1-mini"


def test_pick_llm_falls_back_to_anthropic(monkeypatch) -> None:
    agent = _load("agent")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "y")
    assert agent._pick_llm() == "anthropic/claude-haiku-4-5-20251001"


def test_pick_llm_raises_without_keys(monkeypatch) -> None:
    agent = _load("agent")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import pytest

    with pytest.raises(RuntimeError):
        agent._pick_llm()


def test_connect_proxy_rewrites_ws_url(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVOICE_URL", "wss://sv.example.com")
    monkeypatch.setenv("EXTERNAL_AGENT", "1")  # do not start the agent task
    run = _load("run")

    captured: dict[str, str] = {}

    class _FakeResponse:
        def json(self) -> dict[str, str]:
            return {"ws_url": "ws://localhost:9000/ws/audio"}  # supervoice's bad url

        def raise_for_status(self) -> None:
            return None

    class _FakeAsyncClient:
        def __init__(self, *a, **k) -> None: ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None:
            return None

        async def post(self, url: str, **kwargs):
            captured["url"] = url
            return _FakeResponse()

    monkeypatch.setattr(run.httpx, "AsyncClient", _FakeAsyncClient)

    from fastapi.testclient import TestClient

    with TestClient(run.build_app()) as client:
        resp = client.post("/connect", params={"agent_id": "browser-playground"})

    assert resp.status_code == 200
    # ws_url is rewritten to the configured remote supervoice, not localhost
    assert resp.json()["ws_url"] == "wss://sv.example.com/ws/audio"
    # the proxy forwarded to the supervoice /connect over https
    assert captured["url"] == "https://sv.example.com/connect"
