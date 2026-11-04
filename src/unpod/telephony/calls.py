"""SBC call control: ``client.telephony.calls`` and ``client.telephony.inbound``.

Methods follow the SuperSBC call API contract (UNPO-123, ``telephony-api-openapi.yaml``)
and go through the unpod telephony proxy, which adds the org's SBC credential.
The SDK never talks to the SBC directly and never holds an SBC key. Decisions
D4/D7 plan a direct mode (SDK -> SBC, bypassing the proxy); that mode is out
of scope for this E2E effort (UNPO-183) and not implemented here.

These are SBC calls (legs, participants, audio, transfer, DTMF, recording). They
are not the agent calls at ``client.calls``.

Every route lives in :data:`CALL_API_PATHS` (the UNPO-154 proxy under
``/api/v2/platform/sbc/``). The org must have the call API switched on, or the
proxy answers 403 ``call_api_disabled``. Where SuperSBC has not built a route
yet, the proxy passes SuperSBC's error through.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal
from urllib.parse import quote, urlencode

import httpx
from pydantic import BaseModel, ConfigDict, Field

from unpod.management._http import AsyncHTTPClient, unwrap_data

# ── routes ───────────────────────────────────────────────────────────────────

# Routes of the unpod call-API proxy (backend-core ``unpod/sbc/urls.py``,
# UNPO-154), mounted at ``/api/v2/platform/sbc/``. The proxy is
# path-transparent: ``/sbc/v1/<resource>`` forwards to SuperSBC
# ``/api/v1/<resource>``. Paths here are relative to the platform base
# (``.../api/v2/platform``). The proxy accepts them with or without a
# trailing slash.
#
# Not served by the proxy yet: the ``*_inbound`` routes (SuperSBC has them,
# the proxy read allowlist does not). They follow the same path-transparent
# shape so they work unchanged once the proxy adds them.
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
    "get_recording": "/sbc/v1/calls/{call_id}/recording",
    "set_did_inbound": "/sbc/v1/dids/{number}/inbound",
    "get_did_inbound": "/sbc/v1/dids/{number}/inbound",
    "set_trunk_inbound": "/sbc/v1/trunks/{name}/inbound",
    "get_trunk_inbound": "/sbc/v1/trunks/{name}/inbound",
    "get_webhook_secret": "/sbc/webhook-secret",
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
    """An SBC call with up to two legs (participant A and B)."""

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
    """Inbound call config for a trunk, or a per-DID override of it."""

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
) -> dict[str, Any]:
    """Request body shared by ``create`` and ``add_participant`` (``LegOptions``)."""
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
    """``client.telephony.calls``: create and control SBC calls."""

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
        idempotency_key: str | None = None,
    ) -> TelephonyCall:
        """Dial the first participant (A). Contract ``createCall``.

        ``from_`` is a caller ID the org owns (E.164). ``to`` is an E.164 number
        or a SIP URI. Dialing is asynchronous: the call comes back ``initiated``
        and progress arrives as events at ``status_callback_url``. ``answer_url``
        runs an answer-URL app (play/speak/dial/...) on this leg once it
        connects, instead of just bridging it.
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
        """List active calls. Contract ``listCalls``."""
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
        """One call with per-leg state. Contract ``getCall``."""
        resp = await _call(self._http.get(_path("get_call", call_id=call_id)))
        return TelephonyCall(**unwrap_data(resp))

    async def end(self, call_id: str) -> None:
        """End the whole call (all legs). Contract ``endCall``."""
        await _call(self._http.delete(_path("end_call", call_id=call_id)))

    async def hangup(
        self, call_id: str, *, reason: str | None = None, code: int | None = None
    ) -> None:
        """Hang up with an optional reason and SIP status. Contract ``hangupCall``."""
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
        idempotency_key: str | None = None,
    ) -> Leg:
        """Dial participant B and bridge it on answer. Contract ``addParticipant``.

        A call has at most two participants; a third raises
        :class:`TelephonyAPIError` with status 409. ``answer_url`` runs an
        answer-URL app on this leg once it connects, instead of just bridging it.
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
        """Hang up one participant leg. Contract ``removeParticipant``."""
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
        loop: bool | None = None,
        interrupt_on_dtmf: bool | None = None,
    ) -> PlayResult:
        """Play audio to one leg, or to all legs when ``leg_id`` is omitted.

        Pass exactly one of ``url`` (https, MP3 or WAV) or ``file`` (a path on
        the SBC). Contract ``playAudio``.
        """
        if (url is None) == (file is None):
            raise ValueError("pass exactly one of url= or file=")
        source = {"type": "url", "url": url} if url is not None else {"type": "file", "path": file}
        body: dict[str, Any] = {"source": source}
        if leg_id is not None:
            body["leg_id"] = leg_id
        if loop is not None:
            body["loop"] = loop
        if interrupt_on_dtmf is not None:
            body["interrupt_on_dtmf"] = interrupt_on_dtmf
        resp = await _call(self._http.post(_path("play_audio", call_id=call_id), json=body))
        return PlayResult(**unwrap_data(resp))

    async def stop_audio(
        self, call_id: str, *, leg_id: str | None = None, play_id: str | None = None
    ) -> None:
        """Stop audio on one leg, or on all legs when ``leg_id`` is omitted.

        Contract ``stopAudio``.
        """
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
        """Synthesize ``text`` with the SBC TTS and play it. Contract ``speakAudio``.

        ``voice`` overrides the trunk/DID voice. Raises :class:`TelephonyAPIError`
        with status 503 when TTS is not configured.
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
        """Blind or attended transfer. Contract ``transferCall``.

        For an attended transfer, finish with :meth:`transfer_complete` or
        :meth:`transfer_cancel`.
        """
        body: dict[str, Any] = {"type": type, "target": target}
        if leg_id is not None:
            body["leg"] = leg_id
        await _call(self._http.post(_path("transfer", call_id=call_id), json=body))

    async def transfer_complete(self, call_id: str, consult_leg: str) -> None:
        """Complete an attended transfer. Contract ``transferComplete``."""
        await _call(
            self._http.post(
                _path("transfer_complete", call_id=call_id),
                json={"consult_leg": consult_leg},
            )
        )

    async def transfer_cancel(self, call_id: str, consult_leg: str) -> None:
        """Cancel an attended transfer. Contract ``transferCancel``."""
        await _call(
            self._http.post(
                _path("transfer_cancel", call_id=call_id),
                json={"consult_leg": consult_leg},
            )
        )

    async def send_dtmf(self, call_id: str, digits: str, *, leg_id: str | None = None) -> None:
        """Send DTMF digits (``0-9``, ``A-D``, ``*``, ``#``). Contract ``sendDtmf``."""
        if not digits or any(d not in "0123456789ABCD*#" for d in digits):
            raise ValueError("digits must match ^[0-9A-D*#]+$")
        body: dict[str, Any] = {"digits": digits}
        if leg_id is not None:
            body["leg"] = leg_id
        await _call(self._http.post(_path("send_dtmf", call_id=call_id), json=body))

    async def record(self, call_id: str, *, transcribe: bool | None = None) -> None:
        """Start recording the call. Contract ``recordCall``."""
        body: dict[str, Any] = {}
        if transcribe is not None:
            body["transcribe"] = transcribe
        await _call(self._http.post(_path("record", call_id=call_id), json=body))

    async def get_recording(self, call_id: str) -> Recording:
        """Recording metadata. Contract ``getRecording``.

        Raises :class:`TelephonyAPIError` with status 409 while the recording
        is still being written. Download the WAV from ``Recording.url``.
        """
        resp = await _call(self._http.get(_path("get_recording", call_id=call_id)))
        return Recording(**unwrap_data(resp))


