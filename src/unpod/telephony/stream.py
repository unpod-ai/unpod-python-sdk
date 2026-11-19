"""The Unpod audio-stream wire protocol, without any Pipecat dependency.

When a customer's ``answer_url`` returns a ``stream`` action, SuperSBC dials
the given ``wss://`` URL and speaks JSON text frames over it. (SuperSBC design ``2026-11-11-sip-websocket-streaming``):

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

    {"event": "playedStream", "sequenceNumber": N, "streamId": "...", "name": "<checkpoint>"}
    {"event": "clearedAudio", "sequenceNumber": N, "streamId": "..."}

Every SBC frame also carries a top-level ``"extra_headers"`` string (the
stream's ``extraHeaders``). The SBC closes the socket (code 1000) to end the
stream; there is no ``stop`` frame.

bot → SBC::

    {"event": "playAudio", "streamId": "...",
     "media": {"contentType": "audio/x-mulaw", "sampleRate": 8000, "payload": "<base64>"}}
    {"event": "clearAudio", "streamId": "..."}
    {"event": "checkpoint", "streamId": "...", "name": "<label>"}
    {"event": "sendDTMF", "streamId": "...", "dtmf": "12#"}

``checkpoint`` asks the SBC to answer ``playedStream`` once every audio queued
before it has played; ``clearAudio`` drops the queue (pending checkpoints get
no reply) and is answered with ``clearedAudio``. ``sendDTMF`` sends digits
(``0-9``, ``A-D``, ``*``, ``#``; at most 32 queued) to the caller.

The SBC signs the WebSocket upgrade request with the org's webhook secret in
one header, the same family as the webhook ``X-Webhook-Signature``::

    X-Unpod-Signature: t=<unix seconds>,n=<nonce>,v1=<hex>
    v1 = hex(HMAC-SHA256(secret, "ws." + t + "." + n + "." + url))

``url`` is the URL exactly as the SBC dialled it (``wss://host/path?query``).
:func:`verify_stream_request` checks it, rejects signatures older than
``tolerance`` and, with a :class:`NonceCache`, repeats.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from unpod.telephony.events import DEFAULT_TOLERANCE_SECS, _header

#: μ-law (G.711 PCMU), 8000 Hz. The default.
MULAW = "audio/x-mulaw"
#: A-law (G.711 PCMA), 8000 Hz. Common on carrier trunks; streaming in the
#: trunk's own codec saves the SBC a transcode.
ALAW = "audio/x-alaw"
#: Linear 16-bit PCM, little-endian, mono, at 8000, 16000 or 24000 Hz.
L16 = "audio/x-l16"
ENCODINGS = (MULAW, ALAW, L16)
#: Sample rates the SBC accepts per encoding (``StreamFormat::parse``).
SAMPLE_RATES: dict[str, tuple[int, ...]] = {
    MULAW: (8000,),
    ALAW: (8000,),
    L16: (8000, 16000, 24000),
}

DEFAULT_ENCODING = MULAW
DEFAULT_SAMPLE_RATE = 8000
#: What the SBC streams when no ``contentType`` was given
#: (``StreamFormat::DEFAULT_CONTENT_TYPE``): the fallback when a ``start``
#: event carries no ``mediaFormat``.
SBC_DEFAULT_CONTENT_TYPE = "audio/x-l16;rate=8000"


class MediaFormat(BaseModel):
    """Encoding and sample rate of the audio on one direction of a stream."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    encoding: str = DEFAULT_ENCODING
    sample_rate: int = Field(default=DEFAULT_SAMPLE_RATE, alias="sampleRate")

    @classmethod
    def parse(cls, value: Any) -> MediaFormat:
        """Read ``start.mediaFormat``. ``"audio/x-mulaw;rate=8000"``
        content types are accepted too. A missing format falls back to what the
        SBC streams by default, L16 8 kHz."""
        if isinstance(value, MediaFormat):
            return value
        if isinstance(value, str):
            return cls.from_content_type(value)
        if not isinstance(value, Mapping):
            return cls.from_content_type(SBC_DEFAULT_CONTENT_TYPE)
        encoding = value.get("encoding") or value.get("contentType")
        if not encoding:
            return cls.from_content_type(SBC_DEFAULT_CONTENT_TYPE)
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
        allowed = SAMPLE_RATES[self.encoding]
        if self.sample_rate not in allowed:
            rates = ", ".join(str(r) for r in allowed)
            raise ValueError(f"{self.encoding} streams support {rates} Hz only")
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
    #: The stream's ``extraHeaders`` (``"k=v,k2=v2"``), "" when none.
    extra_headers: str = ""
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
        extra_headers=str(data.get("extra_headers") or ""),
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


#: Longest checkpoint name the SBC accepts.
MAX_CHECKPOINT_NAME = 256
#: Most DTMF digits the SBC queues from one bot at a time.
MAX_SEND_DTMF = 32
_DTMF_CHARS = frozenset("0123456789ABCD*#")


