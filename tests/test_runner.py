"""Tests for AgentRunner."""

import asyncio
import os

import pytest
from unpod.connectivity.runner import AgentRunner


def _bridge_inbound() -> list[str]:
    """Scripted hello.ack + call.started frames for a single call (s1)."""
    return [
        '{"event":"hello.ack","protocol_version":2,"negotiated_events":[],'
        '"negotiated_verbs":[],"call_id":"s1","session_id":"s1",'
        '"job_id":"j1","room_id":"s1"}',
        '{"event":"call.started","session_id":"s1","job_id":"j1",'
        '"room_id":"s1","metadata":{}}',
    ]


class _FakeWS:
    """In-memory duplex websocket stand-in scripted with inbound frames."""

    def __init__(self, inbound: list[str]) -> None:
        self._inbound = inbound
        self.sent: list[str] = []
        self.closed = False

    async def send(self, raw: str) -> None:
        self.sent.append(raw)

    async def recv(self) -> str:
        return self._inbound.pop(0)

    async def close(self) -> None:
        self.closed = True


def test_runner_init() -> None:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    runner = AgentRunner(
        entrypoint=entrypoint,
        agent_id="kyc-bot",
        api_key="unpod_sk_test",
        max_concurrent_calls=50,
    )
    assert runner._agent_id == "kyc-bot"
    assert runner._max_concurrent == 50


def test_runner_init_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNPOD_API_KEY", "unpod_sk_env")

    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    runner = AgentRunner(entrypoint=entrypoint, agent_id="bot")
    assert runner._api_key == "unpod_sk_env"


def test_runner_missing_key_raises() -> None:
    env_key = os.environ.pop("UNPOD_API_KEY", None)
    try:

        async def entrypoint(ctx):  # type: ignore[no-untyped-def]
            pass

        with pytest.raises(ValueError, match="api_key"):
            AgentRunner(entrypoint=entrypoint, agent_id="bot")
    finally:
        if env_key:
            os.environ["UNPOD_API_KEY"] = env_key


def test_runner_stats_initial() -> None:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    runner = AgentRunner(
        entrypoint=entrypoint,
        agent_id="kyc-bot",
        api_key="unpod_sk_test",
    )
    stats = runner.stats()
    assert stats.in_flight == 0
    assert stats.capacity == 50


def test_runner_active_calls_empty_before_any_connection() -> None:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    runner = AgentRunner(
        entrypoint=entrypoint,
        agent_id="kyc-bot",
        api_key="unpod_sk_test",
    )
    assert runner.active_calls() == []


@pytest.mark.anyio
async def test_runner_call_hooks_fire_on_real_call() -> None:
    """call_start and call_end hooks fire on a real bridge connection,
    with final_state="ended" for a normal entrypoint."""

    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    runner = AgentRunner(
        entrypoint=entrypoint,
        agent_id="kyc-bot",
        api_key="unpod_sk_test",
    )
    starts: list[object] = []
    ends: list[tuple[object, str]] = []

    @runner.on("call_start")
    async def _on_start(ctx: object) -> None:
        starts.append(ctx)

    @runner.on("call_end")
    async def _on_end(ctx: object, final_state: str) -> None:
        ends.append((ctx, final_state))

    await runner._bridge_handler(_FakeWS(_bridge_inbound()))

    assert len(starts) == 1
    assert len(ends) == 1
    assert ends[0][1] == "ended"
    assert starts[0] is ends[0][0]


@pytest.mark.anyio
async def test_runner_call_end_hook_final_state_failed_on_raise() -> None:
    """When the entrypoint raises, call_end still fires with
    final_state="failed" and the call counts as failed, not completed."""

    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        raise RuntimeError("boom")

    runner = AgentRunner(
        entrypoint=entrypoint,
        agent_id="kyc-bot",
        api_key="unpod_sk_test",
    )
    ends: list[tuple[object, str]] = []

    @runner.on("call_end")
    async def _on_end(ctx: object, final_state: str) -> None:
        ends.append((ctx, final_state))

    await runner._bridge_handler(_FakeWS(_bridge_inbound()))

    assert len(ends) == 1
    assert ends[0][1] == "failed"
    assert runner._failed_count == 1
    assert runner._completed_count == 0
    assert runner.active_calls() == []


