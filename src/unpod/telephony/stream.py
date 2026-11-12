"""The Unpod audio-stream wire protocol, without any Pipecat dependency.

When a customer's ``answer_url`` returns a ``stream`` action, SuperSBC dials
the given ``wss://`` URL and speaks JSON text frames over it. The message
shapes follow Plivo's audio-streaming protocol, so a bot written for Plivo
needs no protocol change (SuperSBC design ``2026-11-11-sip-websocket-streaming``):

SBC → bot::

    {"event": "start", "sequenceNumber": 1,
     "start": {"callId": "...", "streamId": "...", "accountId": "<org id>",
               "tracks": ["inbound"],
               "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000}}}
    {"event": "media", "sequenceNumber": N, "streamId": "...",
     "media": {"track": "inbound", "timestamp": "<unix ms>", "chunk": N,
               "payload": "<base64 audio>"}}
    {"event": "dtmf", "sequenceNumber": N, "streamId": "...",
     "dtmf": {"track": "inbound", "digit": "5", "timestamp": "<unix ms>"}}

bot → SBC::

    {"event": "playAudio", "streamId": "...",
     "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000, "payload": "<base64>"}}
    {"event": "clearAudio", "streamId": "..."}

The SBC signs the WebSocket upgrade request with the org's webhook secret, the
same ``X-Webhook-Signature: t=<ts>,v1=<hex>`` scheme as call events;
:func:`verify_stream_request` checks it.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from unpod.telephony.events import (
    DEFAULT_SIGNATURE_HEADER,
    DEFAULT_TOLERANCE_SECS,
    _header,
    verify_signature,
)

#: μ-law (G.711 PCMU). The default, and the only format SuperSBC streams in v1.
MULAW = "audio/x-mulaw"
#: Linear 16-bit PCM, little-endian, mono.
L16 = "audio/x-l16"
ENCODINGS = (MULAW, L16)

DEFAULT_ENCODING = MULAW
DEFAULT_SAMPLE_RATE = 8000


class MediaFormat(BaseModel):
    """Encoding and sample rate of the audio on one direction of a stream."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    encoding: str = DEFAULT_ENCODING
    sample_rate: int = Field(default=DEFAULT_SAMPLE_RATE, alias="sampleRate")

    @classmethod
    def parse(cls, value: Any) -> MediaFormat:
        """Read ``start.mediaFormat``. Plivo-style ``"audio/x-mulaw;rate=8000"``
        content types are accepted too; anything missing falls back to μ-law 8k."""
        if isinstance(value, MediaFormat):
            return value
        if isinstance(value, str):
            return cls.from_content_type(value)
        if not isinstance(value, Mapping):
            return cls()
        encoding = value.get("encoding") or value.get("contentType") or DEFAULT_ENCODING
        fmt = cls.from_content_type(str(encoding))
        rate = value.get("sampleRate", value.get("sample_rate"))
        if rate not in (None, ""):
            fmt.sample_rate = int(rate)
        return fmt

    @classmethod
    def from_content_type(cls, content_type: str) -> MediaFormat:
        """``"audio/x-l16;rate=16000"`` → ``MediaFormat(L16, 16000)``."""
        parts = [p.strip() for p in content_type.split(";") if p.strip()]
        encoding = (parts[0] if parts else DEFAULT_ENCODING).lower()
        rate = DEFAULT_SAMPLE_RATE
        for part in parts[1:]:
            key, _, val = part.partition("=")
            if key.strip().lower() == "rate" and val.strip().isdigit():
                rate = int(val)
        return cls(encoding=encoding, sampleRate=rate)

    def validate_supported(self) -> MediaFormat:
        """Raise ``ValueError`` unless this is a format the serializer can code."""
        if self.encoding not in ENCODINGS:
            raise ValueError(
                f"unsupported stream encoding {self.encoding!r}; use one of {ENCODINGS}"
            )
        if self.encoding == MULAW and self.sample_rate != 8000:
            raise ValueError("audio/x-mulaw streams are 8000 Hz only")
        if self.sample_rate <= 0:
            raise ValueError("sample rate must be positive")
        return self


