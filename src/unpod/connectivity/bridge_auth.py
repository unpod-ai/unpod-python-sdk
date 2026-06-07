"""Server-side HMAC verification for inbound bridge connections.

The media agent (client) signs its connect URL with the per-call
``agent_secret``; the runner (server) verifies it here before running the
developer entrypoint. The signed payload is the pipe-delimited message
``session_id|job_id|nonce|ts`` (matching the media agent's
``AgentBridgeClient._build_signed_url``), HMAC-SHA256, base64-encoded.

Verification rejects:

* a signature that does not match the recomputed HMAC (constant-time
  compare),
* a timestamp outside ``max_skew_ms`` of now (replay window), and
* a ``nonce`` already present in ``seen_nonces`` (replay within the
  window).

On success the ``nonce`` is recorded in ``seen_nonces`` so a later replay
of the same URL is rejected.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

# Default replay window: a signed URL is valid for +/- this many ms.
_DEFAULT_MAX_SKEW_MS = 30_000

# Query parameters the media agent appends to the connect URL.
_REQUIRED_PARAMS = ("session_id", "job_id", "nonce", "ts", "signature")


def _expected_signature(
    secret: str,
    session_id: str,
    job_id: str,
    nonce: str,
    ts: int,
) -> str:
    """Recompute the base64 HMAC-SHA256 signature for the call payload."""
    msg = f"{session_id}|{job_id}|{nonce}|{ts}"
    return base64.b64encode(
        hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()


def verify_signed_url(
    *,
    secret: str,
    session_id: str,
    job_id: str,
    nonce: str,
    ts: int,
    signature: str,
    seen_nonces: set[str],
    max_skew_ms: int = _DEFAULT_MAX_SKEW_MS,
    now_ms: int | None = None,
) -> bool:
    """Verify a media agent's signed bridge connection.

    Args:
        secret: The per-call ``agent_secret`` shared with the media agent.
        session_id: Call session id from the connect URL.
        job_id: Dispatched job id from the connect URL.
        nonce: Per-connection nonce from the connect URL.
        ts: Client timestamp in milliseconds since the epoch.
        signature: Base64 HMAC-SHA256 the client presented.
        seen_nonces: Mutable set of nonces already accepted for this job;
            a replayed nonce is rejected. Mutated (the nonce is added) only
            when verification fully succeeds.
        max_skew_ms: Maximum allowed difference between ``ts`` and now.
        now_ms: Current time in ms (injectable for tests; defaults to the
            wall clock).

    Returns:
        ``True`` when the signature is valid, fresh, and unreplayed;
        ``False`` otherwise.
    """
    # Replay-by-nonce: reject before doing any crypto.
    if nonce in seen_nonces:
        return False

    # Freshness: reject timestamps outside the skew window.
    current_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    if abs(current_ms - ts) > max_skew_ms:
        return False

    # Authenticity: constant-time compare against the recomputed HMAC.
    expected = _expected_signature(secret, session_id, job_id, nonce, ts)
    if not hmac.compare_digest(expected, signature):
        return False

    seen_nonces.add(nonce)
    return True


def _connection_path(ws: Any) -> str:
    """Best-effort extraction of the request path+query from a connection.

    Newer ``websockets`` exposes ``ws.request.path``; older versions expose
    ``ws.path``. Returns an empty string when neither is present.
    """
    request = getattr(ws, "request", None)
    if request is not None:
        path = getattr(request, "path", None)
        if isinstance(path, str):
            return path
    path = getattr(ws, "path", "")
    return path if isinstance(path, str) else ""


def verify_connection(
    ws: Any,
    *,
    secret: str,
    seen_nonces: set[str],
    max_skew_ms: int = _DEFAULT_MAX_SKEW_MS,
    now_ms: int | None = None,
) -> bool:
    """Verify an inbound connection's HMAC-signed query parameters.

    Extracts the signed parameters from the connection's request path and
    delegates to :func:`verify_signed_url`. Returns ``False`` when any
    required parameter is missing or malformed.
    """
    query = parse_qs(urlparse(_connection_path(ws)).query)
    values: dict[str, str] = {}
    for key in _REQUIRED_PARAMS:
        got = query.get(key)
        if not got:
            return False
        values[key] = got[0]

    try:
        ts = int(values["ts"])
    except ValueError:
        return False

    return verify_signed_url(
        secret=secret,
        session_id=values["session_id"],
        job_id=values["job_id"],
        nonce=values["nonce"],
        ts=ts,
        signature=values["signature"],
        seen_nonces=seen_nonces,
        max_skew_ms=max_skew_ms,
        now_ms=now_ms,
    )


__all__ = ["verify_connection", "verify_signed_url"]
