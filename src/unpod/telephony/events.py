"""Parse and verify call webhooks: :func:`parse_call_event`.

Call events arrive at ``status_callback_url`` either form-encoded (default) or
as JSON. Field names follow Plivo (``CallUUID``, ``MPCUUID``, ``EventName``), so
this module maps them to snake_case on :class:`CallEvent`.

PROVISIONAL: the contract only says "HMAC signature header". Until the header
name and the signed bytes are final, both can be set per call. The defaults are
HMAC-SHA256 over the raw request body in the ``X-Webhook-Signature`` header,
which is what SuperSBC webhooks use today. Hex and base64 digests are accepted,
with or without a ``sha256=`` prefix.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_SIGNATURE_HEADER = "X-Webhook-Signature"


class WebhookSignatureError(Exception):
    """The webhook signature is missing or does not match the secret."""


class CallEvent(BaseModel):
    """One normalized call or recording event.

    ``call_id`` is the call (``MPCUUID``); ``leg_id`` is the leg (``CallUUID``).
    ``raw`` keeps the fields exactly as received.
    """

    model_config = ConfigDict(extra="allow")

    event: str | None = None
    call_id: str | None = None
    leg_id: str | None = None
    role: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    direction: str | None = None
    call_status: str | None = None
    initiation_time: str | None = None
    hangup_time: str | None = None
    duration: int | None = None
    hangup_cause: str | None = None
    hangup_by: str | None = None
    sip_code: int | None = None
    digit: str | None = None
    play_id: str | None = None
    sequence: int | None = None
    event_id: str | None = None
    recording_url: str | None = None
    recording_duration: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


# Wire name -> CallEvent field.
_FIELD_MAP: dict[str, str] = {
    "EventName": "event",
    "Event": "event",
    "MPCUUID": "call_id",
    "CallUUID": "leg_id",
    "Role": "role",
    "From": "from",
    "To": "to",
    "Direction": "direction",
    "CallStatus": "call_status",
    "InitiationTime": "initiation_time",
    "HangupTime": "hangup_time",
    "Duration": "duration",
    "PlivoHangupCause": "hangup_cause",
    "HangupCause": "hangup_cause",
    "HangupBy": "hangup_by",
    "SipCode": "sip_code",
    "Digit": "digit",
    "PlayId": "play_id",
    "Sequence": "sequence",
    "EventId": "event_id",
    "RecordingURL": "recording_url",
    "RecordUrl": "recording_url",
    "RecordingDuration": "recording_duration",
}

_INT_FIELDS = {"duration", "sip_code", "sequence", "recording_duration"}


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def verify_signature(
    body: bytes,
    signature: str | None,
    secret: str | bytes,
) -> bool:
    """True when ``signature`` is HMAC-SHA256 of ``body`` under ``secret``.

    Constant-time comparison. Accepts hex or base64, optionally ``sha256=``.
    """
    if not signature:
        return False
    key = secret.encode() if isinstance(secret, str) else secret
    digest = hmac.new(key, body, hashlib.sha256).digest()
    sig = signature.strip()
    if sig.lower().startswith("sha256="):
        sig = sig[len("sha256=") :]
    # Compare bytes: compare_digest raises TypeError on non-ASCII str input,
    # and a forged header must fail verification, not crash the handler.
    sig_bytes = sig.encode("utf-8")
    return hmac.compare_digest(sig_bytes.lower(), digest.hex().encode()) or hmac.compare_digest(
        sig_bytes, base64.b64encode(digest)
    )


def _decode(body: bytes, content_type: str | None) -> dict[str, Any]:
    ctype = (content_type or "").lower()
    text = body.decode("utf-8")
    if "json" in ctype or (not ctype and text.lstrip().startswith("{")):
        data = json.loads(text or "{}")
        if not isinstance(data, dict):
            raise ValueError("call event JSON must be an object")
        return data
    return dict(parse_qsl(text, keep_blank_values=True))


def _normalize(raw: dict[str, Any]) -> CallEvent:
    fields: dict[str, Any] = {}
    for wire, value in raw.items():
        name = _FIELD_MAP.get(wire)
        if name is None or value in (None, ""):
            continue
        if name in _INT_FIELDS:
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
        fields.setdefault(name, value)
    meta = raw.get("Metadata")
    if isinstance(meta, str) and meta:
        try:
            meta = json.loads(meta)
        except ValueError:
            meta = {"value": meta}
    if isinstance(meta, dict):
        fields["metadata"] = meta
    return CallEvent(**fields, raw=dict(raw))


def parse_call_event(
    body: bytes | str,
    headers: Mapping[str, str],
    secret: str | bytes | None,
    *,
    signature_header: str = DEFAULT_SIGNATURE_HEADER,
    verify: bool = True,
) -> CallEvent:
    """Verify a call webhook and return it as a :class:`CallEvent`.

    Pass the raw request body exactly as received (before any form or JSON
    parsing) and the request headers. ``Content-Type`` decides form vs JSON.

    Raises :class:`WebhookSignatureError` when the signature is missing or
    wrong. ``verify=False`` skips the check; use it only in local tests.
    """
    raw_body = body.encode("utf-8") if isinstance(body, str) else body
    if verify:
        if not secret:
            raise WebhookSignatureError("a secret is required to verify the webhook")
        if not verify_signature(raw_body, _header(headers, signature_header), secret):
            raise WebhookSignatureError("invalid or missing webhook signature")
    return _normalize(_decode(raw_body, _header(headers, "Content-Type")))


__all__ = [
    "DEFAULT_SIGNATURE_HEADER",
    "CallEvent",
    "WebhookSignatureError",
    "parse_call_event",
    "verify_signature",
]
