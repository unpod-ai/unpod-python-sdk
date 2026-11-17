"""Call control (preview): ``client.telephony.calls`` and ``client.telephony.inbound``.

Phone calls you control yourself: dial, bridge two people, play audio, speak
text, transfer, send DTMF, record. These are NOT the AI-agent calls at
``client.calls``.

Words used here:

- call: one conversation (``call_id``), at most two participants
- leg: one participant's phone connection in a call (``leg_id``)
- ``from_``: caller ID, must be one of your org's numbers
- ``to``: an E.164 number or a SIP URI

Example::

    call = await client.telephony.calls.create("+919000000001", "+919811111111",
                                               role="agent")
    await client.telephony.calls.add_participant(call.call_id, "+919000000001",
                                                 "+919822222222", role="customer")
    await client.telephony.calls.speak(call.call_id, "Connecting you now.")
    await client.telephony.calls.end(call.call_id)

The call API is off by default: ask Unpod to switch it on for your org, or every
request raises :class:`TelephonyAPIError` 403 ``call_api_disabled``. Requests go
through Unpod's API (``/api/v2/platform/sbc/...``), which checks that the call
belongs to your org; the SDK never talks to the call server directly.
Guide: ``docs/08-telephony.md``.
"""

from __future__ import annotations

import os
import re
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field

from unpod.management._http import AsyncHTTPClient, unwrap_data

# ── routes ───────────────────────────────────────────────────────────────────

# Every call-control route, relative to the platform base
# (``https://<host>/api/v2/platform``). The server accepts them with or
# without a trailing slash.
CALL_API_PATHS: dict[str, str] = {
    "create_call": "/sbc/v1/calls",
    "list_calls": "/sbc/v1/calls",
    "get_call": "/sbc/v1/calls/{call_id}",
    "end_call": "/sbc/v1/calls/{call_id}",
    "hangup_call": "/sbc/v1/calls/{call_id}/hangup",
    "add_participant": "/sbc/v1/calls/{call_id}/participants",
    "remove_participant": "/sbc/v1/calls/{call_id}/participants/{leg_id}",
    "play_audio": "/sbc/v1/calls/{call_id}/play",
    "stop_audio": "/sbc/v1/calls/{call_id}/play",
    "speak": "/sbc/v1/calls/{call_id}/speak",
    "transfer": "/sbc/v1/calls/{call_id}/transfer",
    "transfer_complete": "/sbc/v1/calls/{call_id}/transfer/complete",
    "transfer_cancel": "/sbc/v1/calls/{call_id}/transfer/cancel",
    "send_dtmf": "/sbc/v1/calls/{call_id}/dtmf",
    "record": "/sbc/v1/calls/{call_id}/record",
    "stop_recording": "/sbc/v1/calls/{call_id}/record",
    "pause_recording": "/sbc/v1/calls/{call_id}/record/pause",
    "resume_recording": "/sbc/v1/calls/{call_id}/record/resume",
    "get_recording": "/sbc/v1/calls/{call_id}/recording",
    "set_did_inbound": "/sbc/v1/dids/{number}/inbound",
    "get_did_inbound": "/sbc/v1/dids/{number}/inbound",
    "set_trunk_inbound": "/sbc/v1/trunks/{name}/inbound",
    "get_trunk_inbound": "/sbc/v1/trunks/{name}/inbound",
    "get_webhook_secret": "/sbc/webhook-secret",
    "reveal_webhook_secret": "/sbc/webhook-secret/reveal",
    "rotate_webhook_secret": "/sbc/webhook-secret/rotate",
}


def _path(op: str, **params: str) -> str:
    """Build the route for ``op``, URL-quoting each path parameter.

    Quoting matters for E.164 numbers (``+``) and SIP-ish trunk names.
    """
    return CALL_API_PATHS[op].format(
        **{k: quote(str(v), safe="") for k, v in params.items()}
    )


# ── errors ───────────────────────────────────────────────────────────────────


