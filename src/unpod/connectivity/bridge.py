"""Per-call WSS client to Unpod Agent Bridge.

Handles HMAC-SHA256 URL signing, hello/hello.ack handshake,
sending verbs, and receiving events.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from typing import Any

from pydantic import BaseModel
from unpod._protocol import BridgeEvent, HelloAckEvent, HelloEvent, parse_bridge_event


class BridgeClient:
    """Per-call WSS client to Unpod Agent Bridge.

    Handles HMAC-SHA256 URL signing, hello/hello.ack handshake,
    sending verbs, and receiving events.
    """

    def __init__(
        self,
        runner_url: str,
        session_id: str,
        job_id: str,
        agent_secret: str,
        reconnect_max_attempts: int = 5,
        reconnect_initial_delay_ms: int = 200,
    ) -> None:
        self._runner_url = runner_url
        self._session_id = session_id
        self._job_id = job_id
        self._agent_secret = agent_secret
        self._reconnect_max = reconnect_max_attempts
        self._reconnect_delay_ms = reconnect_initial_delay_ms
        self._ws: Any = None
        self._connected = False
        self._protocol_version: str | None = None
        self._negotiated_events: list[str] = []
        self._negotiated_verbs: list[str] = []

    @property
    def connected(self) -> bool:
        """Whether the WebSocket is connected."""
        return self._connected

    @property
    def protocol_version(self) -> str | None:
        """Negotiated protocol version, or None if not yet connected."""
        return self._protocol_version

    @staticmethod
    def sign_url(
        base_url: str,
        session_id: str,
        job_id: str,
        agent_secret: str,
        _nonce: str | None = None,
        _ts: int | None = None,
    ) -> str:
        """Build HMAC-SHA256 signed WebSocket URL.

        Args:
            base_url: WebSocket endpoint (e.g. ws://runner/agent).
            session_id: Call session identifier.
            job_id: Dispatched job identifier.
            agent_secret: Shared secret for HMAC signing.
            _nonce: Override nonce (testing only).
            _ts: Override timestamp (testing only).

        Returns:
            Signed URL with query parameters.
        """
        nonce = _nonce or base64.b64encode(uuid.uuid4().bytes).decode()
        ts = _ts or int(time.time() * 1000)

        payload = f"session_id={session_id}&job_id={job_id}&nonce={nonce}&ts={ts}"
        signature = base64.b64encode(
            hmac.new(
                agent_secret.encode(),
                payload.encode(),
                hashlib.sha256,
            ).digest()
        ).decode()

        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}{payload}&signature={signature}"

    async def connect(self) -> None:
        """Open WebSocket and perform hello/hello.ack handshake."""
        import websockets

        url = self.sign_url(
            self._runner_url,
            self._session_id,
            self._job_id,
            self._agent_secret,
        )
        self._ws = await websockets.connect(url)

        # Send hello
        hello = HelloEvent(
            protocol_version="2",
            supported_events=[
                "user.text",
                "user.interrupted",
                "error",
                "metric",
                "turn.metrics",
            ],
            supported_verbs=[
                "agent.text.delta",
                "agent.text.end",
                "agent.say",
                "agent.transfer",
                "agent.end_call",
                "agent.dispatch",
                "agent.add_participant",
                "agent.remove_participant",
                "agent.merge",
            ],
        )
        await self._ws.send(hello.model_dump_json())

        # Receive hello.ack
        raw = await self._ws.recv()
        ack = parse_bridge_event(raw)
        if isinstance(ack, HelloAckEvent):
            self._protocol_version = "2"
            self._negotiated_events = ack.negotiated_events
            self._negotiated_verbs = ack.negotiated_verbs
            self._connected = True
        else:
            raise ConnectionError(f"Expected hello.ack, got {type(ack).__name__}")

    async def send_verb(self, verb: BaseModel) -> None:
        """Send a bridge verb (agent.text.delta, agent.say, etc.)."""
        if not self._ws:
            raise ConnectionError("Not connected")
        await self._ws.send(verb.model_dump_json())

    async def recv_event(self) -> BridgeEvent:
        """Receive and parse a bridge event."""
        if not self._ws:
            raise ConnectionError("Not connected")
        raw = await self._ws.recv()
        return parse_bridge_event(raw)

    async def close(self) -> None:
        """Close the WebSocket connection."""
        if self._ws:
            await self._ws.close()
            self._ws = None
            self._connected = False
