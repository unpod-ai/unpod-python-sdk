"""Parse and verify call webhooks: :func:`parse_call_event`.

Call events arrive at ``status_callback_url`` either form-encoded (default) or
as JSON. Field names follow Plivo (``CallUUID``, ``MPCUUID``, ``EventName``), so
this module maps them to snake_case on :class:`CallEvent`.

Each request is signed with your org's webhook secret
(``client.telephony.webhook_secret``)::

    X-Webhook-Signature: t=<unix ts>,v1=<hex HMAC-SHA256(secret, "<ts>.<raw body>")>

:func:`parse_call_event` checks that signature and rejects a timestamp older
than ``tolerance`` seconds to limit replays. Typical use in a FastAPI handler::

    body = await request.body()          # raw bytes, before any parsing
    ev = parse_call_event(body, request.headers, secret=SECRET)
    if ev.event == "Hangup":
        print(ev.call_id, ev.hangup_cause)

Recording events and outbound answer-URL requests are not signed yet; parse
those with ``verify=False``.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_SIGNATURE_HEADER = "X-Webhook-Signature"
DEFAULT_TOLERANCE_SECS = 300


class WebhookSignatureError(Exception):
    """The webhook signature is missing or does not match the secret."""


class EventName:
    """``CallEvent.event`` values (``EventName`` on the wire).

    Constants only: ``CallEvent.event`` stays a plain ``str`` so new events
    from the SBC do not break parsing.
    """

    MPC_INITIALIZED = "MPCInitialized"
    MPC_START = "MPCStart"
    MPC_END = "MPCEnd"
    PARTICIPANT_JOIN = "ParticipantJoin"
    PARTICIPANT_EXIT = "ParticipantExit"
    PARTICIPANT_HOLD = "ParticipantHold"
    PARTICIPANT_UNHOLD = "ParticipantUnhold"
    MPC_RECORDING_PAUSED = "MPCRecordingPaused"
    MPC_RECORDING_RESUMED = "MPCRecordingResumed"
    MPC_RECORDING_COMPLETED = "MPCRecordingCompleted"
    PARTICIPANT_RECORDING_INITIATED = "ParticipantRecordingInitiated"
    PARTICIPANT_RECORDING_COMPLETED = "ParticipantRecordingCompleted"


class DigitsRequest(BaseModel):
    """The action-URL request after a ``<GetDigits>`` / ``<GetInput>`` element.

    ``digits`` may be empty when only ``finishOnKey`` was pressed. ``input_type``
    is ``"dtmf"`` for ``<GetInput>``. ``raw`` keeps every field as received.
    """

    model_config = ConfigDict(extra="allow")

    call_id: str | None = None
    leg_id: str | None = None
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    direction: str | None = None
    digits: str = ""
    input_type: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


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
    start_time: int | None = None
    answer_time: int | None = None
    end_time: int | None = None
    timestamp: int | None = None
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
    record_file: str | None = None
    request_uuid: str | None = None
    bill_duration: int | None = None
    call_mode: str | None = None
    participant_call_uuid: str | None = None
    participant_role: str | None = None
    mpc_end_reason: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    raw: dict[str, Any] = Field(default_factory=dict)


# Wire name -> CallEvent field. Both the Plivo-style form keys and their
# snake_case JSON equivalents (sent with ``status_callback_format="json"``)
# map here, so an event is normalized the same way in either format.
_FIELD_MAP: dict[str, str] = {
    "EventName": "event",
    "event_name": "event",
    "Event": "event",
    "event": "event",
    "MPCUUID": "call_id",
    "call_id": "call_id",
    "CallUUID": "leg_id",
    "leg_id": "leg_id",
    "Role": "role",
    "role": "role",
    "From": "from",
    "from": "from",
    "To": "to",
    "to": "to",
    "Direction": "direction",
    "direction": "direction",
    "CallStatus": "call_status",
    "call_status": "call_status",
    "InitiationTime": "initiation_time",
    "initiation_time": "initiation_time",
    "StartTime": "start_time",
    "start_time": "start_time",
    "AnswerTime": "answer_time",
    "answer_time": "answer_time",
    "EndTime": "end_time",
    "end_time": "end_time",
    "Timestamp": "timestamp",
    "timestamp": "timestamp",
    "HangupTime": "hangup_time",
    "hangup_time": "hangup_time",
    "Duration": "duration",
    "duration": "duration",
    "PlivoHangupCause": "hangup_cause",
    "plivo_hangup_cause": "hangup_cause",
    "HangupCause": "hangup_cause",
    "hangup_cause": "hangup_cause",
    "HangupBy": "hangup_by",
    "hangup_by": "hangup_by",
    "SipCode": "sip_code",
    "sip_code": "sip_code",
    "Digit": "digit",
    "digit": "digit",
    "PlayId": "play_id",
    "play_id": "play_id",
    "Sequence": "sequence",
    "sequence": "sequence",
    "EventId": "event_id",
    "event_id": "event_id",
    "RecordingURL": "recording_url",
    "RecordUrl": "recording_url",
    "recording_url": "recording_url",
    "RecordingDuration": "recording_duration",
    "recording_duration": "recording_duration",
    "RecordFile": "record_file",
    "RequestUUID": "request_uuid",
    "request_uuid": "request_uuid",
    "BillDuration": "bill_duration",
    "bill_duration": "bill_duration",
    "CallMode": "call_mode",
    "call_mode": "call_mode",
    "ParticipantCallUUID": "participant_call_uuid",
    "participant_call_uuid": "participant_call_uuid",
    "ParticipantRole": "participant_role",
    "participant_role": "participant_role",
    "MPCEndReason": "mpc_end_reason",
    "mpc_end_reason": "mpc_end_reason",
}

_INT_FIELDS = {
    "duration",
    "sip_code",
    "sequence",
    "recording_duration",
    "bill_duration",
    "start_time",
    "answer_time",
    "end_time",
    "timestamp",
}


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def _parse_signature_header(value: str) -> tuple[int | None, list[str]]:
    """Split ``t=<ts>,v1=<hex>[,v1=<hex>]`` into the timestamp and digests."""
    ts: int | None = None
    sigs: list[str] = []
    for part in value.split(","):
        key, _, val = part.strip().partition("=")
        if key == "t":
            try:
                ts = int(val)
            except ValueError:
                return None, []
        elif key == "v1" and val:
            sigs.append(val)
    return ts, sigs


def verify_signature(
    body: bytes,
    signature: str | None,
    secret: str | bytes,
    *,
    tolerance: int | None = DEFAULT_TOLERANCE_SECS,
    now: float | None = None,
) -> bool:
    """True when ``signature`` is a valid ``t=..,v1=..`` header for ``body``.

    The signed payload is ``"<t>.<body>"``. Comparison is constant-time. With a
    ``tolerance`` (seconds), a timestamp further than that from ``now`` fails;
    pass ``tolerance=None`` to skip the age check.
    """
    if not signature:
        return False
    ts, sigs = _parse_signature_header(signature)
    if ts is None or not sigs:
        return False
    if tolerance is not None:
        current = time.time() if now is None else now
        if abs(current - ts) > tolerance:
            return False
    key = secret.encode() if isinstance(secret, str) else secret
    expected = hmac.new(key, f"{ts}.".encode() + body, hashlib.sha256).hexdigest().encode()
    # Compare bytes: compare_digest raises TypeError on non-ASCII str input,
    # and a forged header must fail verification, not crash the handler.
    return any(hmac.compare_digest(sig.encode("utf-8").lower(), expected) for sig in sigs)


def _decode(body: bytes, content_type: str | None) -> dict[str, Any]:
    ctype = (content_type or "").lower()
    text = body.decode("utf-8")
    if "json" in ctype or (not ctype and text.lstrip().startswith("{")):
        data = json.loads(text or "{}")
        if not isinstance(data, dict):
            raise ValueError("call event JSON must be an object")
        return data
    return dict(parse_qsl(text, keep_blank_values=True))


_STR_TIME_FIELDS = {"initiation_time", "hangup_time"}


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
        elif name in _STR_TIME_FIELDS and not isinstance(value, str):
            # JSON sends these as numbers (`hangup_time` aliases the numeric
            # `end_time`); the field is `str` because the form encoding is.
            value = str(value)
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
    tolerance: int | None = DEFAULT_TOLERANCE_SECS,
    verify: bool = True,
) -> CallEvent:
    """Verify a call webhook and return it as a :class:`CallEvent`.

    Pass the raw request body exactly as received (before any form or JSON
    parsing) and the request headers. ``Content-Type`` decides form vs JSON.

    Raises :class:`WebhookSignatureError` when the signature is missing, wrong
    or older than ``tolerance`` seconds. ``verify=False`` skips the check; use it only in local tests.
    """
    raw_body = body.encode("utf-8") if isinstance(body, str) else body
    if verify:
        if not secret:
            raise WebhookSignatureError("a secret is required to verify the webhook")
        if not verify_signature(
            raw_body, _header(headers, signature_header), secret, tolerance=tolerance
        ):
            raise WebhookSignatureError("invalid or missing webhook signature")
    return _normalize(_decode(raw_body, _header(headers, "Content-Type")))


def parse_digits_request(
    body: bytes | str,
    headers: Mapping[str, str],
    secret: str | bytes | None,
    *,
    signature_header: str = DEFAULT_SIGNATURE_HEADER,
    tolerance: int | None = DEFAULT_TOLERANCE_SECS,
    verify: bool = True,
) -> DigitsRequest:
    """Verify a ``<GetDigits>`` / ``<GetInput>`` action request and parse it.

    Same signature scheme as :func:`parse_call_event` (signed with the org
    secret over the raw body). Fields: ``CallUUID``, ``MPCUUID``, ``From``,
    ``To``, ``Direction``, ``Digits`` (may be empty), ``InputType``.
    Raises :class:`WebhookSignatureError` on a bad signature.
    """
    raw_body = body.encode("utf-8") if isinstance(body, str) else body
    if verify:
        if not secret:
            raise WebhookSignatureError("a secret is required to verify the request")
        if not verify_signature(
            raw_body, _header(headers, signature_header), secret, tolerance=tolerance
        ):
            raise WebhookSignatureError("invalid or missing request signature")
    raw = _decode(raw_body, _header(headers, "Content-Type"))
    names = {
        "CallUUID": "leg_id",
        "MPCUUID": "call_id",
        "From": "from",
        "To": "to",
        "Direction": "direction",
        "Digits": "digits",
        "InputType": "input_type",
    }
    fields = {names[k]: str(v) for k, v in raw.items() if k in names and v is not None}
    return DigitsRequest(**fields, raw=dict(raw))


__all__ = [
    "DEFAULT_SIGNATURE_HEADER",
    "DEFAULT_TOLERANCE_SECS",
    "CallEvent",
    "DigitsRequest",
    "EventName",
    "WebhookSignatureError",
    "parse_call_event",
    "parse_digits_request",
    "verify_signature",
]