class TelephonyAPIError(Exception):
    """An error response from the telephony call API.

    ``code`` is the machine code from the contract's ``{"error", "code"}`` body
    (``bad_request``, ``not_found``, ``conflict``, ``participant_limit``,
    ``not_supported``, ``tts_unavailable``, ``rate_limited`` ...). Branch on it,
    not on ``message``. ``code`` is ``None`` when the body had no code.
    """

    def __init__(
        self, status_code: int, code: str | None, message: str, body: Any = None
    ) -> None:
        super().__init__(f"{status_code} {code or 'error'}: {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.body = body


def _to_api_error(exc: httpx.HTTPStatusError) -> TelephonyAPIError:
    """Map an HTTP error onto :class:`TelephonyAPIError`.

    Accepts the SuperSBC shape ``{"error", "code"}`` (passed through by the
    proxy), the proxy's own ``{"detail": "<code>", "message"}`` (for example
    ``call_api_disabled``, ``from_not_owned``) and the backend-core envelope
    ``{"status_code", "message", "data"}``.
    """
    resp = exc.response
    try:
        body = resp.json()
    except ValueError:
        body = resp.text
    code: str | None = None
    message = resp.reason_phrase or "error"
    if isinstance(body, dict):
        inner = body.get("data") if isinstance(body.get("data"), dict) else {}
        # SuperSBC: {"error", "code"}; the proxy's own: {"detail": code, "message"}.
        detail = body.get("detail") if isinstance(body.get("detail"), str) else None
        code = body.get("code") or inner.get("code") or detail
        message = str(
            body.get("error") or body.get("message") or inner.get("error") or detail or message
        )
    return TelephonyAPIError(resp.status_code, code, message, body)


async def _call(coro: Any) -> Any:
    try:
        return await coro
    except httpx.HTTPStatusError as exc:
        raise _to_api_error(exc) from exc


# ── models ───────────────────────────────────────────────────────────────────
# The contract is a draft, so models accept fields they do not know yet.


class Leg(BaseModel):
    """One participant leg of a call."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    leg_id: str
    call_id: str | None = None
    role: str | None = None
    to: str | None = None
    from_: str | None = Field(default=None, alias="from")
    state: str | None = None  # initiated, ringing, early_media, answered, hungup
    initiated_at: str | None = None
    answered_at: str | None = None
    hangup_at: str | None = None
    hangup_cause: str | None = None
    sip_code: int | None = None


class TelephonyCall(BaseModel):
    """A call you control, with up to two legs (participants)."""

    model_config = ConfigDict(extra="allow")

    call_id: str
    state: str | None = None  # initiated, ringing, talking, ended
    direction: str | None = None
    legs: list[Leg] = Field(default_factory=list)
    metadata: dict[str, str] = Field(default_factory=dict)


class CallList(BaseModel):
    """One page of :meth:`CallsResource.list`."""

    model_config = ConfigDict(extra="allow")

    items: list[TelephonyCall] = Field(default_factory=list)
    page: int | None = None
    page_size: int | None = None
    total: int | None = None


class PlayResult(BaseModel):
    """Result of ``play_audio`` / ``speak``; pass ``play_id`` to ``stop_audio``."""

    model_config = ConfigDict(extra="allow")

    play_id: str | None = None


class Recording(BaseModel):
    """Recording metadata: one two-channel WAV (leg A left, leg B right)."""

    model_config = ConfigDict(extra="allow")

    call_id: str
    available: bool = False
    url: str | None = None
    format: str | None = None
    channels: int | None = None
    duration_secs: int | None = None


class InboundConfig(BaseModel):
    """What happens on an inbound call, for a trunk or for one number.

    ``answer_url`` is your app that answers the call; ``answer_fallback``
    (``"static_routing"`` or ``"reject_503"``) applies when it fails. All URLs
    must be public https.
    """

    model_config = ConfigDict(extra="allow")

    answer_url: str | None = None
    answer_method: Literal["POST", "GET"] | None = None
    answer_timeout_ms: int | None = None
    answer_fallback: Literal["static_routing", "reject_503"] | None = None
    status_callback_url: str | None = None
    status_callback_method: Literal["POST", "GET"] | None = None
    status_callback_format: Literal["form", "json"] | None = None
    recording_callback_url: str | None = None
    record: bool | None = None
    tts_voice: str | None = None
    tts_language: str | None = None


# ── helpers ──────────────────────────────────────────────────────────────────


_STATUS_CALLBACK_EVENTS = ("mpc-state-changes", "participant-state-changes", "leg-state-changes")
_SIP_HEADER_KEY = re.compile(r"^[A-Za-z0-9-]{1,32}$")


def _sip_headers_str(value: str | Mapping[str, str]) -> str:
    """Serialize ``sip_headers`` to the wire form ``"k1=v1,k2=v2"``.

    The SBC adds ``X-PH-<k>`` on the carrier INVITE. Limits: keys
    ``[A-Za-z0-9-]`` up to 32 chars, values up to 256 chars, at most 10. The
    wire form cannot carry ``,`` in a value, so it is rejected here.
    A string is passed through after the same checks.
    """
    if isinstance(value, str):
        pairs = []
        for part in value.split(","):
            k, sep, v = part.partition("=")
            if not sep:
                raise ValueError("sip_headers string must look like 'k1=v1,k2=v2'")
            pairs.append((k, v))
    else:
        pairs = [(str(k), str(v)) for k, v in value.items()]
    if not pairs or len(pairs) > 10:
        raise ValueError("sip_headers needs between 1 and 10 headers")
    for k, v in pairs:
        if not _SIP_HEADER_KEY.match(k):
            raise ValueError(f"invalid sip_headers key {k!r}: use [A-Za-z0-9-], max 32 chars")
        if len(v) > 256 or "," in v:
            raise ValueError(f"invalid sip_headers value for {k!r}: max 256 chars, no commas")
    return ",".join(f"{k}={v}" for k, v in pairs)


def _status_events_str(value: str | Sequence[str]) -> str:
    """Serialize ``status_callback_events`` to a comma list of known event groups."""
    items = [p.strip() for p in value.split(",")] if isinstance(value, str) else list(value)
    if not items:
        raise ValueError("status_callback_events must not be empty")
    for item in items:
        if item not in _STATUS_CALLBACK_EVENTS:
            raise ValueError(
                f"unknown status_callback_events value {item!r}; "
                f"use any of {', '.join(_STATUS_CALLBACK_EVENTS)}"
            )
    return ",".join(items)


def _leg_body(
    from_: str,
    to: str,
    *,
    role: str | None,
    caller_name: str | None,
    friendly_name: str | None,
    ring_timeout: int | None,
    dial_music: str | None,
    status_callback_url: str | None,
    status_callback_method: str | None,
    status_callback_format: str | None,
    recording_callback_url: str | None,
    record: bool | None,
    record_participant_track: bool | None,
    answer_url: str | None,
    answer_method: str | None,
    metadata: dict[str, str] | None,
    time_limit: int | None = None,
    sip_headers: str | Mapping[str, str] | None = None,
    fallback_url: str | None = None,
    fallback_method: str | None = None,
    status_callback_events: str | Sequence[str] | None = None,
    end_mpc_on_exit: bool | None = None,
) -> dict[str, Any]:
    """Request body shared by ``create`` and ``add_participant`` (``LegOptions``)."""
    if time_limit is not None and not 1 <= time_limit <= 86400:
        raise ValueError("time_limit must be between 1 and 86400 seconds")
    if fallback_url is not None and answer_url is None:
        raise ValueError("fallback_url requires answer_url")
    if fallback_method is not None and fallback_url is None:
        raise ValueError("fallback_method requires fallback_url")
    body: dict[str, Any] = {"from": from_, "to": to}
    optional = {
        "role": role,
        "caller_name": caller_name,
        "friendly_name": friendly_name,
        "ring_timeout": ring_timeout,
        "dial_music": dial_music,
        "status_callback_url": status_callback_url,
        "status_callback_method": status_callback_method,
        "status_callback_format": status_callback_format,
        "recording_callback_url": recording_callback_url,
        "record": record,
        "record_participant_track": record_participant_track,
        "answer_url": answer_url,
        "answer_method": answer_method,
        "metadata": metadata,
        "time_limit": time_limit,
        "sip_headers": _sip_headers_str(sip_headers) if sip_headers is not None else None,
        "fallback_url": fallback_url,
        "fallback_method": fallback_method,
        "status_callback_events": (
            _status_events_str(status_callback_events)
            if status_callback_events is not None
            else None
        ),
        "end_mpc_on_exit": end_mpc_on_exit,
    }
    body.update({k: v for k, v in optional.items() if v is not None})
    return body


def _idempotency_headers(key: str | None) -> dict[str, str]:
    """A fresh key per SDK call unless the caller passes one.

    Pass your own key when you retry a request yourself, so the server returns
    the first result instead of dialing a second time.
    """
    return {"Idempotency-Key": key or str(uuid.uuid4())}


def _with_query(path: str, params: dict[str, Any]) -> str:
    query = {k: v for k, v in params.items() if v is not None}
    return f"{path}?{urlencode(query)}" if query else path


# ── resources ────────────────────────────────────────────────────────────────


class CallsResource:
    """``client.telephony.calls``: start and control phone calls yourself."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def create(
        self,
        from_: str,
        to: str,
        *,
        role: str | None = None,
        caller_name: str | None = None,
        friendly_name: str | None = None,
        ring_timeout: int | None = None,
        dial_music: str | None = None,
        status_callback_url: str | None = None,
        status_callback_method: Literal["POST", "GET"] | None = None,
        status_callback_format: Literal["form", "json"] | None = None,
        recording_callback_url: str | None = None,
        record: bool | None = None,
        record_participant_track: bool | None = None,
        answer_url: str | None = None,
        answer_method: Literal["POST", "GET"] | None = None,
        metadata: dict[str, str] | None = None,
        time_limit: int | None = None,
        sip_headers: str | Mapping[str, str] | None = None,
        fallback_url: str | None = None,
        fallback_method: Literal["POST", "GET"] | None = None,
        status_callback_events: str | Sequence[str] | None = None,
        end_mpc_on_exit: bool | None = None,
        idempotency_key: str | None = None,
    ) -> TelephonyCall:
        """Start a call by dialing the first participant.

        ``from_`` is one of your org's numbers (E.164), else 400
        ``from_not_owned``. ``to`` is an E.164 number or a SIP URI. Returns at
        once with ``state="initiated"``; progress (``Ringing``, ``Answered``,
        ``Hangup``) arrives as events at ``status_callback_url``. Add the second
        person with :meth:`add_participant`.

        ``role`` and ``metadata`` are your own labels; they come back on this
        leg's events. ``answer_url`` runs your answer-URL app on this leg once
        it connects. Every URL must be public ``https``. Pass the same
        ``idempotency_key`` when you retry, so the phone is not dialed twice.

        Optional kwargs are sent only when set:

        - ``record``: ``True`` forces recording; ``False`` hides the recording
          (if the global policy made one) from the API, so there are no recording
          webhooks and ``get_recording`` returns 404; omitted lets the policy decide.
        - ``dial_music``: ``None``, ``""`` or ``"real"`` is the normal ringback,
          ``"none"`` is silence, an https URL is custom audio.
        - ``time_limit``: max call length in seconds, 1..86400 (default 14400).
        - ``sip_headers``: a dict or ``"k=v,k=v"``; each is sent to the carrier
          as ``X-PH-<k>`` (keys ``[A-Za-z0-9-]`` up to 32 chars, values up to 256, max 10).
        - ``fallback_url`` / ``fallback_method``: used when ``answer_url`` fails
          (needs ``answer_url``).
        - ``status_callback_events``: a list or comma string of ``mpc-state-changes``,
          ``participant-state-changes``, ``leg-state-changes``; omitted sends all.
        - ``end_mpc_on_exit``: this participant leaving ends the call (default false).
        """
        body = _leg_body(
            from_,
            to,
            role=role,
            caller_name=caller_name,
            friendly_name=friendly_name,
            ring_timeout=ring_timeout,
            dial_music=dial_music,
            status_callback_url=status_callback_url,
            status_callback_method=status_callback_method,
            status_callback_format=status_callback_format,
            recording_callback_url=recording_callback_url,
            record=record,
            record_participant_track=record_participant_track,
            answer_url=answer_url,
            answer_method=answer_method,
            metadata=metadata,
            time_limit=time_limit,
            sip_headers=sip_headers,
            fallback_url=fallback_url,
            fallback_method=fallback_method,
            status_callback_events=status_callback_events,
            end_mpc_on_exit=end_mpc_on_exit,
        )
        resp = await _call(
            self._http.post(
                _path("create_call"),
                json=body,
                headers=_idempotency_headers(idempotency_key),
            )
        )
        return TelephonyCall(**unwrap_data(resp))

    async def list(
        self,
        *,
        page: int | None = None,
        page_size: int | None = None,
        status: Literal["ringing", "talking"] | None = None,
        direction: Literal["inbound", "outbound"] | None = None,
        caller: str | None = None,
        callee: str | None = None,
        number: str | None = None,
        since: str | None = None,
    ) -> CallList:
        """List your org's active calls, one page at a time."""
        params = {
            "page": page,
            "page_size": page_size,
            "status": status,
            "direction": direction,
            "caller": caller,
            "callee": callee,
            "number": number,
            "since": since,
        }
        query = {k: str(v) for k, v in params.items() if v is not None}
        resp = unwrap_data(
            await _call(self._http.get(_path("list_calls"), params=query or None))
        )
        if isinstance(resp, list):
            return CallList(items=[TelephonyCall(**c) for c in resp])
        return CallList(**resp)

    async def get(self, call_id: str) -> TelephonyCall:
        """One call with the state of each leg."""
        resp = await _call(self._http.get(_path("get_call", call_id=call_id)))
        return TelephonyCall(**unwrap_data(resp))

    async def end(self, call_id: str) -> None:
        """End the whole call: every leg hangs up."""
        await _call(self._http.delete(_path("end_call", call_id=call_id)))

    async def hangup(
        self, call_id: str, *, reason: str | None = None, code: int | None = None
    ) -> None:
        """End the call with an optional ``reason`` text and SIP ``code``."""
        body: dict[str, Any] = {}
        if reason is not None:
            body["reason"] = reason
        if code is not None:
            body["code"] = code
        await _call(self._http.post(_path("hangup_call", call_id=call_id), json=body))

    async def add_participant(
        self,
        call_id: str,
        from_: str,
        to: str,
        *,
        role: str | None = None,
        caller_name: str | None = None,
        friendly_name: str | None = None,
        ring_timeout: int | None = None,
        dial_music: str | None = None,
        status_callback_url: str | None = None,
        status_callback_method: Literal["POST", "GET"] | None = None,
        status_callback_format: Literal["form", "json"] | None = None,
        recording_callback_url: str | None = None,
        record: bool | None = None,
        record_participant_track: bool | None = None,
        answer_url: str | None = None,
        answer_method: Literal["POST", "GET"] | None = None,
        metadata: dict[str, str] | None = None,
        time_limit: int | None = None,
        sip_headers: str | Mapping[str, str] | None = None,
        fallback_url: str | None = None,
        fallback_method: Literal["POST", "GET"] | None = None,
        status_callback_events: str | Sequence[str] | None = None,
        end_mpc_on_exit: bool | None = None,
        idempotency_key: str | None = None,
    ) -> Leg:
        """Dial a second person into the call; they are bridged when they answer.

        Takes the same options as :meth:`create`. A call holds at most two
        participants; a third raises :class:`TelephonyAPIError` 409.

        Optional kwargs are sent only when set:

        - ``record``: ``True`` forces recording; ``False`` hides the recording
          (if the global policy made one) from the API, so there are no recording
          webhooks and ``get_recording`` returns 404; omitted lets the policy decide.
        - ``dial_music``: ``None``, ``""`` or ``"real"`` is the normal ringback,
          ``"none"`` is silence, an https URL is custom audio.
        - ``time_limit``: max call length in seconds, 1..86400 (default 14400).
        - ``sip_headers``: a dict or ``"k=v,k=v"``; each is sent to the carrier
          as ``X-PH-<k>`` (keys ``[A-Za-z0-9-]`` up to 32 chars, values up to 256, max 10).
        - ``fallback_url`` / ``fallback_method``: used when ``answer_url`` fails
          (needs ``answer_url``).
        - ``status_callback_events``: a list or comma string of ``mpc-state-changes``,
          ``participant-state-changes``, ``leg-state-changes``; omitted sends all.
        - ``end_mpc_on_exit``: this participant leaving ends the call (default false).
        """
        body = _leg_body(
            from_,
            to,
            role=role,
            caller_name=caller_name,
            friendly_name=friendly_name,
            ring_timeout=ring_timeout,
            dial_music=dial_music,
            status_callback_url=status_callback_url,
            status_callback_method=status_callback_method,
            status_callback_format=status_callback_format,
            recording_callback_url=recording_callback_url,
            record=record,
            record_participant_track=record_participant_track,
            answer_url=answer_url,
            answer_method=answer_method,
            metadata=metadata,
            time_limit=time_limit,
            sip_headers=sip_headers,
            fallback_url=fallback_url,
            fallback_method=fallback_method,
            status_callback_events=status_callback_events,
            end_mpc_on_exit=end_mpc_on_exit,
        )
        resp = await _call(
            self._http.post(
                _path("add_participant", call_id=call_id),
                json=body,
                headers=_idempotency_headers(idempotency_key),
            )
        )
        return Leg(**unwrap_data(resp))

    async def remove_participant(self, call_id: str, leg_id: str) -> None:
        """Hang up one participant (leg); the rest of the call keeps going."""
        await _call(
            self._http.delete(_path("remove_participant", call_id=call_id, leg_id=leg_id))
        )

    async def play_audio(
        self,
        call_id: str,
        *,
        url: str | None = None,
        file: str | None = None,
        leg_id: str | None = None,
        loop: bool | int | None = None,
        length: int | None = None,
        mix: bool | None = None,
        interrupt_on_dtmf: bool | None = None,
    ) -> PlayResult:
        """Play an audio file to one leg, or to every leg when ``leg_id`` is omitted.

        ``url`` is a public https MP3 or WAV. ``file`` (a path on the call
        server) is refused by the hosted API; use ``url``. Returns a
        ``play_id`` you can pass to :meth:`stop_audio`.

        ``loop``: an int ``n`` plays ``n`` times; ``0`` or ``True`` plays until
        stopped. ``length`` stops playback after that many seconds (>= 1).
        ``mix`` is accepted, but mixing is not supported yet: playback replaces
        the call audio.
        """
        if (url is None) == (file is None):
            raise ValueError("pass exactly one of url= or file=")
        if length is not None and length < 1:
            raise ValueError("length must be at least 1 second")
        if loop is not None and not isinstance(loop, bool) and loop < 0:
            raise ValueError("loop must be >= 0")
        source = {"type": "url", "url": url} if url is not None else {"type": "file", "path": file}
        body: dict[str, Any] = {"source": source}
        if leg_id is not None:
            body["leg_id"] = leg_id
        if loop is not None:
            body["loop"] = loop
        if length is not None:
            body["length"] = length
        if mix is not None:
            body["mix"] = mix
        if interrupt_on_dtmf is not None:
            body["interrupt_on_dtmf"] = interrupt_on_dtmf
        resp = await _call(self._http.post(_path("play_audio", call_id=call_id), json=body))
        return PlayResult(**unwrap_data(resp))

    async def stop_audio(
        self, call_id: str, *, leg_id: str | None = None, play_id: str | None = None
    ) -> None:
        """Stop audio on one leg, or on every leg when ``leg_id`` is omitted."""
        path = _with_query(
            _path("stop_audio", call_id=call_id), {"leg_id": leg_id, "play_id": play_id}
        )
        await _call(self._http.delete(path))

    async def speak(
        self,
        call_id: str,
        text: str,
        *,
        voice: str | None = None,
        language: str | None = None,
        leg_id: str | None = None,
        loop: bool | None = None,
    ) -> PlayResult:
        """Speak ``text`` (max 1000 characters) to one leg or every leg.

        ``voice`` overrides the voice set in the number's inbound config.
        Raises :class:`TelephonyAPIError` 503 ``tts_unavailable`` when
        text-to-speech is not configured.
        """
        if len(text) > 1000:
            raise ValueError("text must be at most 1000 characters")
        body: dict[str, Any] = {"text": text}
        if voice is not None:
            body["voice"] = voice
        if language is not None:
            body["language"] = language
        if leg_id is not None:
            body["leg_id"] = leg_id
        if loop is not None:
            body["loop"] = loop
        resp = await _call(self._http.post(_path("speak", call_id=call_id), json=body))
        return PlayResult(**unwrap_data(resp))

    async def transfer(
        self,
        call_id: str,
        target: str,
        *,
        type: Literal["blind", "attended"] = "blind",
        leg_id: str | None = None,
    ) -> None:
        """Transfer the call to ``target``.

        ``type="blind"`` hands the call over at once. ``type="attended"`` first
        connects you to the target; finish with :meth:`transfer_complete` or
        :meth:`transfer_cancel`.
        """
        body: dict[str, Any] = {"type": type, "target": target}
        if leg_id is not None:
            body["leg"] = leg_id
        await _call(self._http.post(_path("transfer", call_id=call_id), json=body))

    async def transfer_complete(self, call_id: str, consult_leg: str) -> None:
        """Finish an attended transfer: connect the caller to the target."""
        await _call(
            self._http.post(
                _path("transfer_complete", call_id=call_id),
                json={"consult_leg": consult_leg},
            )
        )

    async def transfer_cancel(self, call_id: str, consult_leg: str) -> None:
        """Abort an attended transfer and return to the original call."""
        await _call(
            self._http.post(
                _path("transfer_cancel", call_id=call_id),
                json={"consult_leg": consult_leg},
            )
        )

    async def send_dtmf(self, call_id: str, digits: str, *, leg_id: str | None = None) -> None:
        """Send keypad tones: ``0-9``, ``A-D``, ``*``, ``#``."""
        if not digits or any(d not in "0123456789ABCD*#" for d in digits):
            raise ValueError("digits must match ^[0-9A-D*#]+$")
        body: dict[str, Any] = {"digits": digits}
        if leg_id is not None:
            body["leg"] = leg_id
        await _call(self._http.post(_path("send_dtmf", call_id=call_id), json=body))

    async def record(self, call_id: str, *, transcribe: bool | None = None) -> None:
        """Start recording the call. Fetch it later with :meth:`get_recording`.

        Starting while already recording succeeds. After :meth:`stop_recording`
        the server answers 409.
        """
        body: dict[str, Any] = {}
        if transcribe is not None:
            body["transcribe"] = transcribe
        await _call(self._http.post(_path("record", call_id=call_id), json=body))

    async def stop_recording(self, call_id: str) -> None:
        """Stop recording for good (204). 409 when not recording or already stopped."""
        await _call(self._http.delete(_path("stop_recording", call_id=call_id)))

    async def pause_recording(self, call_id: str) -> None:
        """Pause the recording (204). 409 when not recording or already paused."""
        await _call(self._http.post(_path("pause_recording", call_id=call_id), json={}))

    async def resume_recording(self, call_id: str) -> None:
        """Resume a paused recording (204). 409 when not paused or stopped."""
        await _call(self._http.post(_path("resume_recording", call_id=call_id), json={}))

    async def download_recording(self, call_id: str, path: str | os.PathLike[str]) -> Path:
        """Stream the recording WAV to ``path`` with the SDK's auth and return it.

        Written to a temp file next to ``path`` and renamed, so a failed download
        leaves no partial file. Raises :class:`TelephonyAPIError` (404 when the
        call was created with ``record=false``; 409 while still being written).
        """
        dest = Path(path)
        http = self._http
        client = await http._ensure_client()
        url = http._url(_path("get_recording", call_id=call_id))
        headers = {k: v for k, v in http._headers().items() if k.lower() != "content-type"}
        fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=dest.name + ".", suffix=".part")
        try:
            with os.fdopen(fd, "wb") as out:
                async with client.stream(
                    "GET", url, headers=headers, params={"format": "wav"}
                ) as resp:
                    if resp.status_code >= 400:
                        await resp.aread()
                    try:
                        resp.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        raise _to_api_error(exc) from exc
                    async for chunk in resp.aiter_bytes():
                        out.write(chunk)
            os.replace(tmp_name, dest)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
        return dest

    async def get_recording(self, call_id: str) -> Recording:
        """The call's recording: one two-channel WAV at ``Recording.url``.

        Raises :class:`TelephonyAPIError` 409 while the recording is still
        being written; retry a little later.
        """
        resp = await _call(self._http.get(_path("get_recording", call_id=call_id)))
        return Recording(**unwrap_data(resp))


