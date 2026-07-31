"""Per-call bridge DIALER (v2 dial-out transport).

The runner dials OUT to the speech worker's bridge acceptor and then plays
the exact same protocol role it played as a server: send ``hello``, await
``hello.ack`` + ``call.started``, run the entrypoint. All of that logic
lives in :func:`handle_bridge_connection`, which is transport-agnostic —
this module only establishes the connection.

Authentication is the worker's job: the runner presents the per-call
``call_token`` (minted by the orchestrator, delivered in ``job.assign``)
as a query parameter; the worker verifies and pairs the socket to the
pending call.
"""

from __future__ import annotations

from typing import Awaitable, Callable
from urllib.parse import urlencode, urlparse, urlunparse

from unpod._logging import get_logger, redact_url
from unpod.connectivity.bridge_server import handle_bridge_connection
from unpod.connectivity.call_context import CallContext

logger = get_logger("bridge.dialer")


def _with_token(bridge_url: str, call_token: str) -> str:
    """Append ``token=`` to the bridge URL, preserving existing params."""
    parsed = urlparse(bridge_url)
    params = urlencode({"token": call_token})
    query = f"{parsed.query}&{params}" if parsed.query else params
    return urlunparse(parsed._replace(query=query))


# Bound the TCP/WS connect so an unreachable acceptor host fails fast into the
# dialer's bounded retry instead of hanging on the OS TCP timeout (which, times
# the retry count, can exceed the worker's pairing window).
_CONNECT_TIMEOUT_S = 5.0


async def dial_bridge(
    bridge_url: str,
    *,
    call_token: str,
    entrypoint: Callable[[CallContext], Awaitable[None]],
    agent_id: str,
    on_call_start: Callable[[CallContext], Awaitable[None]] | None = None,
    on_call_end: Callable[[CallContext, str], Awaitable[None]] | None = None,
    connect_timeout_s: float = _CONNECT_TIMEOUT_S,
) -> None:
    """Dial the worker's bridge and drive one call to completion."""
    import websockets

    safe_url = redact_url(bridge_url)
    logger.debug(
        "connecting to %s (open_timeout=%.1fs)", safe_url, connect_timeout_s
    )
    async with websockets.connect(
        _with_token(bridge_url, call_token), open_timeout=connect_timeout_s
    ) as ws:
        logger.info("bridge socket open %s", safe_url)
        try:
            await handle_bridge_connection(
                ws,
                entrypoint=entrypoint,
                agent_id=agent_id,
                on_call_start=on_call_start,
                on_call_end=on_call_end,
            )
        finally:
            logger.debug("bridge socket closing %s", safe_url)
