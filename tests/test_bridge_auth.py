"""Tests for server-side HMAC verification of signed bridge URLs.

The signing payload mirrors the media agent's client
(``supervoice.worker.bridge.client.AgentBridgeClient._build_signed_url``),
which signs the pipe-delimited message ``session_id|job_id|nonce|ts``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from urllib.parse import urlencode

from unpod.connectivity.bridge_auth import verify_connection, verify_signed_url


def _sign(secret: str, session_id: str, job_id: str, nonce: str, ts: int) -> str:
    """Reproduce the media agent's HMAC-SHA256 signature."""
    msg = f"{session_id}|{job_id}|{nonce}|{ts}"
    return base64.b64encode(
        hmac.new(secret.encode(), msg.encode(), hashlib.sha256).digest()
    ).decode()


class _FakeRequest:
    def __init__(self, path: str) -> None:
        self.path = path


class _FakeConn:
    """Stand-in for a websockets server connection exposing request.path."""

    def __init__(self, path: str) -> None:
        self.request = _FakeRequest(path)


def _signed_path(
    secret: str,
    session_id: str = "s1",
    job_id: str = "j1",
    nonce: str = "n1",
    ts: int | None = None,
) -> str:
    ts = ts if ts is not None else int(time.time() * 1000)
    sig = _sign(secret, session_id, job_id, nonce, ts)
    query = urlencode(
        {
            "session_id": session_id,
            "job_id": job_id,
            "nonce": nonce,
            "ts": ts,
            "signature": sig,
        }
    )
    return f"/agent?{query}"


def test_accepts_fresh_valid() -> None:
    ts = int(time.time() * 1000)
    sig = _sign("sek", "s1", "j1", "n1", ts)
    seen: set[str] = set()
    assert (
        verify_signed_url(
            secret="sek",
            session_id="s1",
            job_id="j1",
            nonce="n1",
            ts=ts,
            signature=sig,
            seen_nonces=seen,
        )
        is True
    )
    # Accepting a valid signature records the nonce for replay defense.
    assert "n1" in seen


def test_rejects_bad_signature() -> None:
    ts = int(time.time() * 1000)
    assert (
        verify_signed_url(
            secret="sek",
            session_id="s1",
            job_id="j1",
            nonce="n1",
            ts=ts,
            signature="not-the-signature",
            seen_nonces=set(),
        )
        is False
    )


def test_rejects_stale_timestamp() -> None:
    ts = int(time.time() * 1000)
    old = ts - 60_000
    assert (
        verify_signed_url(
            secret="sek",
            session_id="s1",
            job_id="j1",
            nonce="n2",
            ts=old,
            signature=_sign("sek", "s1", "j1", "n2", old),
            seen_nonces=set(),
            max_skew_ms=30_000,
        )
        is False
    )


def test_rejects_future_timestamp_beyond_skew() -> None:
    ts = int(time.time() * 1000)
    future = ts + 60_000
    assert (
        verify_signed_url(
            secret="sek",
            session_id="s1",
            job_id="j1",
            nonce="n3",
            ts=future,
            signature=_sign("sek", "s1", "j1", "n3", future),
            seen_nonces=set(),
            max_skew_ms=30_000,
        )
        is False
    )


def test_rejects_replayed_nonce() -> None:
    ts = int(time.time() * 1000)
    sig = _sign("sek", "s1", "j1", "n1", ts)
    assert (
        verify_signed_url(
            secret="sek",
            session_id="s1",
            job_id="j1",
            nonce="n1",
            ts=ts,
            signature=sig,
            seen_nonces={"n1"},
        )
        is False
    )


def test_wrong_secret_rejected() -> None:
    ts = int(time.time() * 1000)
    sig = _sign("right-secret", "s1", "j1", "n1", ts)
    assert (
        verify_signed_url(
            secret="wrong-secret",
            session_id="s1",
            job_id="j1",
            nonce="n1",
            ts=ts,
            signature=sig,
            seen_nonces=set(),
        )
        is False
    )


def test_tampered_session_id_rejected() -> None:
    """A signature bound to one session must not validate another."""
    ts = int(time.time() * 1000)
    sig = _sign("sek", "s1", "j1", "n1", ts)
    assert (
        verify_signed_url(
            secret="sek",
            session_id="s2",  # attacker swapped the session
            job_id="j1",
            nonce="n1",
            ts=ts,
            signature=sig,
            seen_nonces=set(),
        )
        is False
    )


# ── verify_connection (query-param extraction over a ws connection) ──


def test_verify_connection_accepts_valid_signed_path() -> None:
    conn = _FakeConn(_signed_path("sek"))
    assert verify_connection(conn, secret="sek", seen_nonces=set()) is True


def test_verify_connection_rejects_tampered_signature() -> None:
    conn = _FakeConn(_signed_path("sek") + "x")  # corrupt the signature tail
    assert verify_connection(conn, secret="sek", seen_nonces=set()) is False


def test_verify_connection_rejects_missing_params() -> None:
    conn = _FakeConn("/agent?session_id=s1&job_id=j1")  # no nonce/ts/signature
    assert verify_connection(conn, secret="sek", seen_nonces=set()) is False


def test_verify_connection_rejects_unsigned_path() -> None:
    conn = _FakeConn("/agent")
    assert verify_connection(conn, secret="sek", seen_nonces=set()) is False


def test_verify_connection_replay_rejected_second_time() -> None:
    seen: set[str] = set()
    path = _signed_path("sek", nonce="reused")
    first = _FakeConn(path)
    second = _FakeConn(path)
    assert verify_connection(first, secret="sek", seen_nonces=seen) is True
    assert verify_connection(second, secret="sek", seen_nonces=seen) is False


def test_verify_connection_reads_legacy_path_attribute() -> None:
    """Falls back to ws.path when there is no request object."""

    class _LegacyConn:
        def __init__(self, path: str) -> None:
            self.path = path

    conn = _LegacyConn(_signed_path("sek"))
    assert verify_connection(conn, secret="sek", seen_nonces=set()) is True