class StreamStart(BaseModel):
    """The ``start`` event: who the stream belongs to and its audio format.

    ``call_id`` is the SBC call id, the one ``client.telephony.calls`` takes
    (``calls.hangup(call_id)``). ``account_id`` is the Unpod org. ``custom``
    holds any extra keys the SBC sends in ``start``.
    """

    model_config = ConfigDict(extra="allow")

    call_id: str
    stream_id: str
    account_id: str | None = None
    tracks: list[str] = Field(default_factory=lambda: ["inbound"])
    media_format: MediaFormat = Field(default_factory=MediaFormat)
    custom: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


_START_KNOWN = {"callId", "streamId", "accountId", "tracks", "mediaFormat"}


def parse_start(message: Mapping[str, Any] | str | bytes) -> StreamStart:
    """Parse a ``start`` event. Raises ``ValueError`` if it is not one, or if
    it lacks ``callId``/``streamId``."""
    data = (
        decode_message(message) if not isinstance(message, Mapping) else dict(message)
    )
    if data.get("event") != "start":
        raise ValueError(f"expected a 'start' event, got {data.get('event')!r}")
    start = data.get("start")
    if not isinstance(start, Mapping):
        raise ValueError("'start' event has no 'start' object")  # noqa: TRY004 - malformed message
    call_id = start.get("callId")
    stream_id = start.get("streamId") or data.get("streamId")
    if not call_id or not stream_id:
        raise ValueError("'start' event is missing callId or streamId")
    tracks = start.get("tracks")
    return StreamStart(
        call_id=str(call_id),
        stream_id=str(stream_id),
        account_id=str(start["accountId"]) if start.get("accountId") else None,
        tracks=[str(t) for t in tracks]
        if isinstance(tracks, list) and tracks
        else ["inbound"],
        media_format=MediaFormat.parse(start.get("mediaFormat")),
        custom={k: v for k, v in start.items() if k not in _START_KNOWN},
        raw=data,
    )


def decode_message(data: str | bytes) -> dict[str, Any]:
    """Decode one JSON text frame; ``{}`` when it is not a JSON object."""
    try:
        value = json.loads(data)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def play_audio_message(stream_id: str, payload: bytes, fmt: MediaFormat) -> str:
    """Build a ``playAudio`` frame carrying ``payload`` (already encoded in ``fmt``)."""
    return json.dumps(
        {
            "event": "playAudio",
            "streamId": stream_id,
            "media": {
                "contentType": fmt.encoding,
                "sampleRate": fmt.sample_rate,
                "payload": base64.b64encode(payload).decode("ascii"),
            },
        }
    )


def clear_audio_message(stream_id: str) -> str:
    """Build a ``clearAudio`` frame: drop whatever is queued for playback."""
    return json.dumps({"event": "clearAudio", "streamId": stream_id})


def verify_stream_request(
    headers: Mapping[str, str],
    secret: str | bytes,
    *,
    body: bytes = b"",
    signature_header: str = DEFAULT_SIGNATURE_HEADER,
    tolerance: int | None = DEFAULT_TOLERANCE_SECS,
) -> bool:
    """True when the WebSocket upgrade request carries a valid SBC signature.

    Call it before ``websocket.accept()`` and close the socket when it returns
    False, so only Unpod's SBC can open a stream to your bot. ``secret`` is the
    org's webhook secret (``client.telephony.webhook_secret``). An upgrade has
    no body, so the signed payload is ``"<t>."`` followed by ``body`` (empty).
    """
    if not secret:
        return False
    return verify_signature(
        body, _header(headers, signature_header), secret, tolerance=tolerance
    )


__all__ = [
    "DEFAULT_ENCODING",
    "DEFAULT_SAMPLE_RATE",
    "ENCODINGS",
    "L16",
    "MULAW",
    "MediaFormat",
    "StreamStart",
    "clear_audio_message",
    "decode_message",
    "parse_start",
    "play_audio_message",
    "verify_stream_request",
]
