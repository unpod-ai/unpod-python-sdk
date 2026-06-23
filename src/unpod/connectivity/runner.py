"""AgentRunner: long-lived process that registers with the Unpod
orchestrator and SERVES a per-call bridge that media agents dial into."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import time
import uuid
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from unpod._base_url import ws_base
from unpod._protocol import Heartbeat, Register, Registered, parse_dispatch_frame
from unpod.connectivity.bridge_auth import verify_connection
from unpod.connectivity.bridge_server import handle_bridge_connection
from unpod.connectivity.call_context import CallContext
from unpod.connectivity.hooks import HookRegistry
from unpod.models.session import RunnerStats


def _is_ip_literal(host: str) -> bool:
    """True if ``host`` is a literal IPv4/IPv6 address (bindable as-is)."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class AgentRunner:
    """Long-lived process that registers with the Unpod orchestrator and
    serves a per-call bridge WSS that media agents dial into.

    The control connection to the orchestrator is registration plus
    heartbeat only; there is no per-call dispatch. Each inbound bridge
    connection drives one call's :class:`CallContext` and entrypoint.
    """

    DEFAULT_BASE_URL = "wss://api.unpod.ai"
    _WORKERS_PATH = "/v1/internal/workers"

    def __init__(
        self,
        entrypoint: Callable[[CallContext], Awaitable[None]],
        agent_id: str,
        api_key: str | None = None,
        max_sessions: int = 50,
        max_concurrent_calls: int | None = None,
        permits_per_minute: int = 120,
        drain_timeout_s: int = 60,
        dev_mode: bool = False,
        base_url: str | None = None,
        serving_url: str | None = None,
        agent_secret: str | None = None,
    ) -> None:
        self._entrypoint = entrypoint
        self._agent_id = agent_id
        self._worker_id = f"{agent_id}#{uuid.uuid4().hex[:8]}"
        self._api_key = api_key or os.environ.get("UNPOD_API_KEY")
        if not self._api_key:
            raise ValueError("api_key required (pass directly or set UNPOD_API_KEY)")
        self._max_concurrent = (
            max_concurrent_calls if max_concurrent_calls is not None else max_sessions
        )
        self._permits_per_minute = permits_per_minute
        self._drain_timeout_s = drain_timeout_s
        self._dev_mode = dev_mode
        self._serving_url = serving_url or os.environ.get("UNPOD_RUNNER_URL")
        self._pool = f"{agent_id}@dev" if dev_mode else agent_id
        _base = (
            base_url
            or os.environ.get("UNPOD_ORCHESTRATOR_URL")
            or ws_base()
            or self.DEFAULT_BASE_URL
        ).rstrip("/")
        self._orchestrator_url = f"{_base}{self._WORKERS_PATH}"

        self._hooks = HookRegistry()
        self._active_calls: dict[str, CallContext] = {}
        self._call_start_times: dict[str, float] = {}
        self._completed_count = 0
        self._failed_count = 0
        self._total_duration = 0.0
        self._shutting_down = False

        # Inbound bridge connections are HMAC-verified when an agent secret
        # is configured (directly or via UNPOD_AGENT_SECRET). With no secret
        # the runner accepts unsigned connections (dev/local default). The
        # nonce set is per-runner-process, shared across all connections, so
        # a signed URL can't be replayed against this runner.
        self._agent_secret = agent_secret or os.environ.get("UNPOD_AGENT_SECRET")
        self._seen_nonces: set[str] = set()
        self._verify: Callable[[Any], bool] | None = None
        if self._agent_secret:
            secret = self._agent_secret

            def _verify(ws: Any) -> bool:
                return verify_connection(
                    ws, secret=secret, seen_nonces=self._seen_nonces
                )

            self._verify = _verify

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def on(self, event: str) -> Callable[
        [Callable[..., Any]],
        Callable[..., Any],
    ]:
        """Decorator to register a runner-level hook."""
        return self._hooks.on(event)

    def stats(self) -> RunnerStats:
        """Return current runner pool statistics."""
        in_flight = len(self._active_calls)
        mean_duration = (
            self._total_duration / self._completed_count
            if self._completed_count > 0
            else 0.0
        )
        return RunnerStats(
            in_flight=in_flight,
            queued=0,
            completed_last_hour=self._completed_count,
            failed_last_hour=self._failed_count,
            capacity=self._max_concurrent,
            mean_call_duration_s=mean_duration,
        )

    def active_calls(self) -> list[CallContext]:
        """Return list of currently active call contexts."""
        return list(self._active_calls.values())

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Blocking start -- runs the event loop."""
        asyncio.run(self.run())

    async def run(self) -> None:
        """Async main loop:

        1. Start the per-call bridge server (media agents dial in).
        2. Connect to the orchestrator WSS.
        3. Send the Register frame.
        4. Run the heartbeat loop (registration + heartbeat only).
        """
        import websockets

        host, port = self._serving_host_port()

        async with websockets.serve(self._bridge_handler, host, port):
            async with websockets.connect(
                self._orchestrator_url,
                additional_headers={"Authorization": f"Bearer {self._api_key}"},
            ) as ws:
                register = Register(
                    worker_id=self._worker_id,
                    pool=self._pool,
                    capabilities={
                        "voice_profiles": [],
                        "max_concurrent": self._max_concurrent,
                        "agent_id": self._agent_id,
                        "serving_url": self._serving_url,
                    },
                )
                await ws.send(register.model_dump_json())

                raw = await ws.recv()
                frame = parse_dispatch_frame(raw)
                if not isinstance(frame, Registered):
                    raise ConnectionError(
                        f"Expected Registered, got {type(frame).__name__}"
                    )

                await self._heartbeat_loop(ws, frame.heartbeat_interval_s)

    # ------------------------------------------------------------------
    # Bridge server
    # ------------------------------------------------------------------

    # Bind all interfaces by default: the runner is a server meant to
    # accept inbound bridge connections from media agents.
    _DEFAULT_BIND_HOST = "0.0.0.0"  # nosec B104
    _DEFAULT_BIND_PORT = 8765

    def _serving_host_port(self) -> tuple[str, int]:
        """Derive the bridge server's ``(bind_host, port)`` from the serving URL.

        The serving URL's host is what the runner ADVERTISES (where media agents
        dial it, carried in the Register frame). The *bind* host must be a local
        address: a literal IP is bound as-is (so ``ws://127.0.0.1:8765`` stays
        loopback-only), but a *hostname* — e.g. a Docker/Kubernetes service name
        that resolves to this host — can't be bound directly, so we listen on
        all interfaces and let the name route inbound dials here.
        """
        if not self._serving_url:
            return (self._DEFAULT_BIND_HOST, self._DEFAULT_BIND_PORT)
        parsed = urlparse(self._serving_url)
        host = parsed.hostname or self._DEFAULT_BIND_HOST
        port = parsed.port or self._DEFAULT_BIND_PORT
        if not _is_ip_literal(host):
            host = self._DEFAULT_BIND_HOST
        return (host, port)

    async def _track_call_start(self, ctx: CallContext) -> None:
        """Record a live call context and fire the ``call_start`` hook.

        Runs only for real calls (a context was built), right before the
        entrypoint. Records a start timestamp so the connection's duration
        can be accumulated in :meth:`_track_call_end`.
        """
        self._active_calls[ctx.session_id] = ctx
        self._call_start_times[ctx.session_id] = time.monotonic()
        await self._hooks.fire("call_start", ctx)

    async def _track_call_end(self, ctx: CallContext, final_state: str) -> None:
        """Forget a call context, update accounting, fire ``call_end``.

        Runs only for real calls (a context was built). Accumulates the
        connection duration and counts the call as completed (normal exit)
        or failed (entrypoint raised). ``final_state`` is ``"ended"`` or
        ``"failed"``.
        """
        self._active_calls.pop(ctx.session_id, None)
        started_at = self._call_start_times.pop(ctx.session_id, None)
        if started_at is not None:
            self._total_duration += time.monotonic() - started_at
        if final_state == "failed":
            self._failed_count += 1
        else:
            self._completed_count += 1
        await self._hooks.fire("call_end", ctx, final_state)

    async def _bridge_handler(self, ws: Any) -> None:
        """Handle a single inbound per-call bridge connection.

        Live :class:`CallContext`s are tracked in ``self._active_calls`` via
        the bridge-server lifecycle callbacks, so ``active_calls()`` and the
        heartbeat in-flight count share one source of truth. Completed/failed
        counting and duration accounting live in those same callbacks, which
        fire only for real calls -- rejected (verify=False) or aborted
        handshakes build no context and so count as neither.

        Capacity note: the orchestrator is the capacity gate. ``pick_brain``
        respects ``has_capacity`` and the runner advertises ``max_concurrent``
        in its ``Register`` frame, so the server does not hard-reject at
        ``self._max_concurrent`` here.
        """
        try:
            await handle_bridge_connection(
                ws,
                entrypoint=self._entrypoint,
                agent_id=self._agent_id,
                verify=self._verify,
                on_call_start=self._track_call_start,
                on_call_end=self._track_call_end,
            )
        except Exception:
            # The entrypoint's failure is already accounted for in
            # _track_call_end (final_state="failed"); swallow so a single
            # bad call never tears down the bridge server.
            pass

    async def _heartbeat_loop(self, ws: Any, interval_s: int) -> None:
        """Send periodic heartbeats to the orchestrator.

        ``active_jobs`` reflects the number of live bridge connections,
        derived from ``self._active_calls``.
        """
        while not self._shutting_down:
            hb = Heartbeat(active_jobs=len(self._active_calls))
            await ws.send(hb.model_dump_json())
            await asyncio.sleep(interval_s)

    async def shutdown(self) -> None:
        """Graceful shutdown: stop accepting, drain active calls.

        TODO: shutdown() currently only ends the heartbeat loop (via
        ``_shutting_down``) and drains in-flight calls. Cleanly stopping the
        ``websockets.serve`` listening socket so no new bridge connections
        are accepted is future work.
        """
        self._shutting_down = True
        if self._active_calls:
            await asyncio.sleep(self._drain_timeout_s)