def test_runner_default_params() -> None:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    runner = AgentRunner(
        entrypoint=entrypoint,
        agent_id="bot",
        api_key="unpod_sk_test",
    )
    assert runner._max_concurrent == 50
    assert runner._permits_per_minute == 120
    assert runner._drain_timeout_s == 60
    assert runner._dev_mode is False


def test_runner_custom_params() -> None:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    runner = AgentRunner(
        entrypoint=entrypoint,
        agent_id="bot",
        api_key="unpod_sk_test",
        max_concurrent_calls=100,
        permits_per_minute=200,
        drain_timeout_s=30,
        dev_mode=True,
    )
    assert runner._max_concurrent == 100
    assert runner._permits_per_minute == 200
    assert runner._drain_timeout_s == 30
    assert runner._dev_mode is True


def test_runner_worker_id_is_unique_per_instance() -> None:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    r1 = AgentRunner(entrypoint=entrypoint, agent_id="ag-support", api_key="k")
    r2 = AgentRunner(entrypoint=entrypoint, agent_id="ag-support", api_key="k")
    assert r1._worker_id != r2._worker_id
    assert r1._worker_id.startswith("ag-support#")
    assert r1._agent_id == r2._agent_id == "ag-support"


def test_runner_pool_and_serving_url() -> None:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    r = AgentRunner(
        entrypoint=entrypoint,
        agent_id="ag-support",
        api_key="k",
        serving_url="wss://agents.acme.com/support",
    )
    assert r._pool == "ag-support"
    assert r._serving_url == "wss://agents.acme.com/support"


def test_runner_dev_mode_isolates_pool() -> None:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    r = AgentRunner(
        entrypoint=entrypoint,
        agent_id="ag-support",
        api_key="k",
        serving_url="ws://localhost:8765/agent",
        dev_mode=True,
    )
    assert r._pool == "ag-support@dev"


def test_runner_serving_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    monkeypatch.setenv("UNPOD_RUNNER_URL", "wss://from-env/agent")
    r = AgentRunner(entrypoint=entrypoint, agent_id="ag-support", api_key="k")
    assert r._serving_url == "wss://from-env/agent"


def test_runner_register_capabilities_carry_agent_id_and_serving_url() -> None:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    r = AgentRunner(
        entrypoint=entrypoint,
        agent_id="ag-support",
        api_key="k",
        serving_url="wss://r1/agent",
    )
    # The values that run() puts into Register(...):
    assert r._pool == "ag-support"
    assert r._agent_id == "ag-support"
    assert r._serving_url == "wss://r1/agent"


# ---------------------------------------------------------------------------
# Server model: runner SERVES a per-call bridge (no per-call dispatch loop).
# ---------------------------------------------------------------------------


def test_runner_no_longer_has_dispatch_loop() -> None:
    """The dispatch-loop / dial-out path is gone in the server model."""

    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    r = AgentRunner(entrypoint=entrypoint, agent_id="bot", api_key="k")
    assert not hasattr(r, "_dispatch_loop")
    assert not hasattr(r, "_handle_dispatch")
    assert not hasattr(r, "_context_from_dispatch")


def test_runner_serving_host_port_defaults() -> None:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    r = AgentRunner(entrypoint=entrypoint, agent_id="bot", api_key="k")
    assert r._serving_host_port() == ("0.0.0.0", 8765)


def test_runner_serving_host_port_parsed_from_url() -> None:
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    r = AgentRunner(
        entrypoint=entrypoint,
        agent_id="bot",
        api_key="k",
        serving_url="ws://0.0.0.0:9000/agent",
    )
    assert r._serving_host_port() == ("0.0.0.0", 9000)


def test_runner_serving_host_port_literal_ip_preserves_bind() -> None:
    """A literal IP is bound as-is (loopback stays loopback-only)."""

    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    r = AgentRunner(
        entrypoint=entrypoint,
        agent_id="bot",
        api_key="k",
        serving_url="ws://127.0.0.1:8765",
    )
    assert r._serving_host_port() == ("127.0.0.1", 8765)