class InboundResource:
    """``client.telephony.inbound``: what happens when someone calls your number.

    Set an :class:`InboundConfig` (answer URL, fallback, callbacks) for one
    number with :meth:`set_did`, or for every number of a trunk with
    :meth:`set_trunk`. A number's own config wins over its trunk's, field by
    field.
    """

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    @staticmethod
    def _body(config: InboundConfig | dict[str, Any]) -> dict[str, Any]:
        if isinstance(config, InboundConfig):
            return config.model_dump(exclude_none=True)
        return InboundConfig(**config).model_dump(exclude_none=True)

    async def set_trunk(
        self, name: str, config: InboundConfig | dict[str, Any]
    ) -> InboundConfig:
        """Set the inbound config for every number on trunk ``name``."""
        resp = await _call(
            self._http.put(_path("set_trunk_inbound", name=name), json=self._body(config))
        )
        return InboundConfig(**(unwrap_data(resp) or {}))

    async def get_trunk(self, name: str) -> InboundConfig:
        """The inbound config of trunk ``name``."""
        resp = await _call(self._http.get(_path("get_trunk_inbound", name=name)))
        return InboundConfig(**(unwrap_data(resp) or {}))

    async def set_did(
        self, number: str, config: InboundConfig | dict[str, Any]
    ) -> InboundConfig:
        """Set the inbound config for one E.164 number (overrides its trunk's)."""
        resp = await _call(
            self._http.put(_path("set_did_inbound", number=number), json=self._body(config))
        )
        return InboundConfig(**(unwrap_data(resp) or {}))

    async def get_did(self, number: str) -> InboundConfig:
        """The config that applies to one number: its own, merged over its trunk's."""
        resp = await _call(self._http.get(_path("get_did_inbound", number=number)))
        return InboundConfig(**(unwrap_data(resp) or {}))