class InboundResource:
    """``client.telephony.inbound``: where inbound calls to a trunk or DID go.

    The trunk config applies to every DID on that trunk; a DID override wins
    field by field.
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
        """Set a trunk's inbound config. Contract ``setTrunkInboundConfig``."""
        resp = await _call(
            self._http.put(_path("set_trunk_inbound", name=name), json=self._body(config))
        )
        return InboundConfig(**(unwrap_data(resp) or {}))

    async def get_trunk(self, name: str) -> InboundConfig:
        """A trunk's inbound config. Contract ``getTrunkInboundConfig``."""
        resp = await _call(self._http.get(_path("get_trunk_inbound", name=name)))
        return InboundConfig(**(unwrap_data(resp) or {}))

    async def set_did(
        self, number: str, config: InboundConfig | dict[str, Any]
    ) -> InboundConfig:
        """Override the trunk config for one DID. Contract ``setDidInboundOverride``."""
        resp = await _call(
            self._http.put(_path("set_did_inbound", number=number), json=self._body(config))
        )
        return InboundConfig(**(unwrap_data(resp) or {}))

    async def get_did(self, number: str) -> InboundConfig:
        """The effective config for a DID (override merged over the trunk).

        Contract ``getDidInboundEffective``.
        """
        resp = await _call(self._http.get(_path("get_did_inbound", number=number)))
        return InboundConfig(**(unwrap_data(resp) or {}))


class WebhookSecret(BaseModel):
    """The org's call-event signing secret.

    ``get`` returns it masked; ``rotate`` returns the new secret in full once.
    """

    model_config = ConfigDict(extra="allow")

    secret: str | None = None
    version: int | None = None
    pushed_to_sbc: bool | None = None


class WebhookSecretResource:
    """``client.telephony.webhook_secret``: the secret that signs call events.

    Pass it to :func:`unpod.telephony.parse_call_event`. Only an org admin can
    rotate it.
    """

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def get(self) -> WebhookSecret:
        """The masked secret and its version."""
        resp = await _call(self._http.get(_path("get_webhook_secret")))
        return WebhookSecret(**(unwrap_data(resp) or {}))

    async def rotate(self) -> WebhookSecret:
        """Issue a new secret version; the full secret is returned only here."""
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
