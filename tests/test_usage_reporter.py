"""Tests for the SDK usage egress (playground-usage-ledger P1, 1.9-1.10)."""

from __future__ import annotations

import httpx

from unpod.connectivity.usage import UsageReporter, _split_model_uri

_URL = "https://platform.unpod.test"


def _reporter(handler, session_id="s1", **kw) -> UsageReporter:
    return UsageReporter(
        session_id,
        ingest_url=_URL,
        token="tok",
        transport=httpx.MockTransport(handler),
        **kw,
    )


# ── _split_model_uri ──────────────────────────────────────


def test_split_model_uri():
    assert _split_model_uri("openai/gpt-4o-mini") == ("openai", "gpt-4o-mini")
    assert _split_model_uri("custom/lk-inference/openai/gpt-4o") == (
        "openai",
        "gpt-4o",
    )
    assert _split_model_uri("gpt-4o-mini") == ("", "gpt-4o-mini")
    assert _split_model_uri("") == ("", "")


# ── flush ─────────────────────────────────────────────────


async def test_flush_posts_counters_providers_no_identity():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["auth"] = request.headers.get("Authorization")
        return httpx.Response(200, json={"ok": True, "applied": True})

    r = _reporter(handler)
    r.record_llm(tokens_in=100, tokens_out=40, model="openai/gpt-4o-mini")
    pushed = await r.flush()

    assert pushed is True
    assert seen["url"].endswith("/v1/internal/sessions/s1/usage")
    assert seen["auth"] == "Bearer tok"
    body = seen["body"]
    assert body["source"] == "sdk"
    assert body["seq"] == 0
    assert body["counters"] == {
        "llm_prompt_tokens": 100,
        "llm_completion_tokens": 40,
    }
    assert body["providers"] == {"llm_provider": "openai", "llm_model": "gpt-4o-mini"}
    # SECURITY: no billing identity is ever sent by the producer.
    assert "org_id" not in body
    assert "byok" not in body
    assert "is_dev" not in body


async def test_flush_resets_counters_and_increments_seq():
    seqs = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json

        seqs.append(json.loads(request.content)["seq"])
        return httpx.Response(200, json={"ok": True})

    r = _reporter(handler)
    r.record_llm(tokens_in=10, model="openai/gpt-4o")
    assert await r.flush() is True
    # Nothing new accumulated → second flush is a no-op (delta semantics).
    assert await r.flush() is False
    r.record_llm(tokens_out=5, model="openai/gpt-4o")
    assert await r.flush() is True
    assert seqs == [0, 1]  # distinct seqs for distinct deltas


async def test_unconfigured_is_noop():
    r = UsageReporter("s1", ingest_url="")  # no ingest URL
    r.record_llm(tokens_in=100, model="openai/gpt-4o")
    assert r.configured is False
    assert await r.flush() is False


async def test_no_usage_is_noop():
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not POST when nothing accumulated")

    r = _reporter(handler)
    assert await r.flush() is False


async def test_flush_never_raises_on_http_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "boom"})

    r = _reporter(handler)
    r.record_llm(tokens_in=10, model="openai/gpt-4o")
    # Best-effort: a failing ingest must not raise into the call.
    assert await r.flush() is False


# ── Session wiring ────────────────────────────────────────


from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

from unpod.adapters.base import DialogAdapter  # noqa: E402
from unpod.connectivity.session import Session  # noqa: E402


class _FakeAdapter(DialogAdapter):
    """Minimal explicit DialogAdapter so the session setter wires the callback."""

    def __init__(self) -> None:
        self.cb = None

    async def turn(self, text, context=None):
        return ""

    async def stream(self, text, context=None):
        if False:  # pragma: no cover — never yields
            yield ""

    def assist(self, text):
        pass

    def register_llm_callback(self, cb):
        self.cb = cb


async def test_session_llm_callback_accumulates_usage():
    bridge = AsyncMock()
    bridge.session_id = "sess_1"
    session = Session(bridge=bridge)
    adapter = _FakeAdapter()
    session.dialog_machine = adapter  # setter registers the llm callback

    assert adapter.cb is not None
    await adapter.cb(
        SimpleNamespace(tokens_in=120, tokens_out=30, model="openai/gpt-4o-mini")
    )
    # The session's usage reporter accumulated the tokens.
    assert session._usage._counters() == {
        "llm_prompt_tokens": 120,
        "llm_completion_tokens": 30,
    }


async def test_session_flushes_usage_on_call_end():
    bridge = AsyncMock()
    bridge.session_id = "sess_1"
    bridge.recv_event = AsyncMock(side_effect=RuntimeError("bridge closed"))
    session = Session(bridge=bridge)
    session._usage = AsyncMock()  # spy on flush

    await session.run()

    session._usage.flush.assert_awaited_once()
