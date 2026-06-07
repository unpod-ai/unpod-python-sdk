"""Tests for BridgeClient WSS client with HMAC auth."""

from unpod.connectivity.bridge import BridgeClient


def test_bridge_sign_url() -> None:
    """HMAC-SHA256 signed connection URL."""
    url = BridgeClient.sign_url(
        base_url="ws://runner.example.com/agent",
        session_id="s1",
        job_id="j1",
        agent_secret="secret123",
    )
    assert "session_id=s1" in url
    assert "job_id=j1" in url
    assert "signature=" in url
    assert "ts=" in url
    assert "nonce=" in url


def test_bridge_sign_url_deterministic() -> None:
    """Same inputs + same nonce/ts = same URL."""
    url1 = BridgeClient.sign_url(
        base_url="ws://runner.example.com/agent",
        session_id="s1",
        job_id="j1",
        agent_secret="secret123",
        _nonce="fixed",
        _ts=1000,
    )
    url2 = BridgeClient.sign_url(
        base_url="ws://runner.example.com/agent",
        session_id="s1",
        job_id="j1",
        agent_secret="secret123",
        _nonce="fixed",
        _ts=1000,
    )
    assert url1 == url2


def test_bridge_sign_url_different_secret() -> None:
    """Different secrets produce different signatures."""
    url1 = BridgeClient.sign_url(
        base_url="ws://runner.example.com/agent",
        session_id="s1",
        job_id="j1",
        agent_secret="secret1",
        _nonce="fixed",
        _ts=1000,
    )
    url2 = BridgeClient.sign_url(
        base_url="ws://runner.example.com/agent",
        session_id="s1",
        job_id="j1",
        agent_secret="secret2",
        _nonce="fixed",
        _ts=1000,
    )
    assert url1 != url2


def test_bridge_client_init() -> None:
    """BridgeClient stores session and job IDs."""
    client = BridgeClient(
        runner_url="ws://runner.example.com/agent",
        session_id="s1",
        job_id="j1",
        agent_secret="secret123",
    )
    assert client._session_id == "s1"
    assert client._job_id == "j1"


def test_bridge_initial_state() -> None:
    """New client starts disconnected with no protocol version."""
    client = BridgeClient(
        runner_url="ws://runner.example.com/agent",
        session_id="s1",
        job_id="j1",
        agent_secret="secret123",
    )
    assert client.connected is False
    assert client.protocol_version is None


def test_bridge_sign_url_with_query_param() -> None:
    """Base URL already containing '?' uses '&' separator."""
    url = BridgeClient.sign_url(
        base_url="ws://runner.example.com/agent?token=abc",
        session_id="s1",
        job_id="j1",
        agent_secret="secret123",
        _nonce="fixed",
        _ts=1000,
    )
    assert "?token=abc&session_id=s1" in url


def test_bridge_sign_url_signature_verifiable() -> None:
    """Signature can be independently verified with HMAC-SHA256."""
    import base64
    import hashlib
    import hmac

    url = BridgeClient.sign_url(
        base_url="ws://runner.example.com/agent",
        session_id="s1",
        job_id="j1",
        agent_secret="secret123",
        _nonce="nonce1",
        _ts=5000,
    )
    # Extract signature from URL
    parts = url.split("signature=")
    sig_from_url = parts[1]

    # Recompute expected signature
    payload = "session_id=s1&job_id=j1&nonce=nonce1&ts=5000"
    expected_sig = base64.b64encode(
        hmac.new(
            b"secret123",
            payload.encode(),
            hashlib.sha256,
        ).digest()
    ).decode()
    assert sig_from_url == expected_sig
