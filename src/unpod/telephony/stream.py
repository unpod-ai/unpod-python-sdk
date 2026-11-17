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

The SBC signs the WebSocket upgrade request with the org's webhook secret
using a V3 scheme: ``X-Plivo-Signature-V3`` is
``base64(HMAC-SHA256(secret, <request URL with ws(s) -> http(s)> + nonce))`` and
``X-Plivo-Signature-V3-Nonce`` the nonce. :func:`verify_stream_request` checks
it (and the older ``X-Webhook-Signature: t=<ts>,v1=<hex>`` form).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from unpod.telephony.events import (
    DEFAULT_SIGNATURE_HEADER,
    DEFAULT_TOLERANCE_SECS,
    _header,
    verify_signature,
)

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


class MediaFormat(BaseModel):
    """Encoding and sample rate of the audio on one direction of a stream."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    encoding: str = DEFAULT_ENCODING
    sample_rate: int = Field(default=DEFAULT_SAMPLE_RATE, alias="sampleRate")

    @classmethod
    def parse(cls, value: Any) -> MediaFormat:
        """Read ``start.mediaFormat``. ``"audio/x-mulaw;rate=8000"``
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


V3_SIGNATURE_HEADER = "X-Plivo-Signature-V3"
V3_NONCE_HEADER = "X-Plivo-Signature-V3-Nonce"


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


def v3_signature(secret: str | bytes, url: str, nonce: str) -> str:
    """``base64(HMAC-SHA256(secret, url + nonce))``, as SuperSBC signs the upgrade."""
    key = secret.encode() if isinstance(secret, str) else secret
    digest = hmac.new(key, (url + nonce).encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def _signed_url_candidates(url: str, headers: Mapping[str, str]) -> list[str]:
    """The URLs the SBC may have signed: the one it dialled, with ws(s) as http(s).

    Behind a TLS-terminating proxy or tunnel the server sees ``ws://`` while
    the SBC dialled ``wss://``, so both schemes are tried, on the public host
    (``X-Forwarded-Host``/``Host``) as well as the one in ``url``.
    """
    _, sep, rest = url.partition("://")
    if not sep:
        return [url]
    hosts = [rest.split("/", 1)[0]]
    for name in ("x-forwarded-host", "host"):
        value = _header(headers, name)
        if value and value not in hosts:
            hosts.append(value.split(",")[0].strip())
    path = "/" + rest.split("/", 1)[1] if "/" in rest else ""
    out: list[str] = []
    for host in hosts:
        for http_scheme in ("https", "http"):
            cand = f"{http_scheme}://{host}{path}"
            if cand not in out:
                out.append(cand)
    return out


def verify_stream_request(
    headers: Mapping[str, str],
    secret: str | bytes,
    *,
    url: str | None = None,
    body: bytes = b"",
    signature_header: str = DEFAULT_SIGNATURE_HEADER,
    tolerance: int | None = DEFAULT_TOLERANCE_SECS,
) -> bool:
    """True when the WebSocket upgrade request carries a valid SBC signature.

    Call it before ``websocket.accept()`` and close the socket when it returns
    False, so only Unpod's SBC can open a stream to your bot. ``secret`` is the
    org's webhook secret (``client.telephony.webhook_secret``) and ``url`` the
    request URL (``str(websocket.url)``), which the V3 signature covers.

    SuperSBC signs with V3 headers (see the module docs); the older
    ``X-Webhook-Signature`` form (``"<t>."`` + ``body``) is accepted too.
    """
    if not secret:
        return False
    v3 = _header(headers, V3_SIGNATURE_HEADER)
    nonce = _header(headers, V3_NONCE_HEADER)
    if v3 and nonce is not None:
        if not url:
            raise ValueError("verify_stream_request needs url= to check a V3 signature")
        sigs = [x.strip() for x in v3.split(",") if x.strip()]
        for cand in _signed_url_candidates(url, headers):
            want = v3_signature(secret, cand, nonce)
            if any(hmac.compare_digest(want, sig) for sig in sigs):
                return True
        return False
    return verify_signature(
        body, _header(headers, signature_header), secret, tolerance=tolerance
    )


__all__ = [
    "ALAW",
    "DEFAULT_ENCODING",
    "DEFAULT_SAMPLE_RATE",
    "ENCODINGS",
    "L16",
    "MAX_CHECKPOINT_NAME",
    "MAX_SEND_DTMF",
    "MAX_STREAM_EXTRA_HEADERS_BYTES",
    "MULAW",
    "SAMPLE_RATES",
    "V3_NONCE_HEADER",
    "V3_SIGNATURE_HEADER",
    "MediaFormat",
    "StreamStart",
    "checkpoint_message",
    "clear_audio_message",
    "decode_message",
    "extra_headers_str",
    "parse_start",
    "play_audio_message",
    "send_dtmf_message",
    "v3_signature",
    "verify_stream_request",
]
