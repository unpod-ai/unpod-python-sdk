"""Per-call bridge server.

The developer's runner SERVES a per-call bridge WSS; media agents dial in.
For each incoming connection (one per call) the runner:

1. (optionally) verifies the connection (hook reserved for Task 14),
2. sends ``hello`` advertising supported events/verbs,
3. receives ``hello.ack``,
4. receives ``call.started`` carrying call metadata,
5. builds a :class:`CallContext` and a :class:`Session` bound to the
   connection, then runs the developer's ``entrypoint(ctx)``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

from pydantic import BaseModel
from unpod._logging import get_logger
from unpod._protocol import (
    BridgeEvent,
    CallStartedEvent,
    HelloAckEvent,
    HelloEvent,
    parse_bridge_event,
)
from unpod.connectivity.call_context import CallContext
from unpod.connectivity.session import Session

# How long to wait for the client to complete the handshake
# (hello.ack + call.started) before giving up. Prevents a client that
# connects but never finishes the handshake from tying up the connection
# (and a tracked slot) forever.
_HANDSHAKE_TIMEOUT_S = 10.0

logger = get_logger("bridge.server")


class BridgeHandshakeError(Exception):
    """The per-call bridge handshake did not complete as expected.

    Raised (rather than silently returning) so a dialing caller can retry:
    an unexpected/mis-ordered frame or a handshake timeout is a real failure,
    not a completed call. A silent return would look identical to a call that
    ran and ended, black-holing the call.
    """

_PROTOCOL_VERSION = 2
_SUPPORTED_EVENTS = [
    "user.text",
    "user.interrupted",
    "error",
    "metric",
    "turn.metrics",
    "state",
    "call.started",
]
_SUPPORTED_VERBS = [
    "agent.text.delta",
    "agent.text.end",
    "agent.say",
    "agent.transfer",
    "agent.end_call",
    "agent.dispatch",
    "agent.add_participant",
    "agent.remove_participant",
    "agent.merge",
]

_NUMBER_KEYS = (
    "user_number",
    "caller_number",
    "to_number",
    "from_number",
)
_RESERVED_METADATA_KEYS = {
    "direction",
    "user_number",
    "caller_number",
    "to_number",
    "from_number",
    "instructions",
}

# Server-owned identity that must reach the entrypoint whatever the caller put
# in ``data``. The playbook pool resolves its brain from these: ``agent_id``
# first, ``playbook_id`` as the fallback, the other two only scoping the
# lookup. Losing them is a call that connects and then hangs up in silence.
_ROUTING_METADATA_KEYS = ("agent_id", "playbook_id", "project_id", "org_id")


class _ServerTransport:
    """Adapts a server-side websocket to the bridge transport interface.

    Exposes ``send_verb`` / ``recv_event`` so an existing :class:`Session`
    (which expects a ``BridgeClient``-like object) works unchanged over a
    duplex connection where the runner is the server.
    """

    def __init__(self, ws: Any) -> None:
        self._ws = ws

    async def send_verb(self, verb: BaseModel) -> None:
        """Send a bridge verb over the server-side connection."""
        await self._ws.send(verb.model_dump_json())

    async def recv_event(self) -> BridgeEvent:
        """Receive and parse the next bridge event."""
        raw = await self._ws.recv()
        return parse_bridge_event(raw)

    async def close(self) -> None:
        """Close the underlying connection."""
        await self._ws.close()


def _context_from_call_started(
    started: CallStartedEvent,
    *,
    agent_id: str,
    session: Session,
) -> CallContext:
    """Build the developer-facing call context from a call.started event."""
    metadata = started.metadata or {}
    data = metadata.get("data")
    if isinstance(data, dict):
        # Copy: this dict belongs to the caller's frame, and the defaults below
        # would otherwise mutate it.
        data = dict(data)
    else:
        data = {
            key: value
            for key, value in metadata.items()
            if key not in _RESERVED_METADATA_KEYS
        }
    # The dispatcher forwards the ROUTING IDENTITY as top-level metadata keys,
    # alongside a nested ``data`` holding the caller's own payload ("the caller
    # never has to pass either in data" — supervoice dispatch_client). Because
    # an empty ``data: {}`` is still a dict, the branch above always won and
    # that identity was dropped on the floor: a pool brain then saw neither
    # agent_id nor playbook_id, and ended the call as ``invalid_call_target``
    # before it ever spoke.
    #
    # Server value wins over a same-named caller key. ``agent_id`` decides
    # WHICH agent answers, so letting request data name it is the
    # wrong-agent-answered bug.
    for key in _ROUTING_METADATA_KEYS:
        value = metadata.get(key)
        if value:
            data[key] = value
    # voice_profile_id rides call.started as a top-level field (not metadata);
    # surface it in data so entrypoints see the profile the media agent used.
    if started.voice_profile_id:
        data.setdefault("voice_profile_id", started.voice_profile_id)
    user_number = ""
    for key in _NUMBER_KEYS:
        value = metadata.get(key)
        if value:
            user_number = value
            break
    return CallContext(
        call_id=started.session_id,
        session_id=started.session_id,
        # The CALL's agent identity wins; the runner's own id is runner_id.
        agent_id=started.agent_id or agent_id,
        direction=metadata.get("direction", "inbound"),
        user_number=user_number,
        instructions=metadata.get("instructions"),
        data=data,
        session=session,
        runner_id=agent_id,
    )


async def handle_bridge_connection(
    ws: Any,
    *,
    entrypoint: Callable[[CallContext], Awaitable[None]],
    agent_id: str,
    verify: Callable[[Any], bool] | None = None,
    on_call_start: Callable[[CallContext], Awaitable[None]] | None = None,
    on_call_end: Callable[[CallContext, str], Awaitable[None]] | None = None,
    handshake_timeout_s: float = _HANDSHAKE_TIMEOUT_S,
) -> None:
    """Handle a single inbound per-call bridge connection.

    Args:
        ws: A duplex connection with ``await ws.send(str)`` and
            ``await ws.recv() -> str``.
        entrypoint: The developer's per-call coroutine.
        agent_id: The runner's agent identifier.
        verify: Optional hook (reserved for Task 14). If provided and it
            returns ``False``, the connection is closed without running
            the entrypoint.
        on_call_start: Optional async callback awaited with the freshly
            built :class:`CallContext` right before the entrypoint runs.
            Lets the caller track the context for the duration of the
            connection. Only fired when a context was built (i.e. for a
            real call, never for a rejected or aborted handshake).
        on_call_end: Optional async callback awaited with the same context
            and the call's ``final_state`` (``"ended"`` if the entrypoint
            returned normally, ``"failed"`` if it raised) when the
            connection finishes. Only fired when a context was built.
        handshake_timeout_s: Max seconds to wait for the client to send
            ``hello.ack`` then ``call.started``. On timeout the connection
            is closed and counts as neither completed nor failed.
    """
    ctx: CallContext | None = None
    final_state = "ended"
    try:
        if verify is not None and not verify(ws):
            logger.warning(
                "bridge connection rejected: signature/nonce verification "
                "failed (check UNPOD_AGENT_SECRET on both sides, and clock skew)"
            )
            return

        # Advertise capabilities.
        hello = HelloEvent(
            protocol_version=_PROTOCOL_VERSION,
            supported_events=list(_SUPPORTED_EVENTS),
            supported_verbs=list(_SUPPORTED_VERBS),
        )
        await ws.send(hello.model_dump_json())
        logger.debug(
            "handshake: sent hello v%d (%d events, %d verbs)",
            _PROTOCOL_VERSION,
            len(_SUPPORTED_EVENTS),
            len(_SUPPORTED_VERBS),
        )

        # Bound the handshake: a client that connects but never completes
        # it must not tie up the connection (or a tracked slot) forever.
        try:
            async with asyncio.timeout(handshake_timeout_s):
                # Expect hello.ack.
                ack = parse_bridge_event(await ws.recv())
                if not isinstance(ack, HelloAckEvent):
                    raise BridgeHandshakeError(
                        f"expected hello.ack, got {type(ack).__name__}"
                    )
                logger.debug(
                    "handshake: hello.ack v%s (%d events, %d verbs negotiated)",
                    getattr(ack, "protocol_version", "?"),
                    len(getattr(ack, "negotiated_events", []) or []),
                    len(getattr(ack, "negotiated_verbs", []) or []),
                )

                # Expect call.started carrying call metadata.
                started = parse_bridge_event(await ws.recv())
                if not isinstance(started, CallStartedEvent):
                    raise BridgeHandshakeError(
                        f"expected call.started, got {type(started).__name__}"
                    )
        except TimeoutError as exc:
            logger.error(
                "handshake: timed out after %.1fs waiting for "
                "hello.ack + call.started",
                handshake_timeout_s,
            )
            raise BridgeHandshakeError("handshake timed out") from exc
        except BridgeHandshakeError as exc:
            logger.error("handshake: %s", exc)
            raise

        transport = _ServerTransport(ws)
        session = Session(bridge=transport)
        ctx = _context_from_call_started(started, agent_id=agent_id, session=session)
        logger.info(
            "handshake complete session_id=%s job_id=%s room=%s — "
            "running entrypoint",
            getattr(ctx, "session_id", ""),
            getattr(ctx, "job_id", ""),
            getattr(ctx, "room_id", ""),
        )
        if on_call_start is not None:
            await on_call_start(ctx)
        try:
            await entrypoint(ctx)
            logger.info(
                "entrypoint returned session_id=%s — ending the call",
                getattr(ctx, "session_id", ""),
            )
        except asyncio.CancelledError:
            final_state = "failed"
            logger.warning(
                "entrypoint cancelled session_id=%s",
                getattr(ctx, "session_id", ""),
            )
            raise
        except Exception:
            final_state = "failed"
            # exception() so the traceback lands in the log: an entrypoint
            # raising is the single most common cause of a call going silent.
            logger.exception(
                "entrypoint raised session_id=%s — the call is lost "
                "(the worker keeps the media leg until its watchdog fires)",
                getattr(ctx, "session_id", ""),
            )
            raise
    finally:
        if ctx is not None and on_call_end is not None:
            await on_call_end(ctx, final_state)
        await ws.close()