def checkpoint_message(stream_id: str, name: str) -> str:
    """Build a ``checkpoint`` frame. The SBC answers ``playedStream`` with
    ``name`` once all audio sent before it has played."""
    if not name or len(name) > MAX_CHECKPOINT_NAME:
        raise ValueError(f"checkpoint name must be 1..{MAX_CHECKPOINT_NAME} characters")
    return json.dumps({"event": "checkpoint", "streamId": stream_id, "name": name})


def send_dtmf_message(stream_id: str, digits: str) -> str:
    """Build a ``sendDTMF`` frame: play ``digits`` to the caller as DTMF."""
    clean = digits.upper()
    if not clean or any(c not in _DTMF_CHARS for c in clean):
        raise ValueError("digits must be 0-9, A-D, * or #")
    if len(clean) > MAX_SEND_DTMF:
        raise ValueError(f"at most {MAX_SEND_DTMF} digits per message")
    return json.dumps({"event": "sendDTMF", "streamId": stream_id, "dtmf": clean})


_EXTRA_HEADER_TOKEN = re.compile(r"^[A-Za-z0-9_-]+$")
MAX_STREAM_EXTRA_HEADERS_BYTES = 512


def extra_headers_str(value: str | Mapping[str, str]) -> str:
    """Validate and render a stream's ``extra_headers`` as ``"k=v,k2=v2"``.

    Same rule as the SBC: every key and value is ``[A-Za-z0-9_-]+``, pairs
    separated by ``,`` (or ``;``), at most 512 bytes in total.
    """
    if isinstance(value, str):
        pairs = [p.strip() for p in re.split(r"[,;]", value) if p.strip()]
        items = [p.split("=", 1) if "=" in p else [p, ""] for p in pairs]
        text = value
    else:
        items = [[str(k), str(v)] for k, v in value.items()]
        text = ",".join(f"{k}={v}" for k, v in items)
    for key, val in items:
        if not (
            _EXTRA_HEADER_TOKEN.match(key.strip())
            and _EXTRA_HEADER_TOKEN.match(val.strip())
        ):
            raise ValueError(
                f"extra_headers {key}={val!r}: keys and values must be [A-Za-z0-9_-]"
            )
    if len(text.encode()) > MAX_STREAM_EXTRA_HEADERS_BYTES:
        raise ValueError(
            f"extra_headers exceeds {MAX_STREAM_EXTRA_HEADERS_BYTES} bytes"
        )
    return text


WS_SCHEMES = ("ws://", "wss://")
AUDIO_TRACKS = ("inbound", "outbound", "both")


def stream_options(
    url: str,
    *,
    bidirectional: bool | None = None,
    audio_track: str | None = None,
    content_type: str | None = None,
    stream_timeout: int | None = None,
    status_callback_method: str | None = None,
    extra_headers: str | Mapping[str, str] | None = None,
    noise_cancellation_level: int | None = None,
) -> dict[str, Any]:
    """Validate a stream's options with SuperSBC's rules (``validate_stream``
    in ``answer_url/actions.rs``) and return them as the SBC's snake_case keys,
    ``None`` values dropped. One rule set for ``Response.stream()`` and
    ``calls.start_stream()``; raises ``ValueError``."""
    if not url.startswith(WS_SCHEMES):
        raise ValueError("stream url must start with ws:// or wss://")
    if audio_track is not None and audio_track not in AUDIO_TRACKS:
        raise ValueError(f"audio_track must be one of {AUDIO_TRACKS}")
    if bidirectional and audio_track not in (None, "inbound"):
        raise ValueError('a bidirectional stream supports only audio_track="inbound"')
    if content_type is not None:
        MediaFormat.from_content_type(content_type).validate_supported()
    if stream_timeout is not None and stream_timeout < 1:
        raise ValueError("stream_timeout must be at least 1 second")
    if status_callback_method not in (None, "GET", "POST"):
        raise ValueError('status_callback_method must be "GET" or "POST"')
    if noise_cancellation_level is not None and not (
        60 <= noise_cancellation_level <= 100
    ):
        raise ValueError("noise_cancellation_level must be between 60 and 100")
    options = {
        "bidirectional": bidirectional,
        "audio_track": audio_track,
        "content_type": content_type,
        "stream_timeout": stream_timeout,
        "status_callback_method": status_callback_method,
        "extra_headers": extra_headers_str(extra_headers) if extra_headers else None,
        "noise_cancellation_level": noise_cancellation_level,
    }
    return {k: v for k, v in options.items() if v is not None}


#: The header SuperSBC signs a stream's WebSocket upgrade with.
SIGNATURE_HEADER = "X-Unpod-Signature"