def test_runner_serving_host_port_hostname_binds_all_interfaces() -> None:
    """A hostname (e.g. a Docker/K8s service name) can't be bound directly, so
    the bridge binds all interfaces while still ADVERTISING that hostname — this
    is what makes the speech container's dial-back reach the harness in compose."""

    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    r = AgentRunner(
        entrypoint=entrypoint,
        agent_id="bot",
        api_key="k",
        serving_url="ws://supervoice-playground:8765",
    )
    # Bind: all interfaces (a hostname is not a bindable local address).
    assert r._serving_host_port() == ("0.0.0.0", 8765)
    # Advertise: the hostname is preserved for the Register frame / relay.
    assert r._serving_url == "ws://supervoice-playground:8765"


@pytest.mark.anyio
async def test_runner_bridge_handler_runs_entrypoint_and_counts() -> None:
    """_bridge_handler runs the entrypoint and tracks the in-flight count."""
    observed: list[int] = []

    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        observed.append(ctx.session_id)  # type: ignore[arg-type]
        # in-flight count should reflect the live connection mid-call.
        observed.append(len(r._active_calls))

    r = AgentRunner(entrypoint=entrypoint, agent_id="bot", api_key="k")

    await r._bridge_handler(_FakeWS(_bridge_inbound()))

    assert "s1" in observed
    assert len(r._active_calls) == 0
    assert r._completed_count == 1
    # During the connection, the in-flight count was 1 (recorded mid-call).
    assert 1 in observed


@pytest.mark.anyio
async def test_active_calls_tracks_live_connection() -> None:
    """active_calls() exposes the live CallContext during a connection and
    is empty again after the handler returns."""
    holder: dict[str, object] = {}

    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        live = r.active_calls()
        holder["len"] = len(live)
        holder["session_ids"] = [c.session_id for c in live]
        holder["is_same_ctx"] = live[0] is ctx
        holder["in_flight"] = r.stats().in_flight

    r = AgentRunner(entrypoint=entrypoint, agent_id="bot", api_key="k")

    # Empty before the connection.
    assert r.active_calls() == []

    await r._bridge_handler(_FakeWS(_bridge_inbound()))

    # Captured while the entrypoint was running.
    assert holder["len"] == 1
    assert holder["session_ids"] == ["s1"]
    assert holder["is_same_ctx"] is True
    # stats().in_flight is consistent with active_calls() len mid-call.
    assert holder["in_flight"] == 1

    # Empty again after the handler returns.
    assert r.active_calls() == []
    assert r.stats().in_flight == 0


@pytest.mark.anyio
async def test_runner_heartbeat_reports_active_connections() -> None:
    """Heartbeat.active_jobs reflects live bridge connections."""
    from unpod._protocol import Heartbeat, parse_dispatch_frame

    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    r = AgentRunner(entrypoint=entrypoint, agent_id="bot", api_key="k")
    r._active_calls = {
        "a": object(),  # type: ignore[dict-item]
        "b": object(),  # type: ignore[dict-item]
        "c": object(),  # type: ignore[dict-item]
    }

    sent: list[str] = []

    class _FakeWS:
        async def send(self, raw: str) -> None:
            sent.append(raw)
            r._shutting_down = True

    await r._heartbeat_loop(_FakeWS(), interval_s=0)

    frame = parse_dispatch_frame(sent[0])
    assert isinstance(frame, Heartbeat)
    assert frame.active_jobs == 3


# ---------------------------------------------------------------------------
# Accounting: duration, completed/failed, and non-call connections.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_runner_mean_duration_positive_after_completed_call() -> None:
    """A completed call accumulates real duration; mean is > 0."""

    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        await asyncio.sleep(0.001)

    r = AgentRunner(entrypoint=entrypoint, agent_id="bot", api_key="k")

    await r._bridge_handler(_FakeWS(_bridge_inbound()))

    assert r._completed_count == 1
    assert r._total_duration > 0
    assert r.stats().mean_call_duration_s > 0


@pytest.mark.anyio
async def test_runner_verify_false_counts_as_neither() -> None:
    """A rejected connection builds no context: neither completed nor
    failed, the ws is closed, and the entrypoint never runs."""
    ran = {"entrypoint": False}

    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        ran["entrypoint"] = True

    r = AgentRunner(entrypoint=entrypoint, agent_id="bot", api_key="k")
    r._verify = lambda _ws: False

    ws = _FakeWS(_bridge_inbound())
    await r._bridge_handler(ws)

    assert ran["entrypoint"] is False
    assert r._completed_count == 0
    assert r._failed_count == 0
    assert ws.closed is True
    assert r.active_calls() == []