class WebhookSecret(BaseModel):
    """The org's call-event signing secret.

    ``get`` returns it masked; ``reveal`` returns the current secret in full
    (admin only); ``rotate`` issues a new one and returns it in full.
    """

    model_config = ConfigDict(extra="allow")

    secret: str | None = None
    version: int | None = None
    pushed_to_sbc: bool | None = None


class WebhookSecretResource:
    """``client.telephony.webhook_secret``: the secret that signs call events.

    Pass it to :func:`unpod.telephony.parse_call_event`. Only an org admin can
    reveal or rotate it.
    """

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def get(self) -> WebhookSecret:
        """The masked secret and its version."""
        resp = await _call(self._http.get(_path("get_webhook_secret")))
        return WebhookSecret(**(unwrap_data(resp) or {}))

    async def reveal(self) -> WebhookSecret:
        """The current full secret and its version (org admin only; 403 otherwise).

        Use it to recover a secret you lost without rotating, which would break
        every other receiver still verifying with the old one. Treat the value as
        a credential: keep it out of logs and source control.
        """
        resp = await _call(self._http.get(_path("reveal_webhook_secret")))
        return WebhookSecret(**(unwrap_data(resp) or {}))

    async def rotate(self) -> WebhookSecret:
        """Issue a new secret version and return it in full.

        The previous version stops working at once, so update every receiver.
        """
        resp = await _call(self._http.post(_path("rotate_webhook_secret"), json={}))
        return WebhookSecret(**(unwrap_data(resp) or {}))


__all__ = [
    "CALL_API_PATHS",
    "CallList",
    "CallsResource",
    "InboundConfig",
    "InboundResource",
    "Leg",
    "PlayResult",
    "Recording",
    "TelephonyAPIError",
    "TelephonyCall",
    "WebhookSecret",
    "WebhookSecretResource",
]
