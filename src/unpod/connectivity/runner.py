"""AgentRunner: long-lived process that registers with the Unpod
orchestrator and SERVES a per-call bridge that media agents dial into."""

from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import os
import random
import time
import uuid
import warnings
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from unpod._base_url import ws_base
from unpod._logging import close_code_of, get_logger, redact_url
from unpod._protocol import (
    Heartbeat,
    JobAck,
    JobAssign,
    JobCancel,
    Register,
    Registered,
    parse_dispatch_frame,
)
from unpod.connectivity.bridge_auth import verify_connection
from unpod.connectivity.bridge_server import handle_bridge_connection
from unpod.connectivity.call_context import CallContext
from unpod.connectivity.hooks import HookRegistry
from unpod.models.session import RunnerStats


logger = get_logger("runner")

# Control-socket close codes that mean "do not bother retrying": the
# orchestrator rejected the credential (4001) or applied a policy (1008). A
# silent reconnect loop on a bad API key is indistinguishable from a network
# outage, which is exactly the debugging dead end this avoids.
_FATAL_CLOSE_CODES = frozenset({1008, 4001})


class RunnerAuthError(ConnectionError):
    """The orchestrator refused this runner's credential. Not retriable."""


# Worker-side bridge acceptor close codes, translated into the action to take.
# Without these a failed dial reads as a bare "connection closed".
_DIAL_CLOSE_HINTS = {
    4003: (
        "worker rejected the pairing: unknown call_id, expired pairing "
        "(the dial arrived >20s after dispatch), or bad call_token"
    ),
    4009: "worker already has a live bridge socket for this call",
}