@pytest.mark.anyio
async def test_runner_malformed_handshake_counts_as_neither() -> None:
    """A wrong event before call.started aborts before any context is
    built: neither completed nor failed, ws closed, entrypoint not run."""
    ran = {"entrypoint": False}

    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        ran["entrypoint"] = True

    r = AgentRunner(entrypoint=entrypoint, agent_id="bot", api_key="k")

    # hello.ack arrives, then a bogus event instead of call.started.
    ws = _FakeWS(
        [
            '{"event":"hello.ack","protocol_version":2,"negotiated_events":[],'
            '"negotiated_verbs":[],"call_id":"s1","session_id":"s1",'
            '"job_id":"j1","room_id":"s1"}',
            '{"event":"user.text","text":"hi"}',
        ]
    )
    await r._bridge_handler(ws)

    assert ran["entrypoint"] is False
    assert r._completed_count == 0
    assert r._failed_count == 0
    assert ws.closed is True
    assert r.active_calls() == []


@pytest.mark.anyio
async def test_runner_handshake_timeout_does_not_hang() -> None:
    """A client that never finishes the handshake is dropped on timeout
    rather than tying up the connection forever."""
    import unpod.connectivity.bridge_server as bs

    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    r = AgentRunner(entrypoint=entrypoint, agent_id="bot", api_key="k")

    class _HangingWS(_FakeWS):
        async def recv(self) -> str:
            # First recv (hello.ack) works; second blocks forever.
            if self._inbound:
                return self._inbound.pop(0)
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    ws = _HangingWS(
        [
            '{"event":"hello.ack","protocol_version":2,"negotiated_events":[],'
            '"negotiated_verbs":[],"call_id":"s1","session_id":"s1",'
            '"job_id":"j1","room_id":"s1"}',
        ]
    )

    # Short timeout via the bridge-server constant so the test is fast.
    async with asyncio.timeout(2.0):
        await bs.handle_bridge_connection(
            ws,
            entrypoint=r._entrypoint,
            agent_id=r._agent_id,
            handshake_timeout_s=0.05,
        )

    assert ws.closed is True
    assert r._completed_count == 0
    assert r._failed_count == 0


# ── HMAC enforcement on the bridge accept path (Task 14) ──


def _make_runner(**kwargs):  # type: ignore[no-untyped-def]
    async def entrypoint(ctx):  # type: ignore[no-untyped-def]
        pass

    return AgentRunner(entrypoint=entrypoint, agent_id="bot", api_key="k", **kwargs)


def test_runner_no_secret_means_no_verify() -> None:
    """With no agent_secret configured, inbound connections aren't gated."""
    r = _make_runner()
    assert r._verify is None


def test_runner_agent_secret_builds_verifier() -> None:
    r = _make_runner(agent_secret="sek")
    assert r._verify is not None


def test_runner_agent_secret_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNPOD_AGENT_SECRET", "env-secret")
    r = _make_runner()
    assert r._verify is not None


def test_runner_verifier_rejects_unsigned_connection() -> None:
    r = _make_runner(agent_secret="sek")
    assert r._verify is not None

    class _Req:
        path = "/agent"  # no signature params

    class _Conn:
        request = _Req()

    assert r._verify(_Conn()) is False


def test_runner_verifier_accepts_valid_signed_connection() -> None:
    import base64
    import hashlib
    import hmac
    import time
    from urllib.parse import urlencode

    secret = "sek"
    ts = int(time.time() * 1000)
    msg = f"s1|j1|n1|{ts}"
    sig = base64.b64encode(
        hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()
    query = urlencode(
        {
            "session_id": "s1",
            "job_id": "j1",
            "nonce": "n1",
            "ts": ts,
            "signature": sig,
        }
    )

    class _Req:
        path = f"/agent?{query}"

    class _Conn:
        request = _Req()

    r = _make_runner(agent_secret=secret)
    assert r._verify is not None
    conn = _Conn()
    assert r._verify(conn) is True
    # Replay of the same nonce against the same runner is rejected.
    assert r._verify(_Conn()) is False