def stream_signature(secret: str | bytes, url: str, timestamp: int, nonce: str) -> str:
    """``hex(HMAC-SHA256(secret, "ws.<t>.<n>.<url>"))``, as SuperSBC signs."""
    key = secret.encode() if isinstance(secret, str) else secret
    payload = f"ws.{timestamp}.{nonce}.{url}".encode()
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def _parse_signature(value: str) -> tuple[int | None, str | None, list[str]]:
    """``t=..,n=..,v1=..[,v1=..]`` -> (t, n, [v1...]). Several ``v1`` appear
    while the org's secret is being rotated."""
    ts: int | None = None
    nonce: str | None = None
    sigs: list[str] = []
    for part in value.split(","):
        key, _, val = part.strip().partition("=")
        if key == "t":
            try:
                ts = int(val)
            except ValueError:
                return None, None, []
        elif key == "n":
            nonce = val
        elif key == "v1" and val:
            sigs.append(val.lower())
    return ts, nonce, sigs


class NonceCache:
    """Remembers the nonces of accepted signatures, so a captured upgrade
    request cannot be replayed even inside the ``tolerance`` window.

    In-memory and per process: share one instance across your handlers. With
    several bot processes, a replay could still land on another process.
    """

    def __init__(self, ttl: float = DEFAULT_TOLERANCE_SECS * 2) -> None:
        self._ttl = ttl
        self._seen: dict[str, float] = {}

    def seen_before(self, nonce: str, now: float | None = None) -> bool:
        """Record ``nonce``; ``True`` when it was already used."""
        current = time.time() if now is None else now
        if len(self._seen) > 10_000:
            self._seen = {n: t for n, t in self._seen.items() if t > current}
        expires = self._seen.get(nonce)
        if expires is not None and expires > current:
            return True
        self._seen[nonce] = current + self._ttl
        return False


def _with_base(url: str, public_base: str | None) -> list[str]:
    """The URL(s) the SBC may have signed. ``public_base`` (``wss://host``)
    replaces the scheme and host the server sees (behind a proxy or tunnel);
    ``ws``/``wss`` are both tried, since TLS often ends at the proxy."""
    scheme, sep, rest = url.partition("://")
    if not sep:
        return [url]
    path = "/" + rest.split("/", 1)[1] if "/" in rest else ""
    if public_base:
        scheme, _, host = public_base.rstrip("/").partition("://")
    else:
        host = rest.split("/", 1)[0]
    out = []
    for sch in (scheme, "wss" if scheme == "ws" else "ws"):
        cand = f"{sch}://{host}{path}"
        if cand not in out:
            out.append(cand)
    return out


def verify_stream_request(
    headers: Mapping[str, str],
    secret: str | bytes,
    *,
    url: str,
    public_base: str | None = None,
    tolerance: int | None = DEFAULT_TOLERANCE_SECS,
    nonce_cache: NonceCache | None = None,
    now: float | None = None,
) -> bool:
    """True when the WebSocket upgrade carries a valid ``X-Unpod-Signature``.

    Call it before ``websocket.accept()`` and close the socket when it returns
    False, so only Unpod's SBC can open a stream to your bot.

    ``secret`` is the org's webhook secret (``client.telephony.webhook_secret``).
    ``url`` is the request URL (``str(websocket.url)``). Behind a proxy or
    tunnel, pass ``public_base="wss://bot.example.com"``: the scheme and host
    are taken from it, never from request headers (which the caller controls).
    Signatures older than ``tolerance`` seconds fail (``None`` skips the age
    check); with a ``nonce_cache`` a repeated signature fails too.
    """
    if not secret:
        return False
    value = _header(headers, SIGNATURE_HEADER)
    if not value:
        return False
    ts, nonce, sigs = _parse_signature(value)
    if ts is None or not nonce or not sigs:
        return False
    current = time.time() if now is None else now
    if tolerance is not None and abs(current - ts) > tolerance:
        return False
    ok = any(
        hmac.compare_digest(stream_signature(secret, cand, ts, nonce), sig)
        for cand in _with_base(url, public_base)
        for sig in sigs
    )
    if ok and nonce_cache is not None and nonce_cache.seen_before(nonce, current):
        return False
    return ok


__all__ = [
    "ALAW",
    "AUDIO_TRACKS",
    "DEFAULT_ENCODING",
    "DEFAULT_SAMPLE_RATE",
    "ENCODINGS",
    "L16",
    "MAX_CHECKPOINT_NAME",
    "MAX_SEND_DTMF",
    "MAX_STREAM_EXTRA_HEADERS_BYTES",
    "MULAW",
    "SAMPLE_RATES",
    "SBC_DEFAULT_CONTENT_TYPE",
    "SIGNATURE_HEADER",
    "WS_SCHEMES",
    "MediaFormat",
    "NonceCache",
    "StreamStart",
    "checkpoint_message",
    "clear_audio_message",
    "decode_message",
    "extra_headers_str",
    "parse_start",
    "play_audio_message",
    "send_dtmf_message",
    "stream_options",
    "stream_signature",
    "verify_stream_request",
]
