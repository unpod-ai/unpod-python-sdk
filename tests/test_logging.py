"""SDK logging: namespacing, library hygiene, redaction, and key call sites.

The SDK used to be silent — a failed dial or a refused credential produced no
output at all, which is what made the QA bridge failure undebuggable. These
tests pin the contract: logs exist, they carry the correlating ids, and secrets
never reach them.
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from unpod._logging import close_code_of, enable_logging, get_logger, redact_url
from unpod._protocol import JobAssign, Register, Registered
from unpod.connectivity.runner import AgentRunner, RunnerAuthError


# -- library hygiene --------------------------------------------------------


def test_package_logger_has_null_handler() -> None:
    """A library must not print unless the host app opts in."""
    root = logging.getLogger("unpod")
    assert any(isinstance(h, logging.NullHandler) for h in root.handlers)


def test_loggers_are_namespaced() -> None:
    assert get_logger("runner").name == "unpod.runner"


def test_enable_logging_is_idempotent() -> None:
    root = logging.getLogger("unpod")
    before = len(root.handlers)
    enable_logging(logging.DEBUG)
    enable_logging(logging.DEBUG)
    added = len(root.handlers) - before
    assert added == 1
    # Clean up so the handler does not leak into other tests.
    for h in list(root.handlers):
        if getattr(h, "_unpod_handler", False):
            root.removeHandler(h)
    root.propagate = True


# -- redaction --------------------------------------------------------------


def test_redact_url_hides_call_token() -> None:
    out = redact_url("wss://w.example/call/s-1?token=supersecret&x=1")
    assert "supersecret" not in out
    assert "token=***" in out
    assert "wss://w.example/call/s-1" in out
    assert "x=1" in out


def test_redact_url_passes_through_plain_urls() -> None:
    assert redact_url("wss://w.example/call/s-1") == "wss://w.example/call/s-1"


def test_redact_url_never_raises() -> None:
    assert redact_url("::::not a url::::")  # returns something, does not raise


def test_close_code_extracted_from_websockets_style_exception() -> None:
    class _Frame:
        code = 4003

    class _Closed(Exception):
        rcvd = _Frame()

    assert close_code_of(_Closed()) == 4003
    assert close_code_of(ValueError("plain")) is None


# -- call sites -------------------------------------------------------------


def _runner(**kw):
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    return AgentRunner(entrypoint=entrypoint, agent_id="bot", api_key="k", **kw)


class _FakeWS:
    def __init__(self, inbound: list[str]) -> None:
        self._queue: asyncio.Queue[str | None] = asyncio.Queue()
        for f in inbound:
            self._queue.put_nowait(f)
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        self.sent.append(json.loads(raw))

    async def recv(self) -> str:
        item = await self._queue.get()
        if item is None:
            raise ConnectionError("closed")
        return item

    def close(self) -> None:
        self._queue.put_nowait(None)


@pytest.mark.anyio
async def test_assign_logs_job_and_redacted_bridge(caplog) -> None:
    r = _runner()
    ws = _FakeWS([])
    assign = JobAssign(
        job_id="j-42",
        call_id="s-42",
        agent_id="bot",
        bridge_url="wss://w.example/call/s-42",
        call_token="tok-secret",
    )
    with caplog.at_level(logging.INFO, logger="unpod.runner"):
        await r._handle_assign(ws, assign)

    line = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "job.assign" in line
    assert "job_id=j-42" in line
    assert "accepted" in line
    assert "tok-secret" not in line


@pytest.mark.anyio
async def test_rejected_assign_logs_the_reason(caplog) -> None:
    r = _runner()
    ws = _FakeWS([])
    assign = JobAssign(
        job_id="j-1",
        call_id="s-1",
        agent_id="someone-else",  # mismatch
        bridge_url="wss://w.example/call/s-1",
        call_token="t",
    )
    with caplog.at_level(logging.INFO, logger="unpod.runner"):
        await r._handle_assign(ws, assign)

    assert "agent_id_mismatch" in "\n".join(r.getMessage() for r in caplog.records)
    assert ws.sent[0]["accepted"] is False


@pytest.mark.anyio
async def test_registration_logs_worker_and_transport(caplog) -> None:
    r = _runner()
    registered = Registered(heartbeat_interval_s=30, transport_ack="dial_out")
    ws = _FakeWS([registered.model_dump_json()])
    ws.close()  # ends the recv loop right after registration

    with caplog.at_level(logging.INFO, logger="unpod.runner"):
        with pytest.raises(ConnectionError):
            await r._control_session(ws)

    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "control: register" in text
    assert "control: registered" in text
    assert "transport_ack=dial_out" in text
    sent = Register.model_validate(ws.sent[0])
    assert sent.capabilities["kind"] == "brain"


@pytest.mark.anyio
async def test_unexpected_first_frame_is_logged_as_error(caplog) -> None:
    """A refused register used to raise with nothing explaining why."""
    r = _runner()
    ws = _FakeWS([json.dumps({"type": "heartbeat", "active_jobs": 0})])

    with caplog.at_level(logging.ERROR, logger="unpod.runner"):
        with pytest.raises(ConnectionError):
            await r._control_session(ws)

    assert "expected 'registered'" in "\n".join(
        rec.getMessage() for rec in caplog.records
    )


@pytest.mark.anyio
async def test_hook_failure_is_attributed_then_reraised(caplog) -> None:
    """A raising hook killed the call with no clue which hook it was."""
    from unpod.connectivity.hooks import HookRegistry

    hooks = HookRegistry()

    @hooks.on("call_start")
    async def exploding_hook() -> None:
        raise RuntimeError("boom")

    with caplog.at_level(logging.ERROR, logger="unpod.hooks"):
        with pytest.raises(RuntimeError):
            await hooks.fire("call_start")

    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "exploding_hook" in text
    assert "call_start" in text


def test_bridge_auth_logs_each_rejection_reason(caplog) -> None:
    from unpod.connectivity.bridge_auth import verify_signed_url

    seen: set[str] = set()
    kw = dict(
        secret="s",
        session_id="s-1",
        job_id="j-1",
        seen_nonces=seen,
        now_ms=1_000_000,
    )
    with caplog.at_level(logging.WARNING, logger="unpod.bridge.auth"):
        # Stale timestamp.
        assert not verify_signed_url(nonce="n1", ts=1, signature="x", **kw)
        # Bad signature (fresh ts).
        assert not verify_signed_url(
            nonce="n2", ts=1_000_000, signature="wrong", **kw
        )
        # Replay.
        seen.add("n3")
        assert not verify_signed_url(nonce="n3", ts=1_000_000, signature="x", **kw)

    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "STALE" in text
    assert "BAD SIGNATURE" in text
    assert "REPLAY" in text
    assert "wrong" not in text  # the presented signature is never logged


def test_http_error_logs_status_and_body(caplog) -> None:
    from unpod.management._http import AsyncHTTPClient

    class _Req:
        method = "GET"
        url = "https://api.unpod.ai/v1/pipes"

    class _Resp:
        status_code = 422
        request = _Req()
        text = '{"detail":"voice_profile_not_found: \'nope\'"}'

    with caplog.at_level(logging.WARNING, logger="unpod.http"):
        AsyncHTTPClient._log_response(_Resp())

    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "422" in text
    assert "voice_profile_not_found" in text


@pytest.mark.anyio
async def test_session_loop_logs_turns_and_outcome(caplog) -> None:
    """The in-call runtime used to be entirely silent."""
    from unpod._protocol import UserTextEvent
    from unpod.connectivity.session import Session

    events = [UserTextEvent(text="hello there")]

    class _Bridge:
        def __init__(self) -> None:
            self.sent: list[object] = []

        async def send_verb(self, verb: object) -> None:
            self.sent.append(verb)

        async def recv_event(self):  # type: ignore[no-untyped-def]
            if events:
                return events.pop(0)
            raise ConnectionError("bridge closed")

    session = Session(bridge=_Bridge())

    with caplog.at_level(logging.INFO, logger="unpod.session"):
        await session.run()

    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "session loop start" in text
    assert "turn 1 user: 'hello there'" in text
    # No dialog adapter attached ⇒ the user is talking to nothing. Say so.
    assert "no dialog adapter attached" in text
    assert "bridge closed" in text
    assert "outcome=hangup" in text
    assert "turns=1" in text


@pytest.mark.anyio
async def test_session_verbs_are_logged(caplog) -> None:
    from unpod.connectivity.session import Session

    class _Bridge:
        async def send_verb(self, verb: object) -> None:
            pass

    session = Session(bridge=_Bridge())
    with caplog.at_level(logging.INFO, logger="unpod.session"):
        await session.say("hi")
        await session.transfer("+15551234567", transfer_type="human", mode="warm")
        await session.end("completed")

    text = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "verb agent.say" in text
    assert "verb agent.transfer type=human mode=warm target=+15551234567" in text
    assert "verb agent.end_call reason=completed" in text


def test_http_logging_never_raises_on_a_test_double() -> None:
    from unittest.mock import Mock

    from unpod.management._http import AsyncHTTPClient

    AsyncHTTPClient._log_response(Mock())  # must not raise
    AsyncHTTPClient._log_response(object())


@pytest.mark.anyio
async def test_auth_close_is_fatal_and_logged(monkeypatch, caplog) -> None:
    """A bad API key must not loop forever in silence."""

    class _Frame:
        code = 4001

    class _Refused(Exception):
        rcvd = _Frame()

    def _boom(*a, **kw):
        raise _Refused()

    import websockets

    monkeypatch.setattr(websockets, "connect", _boom)
    r = _runner()

    with caplog.at_level(logging.ERROR, logger="unpod.runner"):
        with pytest.raises(RunnerAuthError):
            await r.run()

    assert "UNPOD_API_KEY" in "\n".join(rec.getMessage() for rec in caplog.records)