def _is_ip_literal(host: str) -> bool:
    """True if ``host`` is a literal IPv4/IPv6 address (bindable as-is)."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


class _TransportRejected(ConnectionError):
    """The orchestrator did not acknowledge the requested transport.

    Not retriable: reconnecting would hit the same (older) orchestrator.
    """


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
        transport: str = "dial_out",
    ) -> None:
        if transport not in ("dial_out", "serve"):
            raise ValueError(f"transport must be 'dial_out' or 'serve', got {transport!r}")
        self._transport = transport
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

        # v2 dial-out state: accepted jobs (dialing OR in-call) by job_id.
        self._jobs: dict[str, asyncio.Task] = {}
        self._backoff_initial_s = 1.0
        self._backoff_max_s = 30.0
        if transport == "serve" and not self._serving_url:
            raise ValueError(
                "transport='serve' requires serving_url (or UNPOD_RUNNER_URL): "
                "the orchestrator has no way to reach this runner without it "
                "and refuses the registration"
            )
        if transport == "dial_out" and (serving_url or agent_secret):
            warnings.warn(
                "serving_url/agent_secret are ignored with the dial_out "
                "transport (the runner never listens); drop them or pass "
                "transport='serve' for the legacy server model",
                DeprecationWarning,
                stacklevel=2,
            )

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

    def on(
        self, event: str
    ) -> Callable[
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
        """Supervising main loop (v2 dial_out default).

        connect -> register -> serve control frames. Any control-socket
        failure reconnects with jittered exponential backoff and
        re-registers idempotently (same worker_id). In-flight calls ride
        their own bridge sockets and survive control drops. Registration
        failure never kills the process — except a transport rejection
        (orchestrator too old for dial_out), which is not retriable.

        ``transport="serve"`` runs the legacy server model unchanged.
        """
        import websockets

        if self._transport == "serve":
            await self._run_legacy_serve()
            return

        logger.info(
            "runner starting worker_id=%s agent_id=%s pool=%s transport=%s "
            "max_concurrent=%d orchestrator=%s",
            self._worker_id,
            self._agent_id,
            self._pool,
            self._transport,
            self._max_concurrent,
            self._orchestrator_url,
        )
        attempt = 0
        while not self._shutting_down:
            try:
                logger.info(
                    "control: connecting to %s (attempt %d)",
                    self._orchestrator_url,
                    attempt + 1,
                )
                async with websockets.connect(
                    self._orchestrator_url,
                    additional_headers={
                        "Authorization": f"Bearer {self._api_key}"
                    },
                ) as ws:
                    attempt = 0  # connected: reset the backoff ladder
                    logger.info("control: connected worker_id=%s", self._worker_id)
                    await self._control_session(ws)
                    logger.warning(
                        "control: session ended worker_id=%s (will reconnect); "
                        "in-flight calls: %d",
                        self._worker_id,
                        len(self._active_calls),
                    )
            except _TransportRejected:
                logger.error(
                    "control: orchestrator rejected transport=%s — not retriable",
                    self._transport,
                )
                raise
            except Exception as e:  # noqa: BLE001 — network errors: retry
                code = close_code_of(e)
                if code in _FATAL_CLOSE_CODES:
                    # Retrying cannot fix a refused credential; surface it
                    # instead of looping forever with nothing in the log.
                    logger.error(
                        "control: orchestrator refused the connection "
                        "(close %d) — check UNPOD_API_KEY; not retrying",
                        code,
                    )
                    raise RunnerAuthError(
                        f"orchestrator refused the runner credential (close {code})"
                    ) from e
                logger.warning(
                    "control: link failed (%s: %s%s)",
                    type(e).__name__,
                    e,
                    f", close={code}" if code is not None else "",
                )
            if self._shutting_down:
                return
            attempt += 1
            delay = self._next_backoff(attempt)
            logger.info(
                "control: reconnecting in %.1fs (attempt %d)", delay, attempt + 1
            )
            await asyncio.sleep(delay)

    async def _run_legacy_serve(self) -> None:
        """Legacy server model (transport="serve") — pre-v2 body, verbatim.

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
                        "kind": "brain",
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
    # v2 dial-out control plane
    # ------------------------------------------------------------------

    def _build_register(self) -> Register:
        """The v2 (dial_out) Register frame — no serving_url.

        The legacy serve path never calls this: _run_legacy_serve keeps
        the old inline Register construction, byte-identical on the wire.
        """
        return Register(
            worker_id=self._worker_id,
            pool=self._pool,
            capabilities={
                "voice_profiles": [],
                "max_concurrent": self._max_concurrent,
                "agent_id": self._agent_id,
                "transport": self._transport,
                # Role discriminator: this process is a BRAIN (agent runner),
                # never a media/speech worker. The orchestrator infers the same
                # from agent_id when absent, but declaring it makes a
                # mis-advertisement fail at Register instead of at dispatch.
                "kind": "brain",
            },
        )

    def _next_backoff(self, attempt: int) -> float:
        """Exponential backoff with +-50% jitter, capped AFTER jitter."""
        base = min(
            self._backoff_initial_s * (2 ** (attempt - 1)), self._backoff_max_s
        )
        return min(base * (0.5 + random.random()), self._backoff_max_s)  # noqa: S311

    async def _control_session(self, ws: Any) -> None:
        """One connected control session: register, then heartbeat + recv."""
        register = self._build_register()
        logger.info(
            "control: register worker_id=%s pool=%s agent_id=%s transport=%s",
            register.worker_id,
            register.pool,
            self._agent_id,
            self._transport,
        )
        await ws.send(register.model_dump_json())
        frame = parse_dispatch_frame(await ws.recv())
        if not isinstance(frame, Registered):
            logger.error(
                "control: expected 'registered', got %r — orchestrator refused "
                "the register frame (check pool/agent_id and capabilities)",
                getattr(frame, "type", type(frame).__name__),
            )
            raise ConnectionError(
                f"Expected Registered, got {type(frame).__name__}"
            )
        logger.info(
            "control: registered worker_id=%s heartbeat=%ds transport_ack=%s",
            self._worker_id,
            frame.heartbeat_interval_s,
            frame.transport_ack,
        )
        if frame.transport_ack != "dial_out":
            raise _TransportRejected(
                "orchestrator did not acknowledge dial_out transport — "
                "upgrade supervoice or construct AgentRunner(transport='serve')"
            )
        hb_task = asyncio.create_task(
            self._heartbeat_loop(ws, frame.heartbeat_interval_s)
        )
        try:
            await self._control_recv_loop(ws)
        finally:
            hb_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hb_task

    async def _control_recv_loop(self, ws: Any) -> None:
        while not self._shutting_down:
            frame = parse_dispatch_frame(await ws.recv())
            if isinstance(frame, JobAssign):
                await self._handle_assign(ws, frame)
            elif isinstance(frame, JobCancel):
                task = self._jobs.pop(frame.job_id, None)
                logger.info(
                    "job.cancel job_id=%s (%s)",
                    frame.job_id,
                    "cancelling live job" if task is not None else "unknown job",
                )
                if task is not None:
                    task.cancel()

    async def _handle_assign(self, ws: Any, assign: JobAssign) -> None:
        reason: str | None = None
        if assign.agent_id != self._agent_id:
            reason = "agent_id_mismatch"
        elif len(self._jobs) >= self._max_concurrent:
            # _jobs covers accepted-but-still-dialing AND in-call jobs;
            # _active_calls alone would undercount during a dial burst.
            reason = "at_capacity"
        logger.info(
            "job.assign job_id=%s call_id=%s agent_id=%s bridge=%s -> %s%s "
            "(jobs %d/%d)",
            assign.job_id,
            assign.call_id,
            assign.agent_id,
            redact_url(assign.bridge_url),
            "accepted" if reason is None else "rejected",
            f" ({reason})" if reason else "",
            len(self._jobs),
            self._max_concurrent,
        )
        ack = JobAck(
            job_id=assign.job_id,
            assign_id=assign.assign_id,
            accepted=reason is None,
            reason=reason,
        )
        await ws.send(ack.model_dump_json())
        if reason is not None:
            return
        task = asyncio.create_task(self._dial_for_job(assign))
        self._jobs[assign.job_id] = task
        task.add_done_callback(lambda _t: self._jobs.pop(assign.job_id, None))

    async def _dial_for_job(self, assign: JobAssign) -> None:
        """Dial the worker's bridge; bounded retry on connect/handshake failure.

        The retry covers the pre-handshake window only: transient network
        errors and a refused pairing (the worker may register the pairing
        a beat after our ack). Once handle_bridge_connection returns, the
        call is over from the runner's perspective — mid-call automatic
        redial is an explicit follow-up: Session.run swallows a transport
        drop as a normal end, so the dialer cannot distinguish a drop
        from a hangup without an abnormal-close signal that doesn't exist
        yet. The worker-side pairing stays valid for the job lifetime so
        a future redial (or pool-level resume via the shared session
        store) works.
        """
        from unpod.connectivity.bridge_dialer import dial_bridge

        url = redact_url(assign.bridge_url)
        for attempt in range(1, 4):
            started = time.monotonic()
            try:
                logger.info(
                    "bridge: dialing job_id=%s %s (attempt %d/3)",
                    assign.job_id,
                    url,
                    attempt,
                )
                await dial_bridge(
                    assign.bridge_url,
                    call_token=assign.call_token,
                    entrypoint=self._entrypoint,
                    agent_id=self._agent_id,
                    on_call_start=self._track_call_start,
                    on_call_end=self._track_call_end,
                )
                # Reached only when the bridge socket closed. The dialer does
                # NOT redial mid-call, so from here the worker is talking to
                # nobody until the call is torn down — say so explicitly.
                logger.info(
                    "bridge: closed job_id=%s after %.1fs — no redial "
                    "(mid-call reconnect is not implemented; the worker will "
                    "see 'bridge not connected' until the call ends)",
                    assign.job_id,
                    time.monotonic() - started,
                )
                return
            except asyncio.CancelledError:
                logger.info("bridge: dial cancelled job_id=%s", assign.job_id)
                raise  # job.cancel
            except Exception as e:  # noqa: BLE001 — a bad call never kills the runner
                code = close_code_of(e)
                hint = _DIAL_CLOSE_HINTS.get(code or 0, "")
                logger.warning(
                    "bridge: dial failed job_id=%s attempt %d/3 after %.1fs "
                    "(%s: %s%s)%s",
                    assign.job_id,
                    attempt,
                    time.monotonic() - started,
                    type(e).__name__,
                    e,
                    f", close={code}" if code is not None else "",
                    f" — {hint}" if hint else "",
                )
                if attempt == 3 or self._shutting_down:
                    logger.error(
                        "bridge: giving up job_id=%s %s — the call has no "
                        "agent attached",
                        assign.job_id,
                        "(shutting down)" if self._shutting_down else "after 3 attempts",
                    )
                    return
                await asyncio.sleep(0.2 * (2 ** (attempt - 1)))

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
        logger.info(
            "call start session_id=%s job_id=%s (active %d/%d)",
            ctx.session_id,
            getattr(ctx, "job_id", ""),
            len(self._active_calls),
            self._max_concurrent,
        )
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
        duration = time.monotonic() - started_at if started_at is not None else 0.0
        if started_at is not None:
            self._total_duration += duration
        if final_state == "failed":
            self._failed_count += 1
        else:
            self._completed_count += 1
        log = logger.error if final_state == "failed" else logger.info
        log(
            "call end session_id=%s state=%s after %.1fs "
            "(completed=%d failed=%d active=%d)",
            ctx.session_id,
            final_state,
            duration,
            self._completed_count,
            self._failed_count,
            len(self._active_calls),
        )
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
            # worker_id (v2) lets a reconnected socket correlate explicitly.
            # Omitted in legacy serve mode to keep that wire byte-identical.
            hb = Heartbeat(
                active_jobs=len(self._active_calls),
                worker_id=self._worker_id if self._transport == "dial_out" else None,
            )
            logger.debug(
                "heartbeat worker_id=%s active_jobs=%d",
                self._worker_id,
                hb.active_jobs,
            )
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
        logger.info(
            "shutdown requested worker_id=%s draining %d active call(s) "
            "(timeout %ds)",
            self._worker_id,
            len(self._active_calls),
            self._drain_timeout_s,
        )
        if self._active_calls:
            await asyncio.sleep(self._drain_timeout_s)
            if self._active_calls:
                logger.warning(
                    "shutdown: %d call(s) still active after the %ds drain: %s",
                    len(self._active_calls),
                    self._drain_timeout_s,
                    ", ".join(sorted(self._active_calls)),
                )
        logger.info("shutdown complete worker_id=%s", self._worker_id)
