"""Tests for SBC call control: ``client.telephony.calls`` / ``inbound`` and
``parse_call_event``.

Request shapes follow the OpenAPI contract; routes are looked up from
``CALL_API_PATHS`` (the UNPO-154 proxy) except where a test pins them.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import httpx
import pytest

from unpod.telephony import (
    CALL_API_PATHS,
    CallEvent,
    DigitsRequest,
    EventName,
    InboundConfig,
    Leg,
    TelephonyAPIError,
    TelephonyCall,
    TelephonyNamespace,
    WebhookSignatureError,
    parse_call_event,
    parse_digits_request,
    verify_signature,
)


def _p(op: str, **kw: str) -> str:
    return CALL_API_PATHS[op].format(**kw)


def _env(data):
    return {"status_code": 200, "message": "ok", "data": data}


class _FakeHTTP:
    """Records (method, path, json, headers); returns canned responses."""

    def __init__(self, responses: dict | None = None, error: Exception | None = None) -> None:
        self.responses = responses or {}
        self.error = error
        self.calls: list[tuple] = []

    def _reply(self, method, path):
        if self.error:
            raise self.error
        return self.responses.get((method, path), {})

    async def get(self, path, params=None):
        self.calls.append(("GET", path, params, None))
        return self._reply("GET", path)

    async def post(self, path, json=None, headers=None, timeout=None):
        self.calls.append(("POST", path, json, headers))
        return self._reply("POST", path)

    async def put(self, path, json=None):
        self.calls.append(("PUT", path, json, None))
        return self._reply("PUT", path)

    async def delete(self, path):
        self.calls.append(("DELETE", path, None, None))
        if self.error:
            raise self.error


CALL = {
    "call_id": "c1",
    "state": "initiated",
    "direction": "outbound",
    "legs": [{"leg_id": "l1", "call_id": "c1", "state": "initiated", "from": "+911", "to": "+912"}],
    "metadata": {"ticket": "42"},
}


@pytest.mark.anyio
async def test_create_call_body_and_idempotency_key():
    http = _FakeHTTP({("POST", _p("create_call")): CALL})
    ns = TelephonyNamespace(http)
    call = await ns.calls.create(
        "+911",
        "+912",
        role="agent",
        ring_timeout=20,
        status_callback_url="https://example.test/cb?x=1",
        status_callback_format="json",
        record=True,
        metadata={"ticket": "42"},
    )
    assert isinstance(call, TelephonyCall)
    assert call.legs[0].from_ == "+911"
    method, path, body, headers = http.calls[0]
    assert (method, path) == ("POST", _p("create_call"))
    assert body == {
        "from": "+911",
        "to": "+912",
        "role": "agent",
        "ring_timeout": 20,
        "status_callback_url": "https://example.test/cb?x=1",
        "status_callback_format": "json",
        "record": True,
        "metadata": {"ticket": "42"},
    }
    assert headers["Idempotency-Key"]


@pytest.mark.anyio
async def test_create_call_answer_url():
    http = _FakeHTTP({("POST", _p("create_call")): CALL})
    await TelephonyNamespace(http).calls.create(
        "+911", "+912", answer_url="https://example.test/answer", answer_method="GET"
    )
    body = http.calls[0][2]
    assert body["answer_url"] == "https://example.test/answer"
    assert body["answer_method"] == "GET"


@pytest.mark.anyio
async def test_create_call_keeps_caller_idempotency_key_and_unwraps_envelope():
    http = _FakeHTTP({("POST", _p("create_call")): _env(CALL)})
    call = await TelephonyNamespace(http).calls.create("+911", "+912", idempotency_key="k1")
    assert call.call_id == "c1"
    assert http.calls[0][3] == {"Idempotency-Key": "k1"}


@pytest.mark.anyio
async def test_fresh_idempotency_key_per_call():
    http = _FakeHTTP({("POST", _p("create_call")): CALL})
    ns = TelephonyNamespace(http)
    await ns.calls.create("+911", "+912")
    await ns.calls.create("+911", "+912")
    assert http.calls[0][3]["Idempotency-Key"] != http.calls[1][3]["Idempotency-Key"]


@pytest.mark.anyio
async def test_add_and_remove_participant():
    leg = {"leg_id": "l2", "call_id": "c1", "state": "initiated", "role": "customer"}
    http = _FakeHTTP({("POST", _p("add_participant", call_id="c1")): leg})
    ns = TelephonyNamespace(http)
    res = await ns.calls.add_participant("c1", "+911", "+913", role="customer", dial_music="none")
    assert isinstance(res, Leg) and res.leg_id == "l2"
    _, _, body, headers = http.calls[0]
    assert body == {"from": "+911", "to": "+913", "role": "customer", "dial_music": "none"}
    assert "Idempotency-Key" in headers

    await ns.calls.remove_participant("c1", "l2")
    assert http.calls[1][:2] == ("DELETE", _p("remove_participant", call_id="c1", leg_id="l2"))


@pytest.mark.anyio
async def test_participant_limit_maps_to_api_error():
    req = httpx.Request("POST", "https://x.test/")
    resp = httpx.Response(
        409, request=req, json={"error": "max 2 participants", "code": "participant_limit"}
    )
    http = _FakeHTTP(error=httpx.HTTPStatusError("409", request=req, response=resp))
    with pytest.raises(TelephonyAPIError) as exc:
        await TelephonyNamespace(http).calls.add_participant("c1", "+911", "+914")
    assert exc.value.status_code == 409
    assert exc.value.code == "participant_limit"
    assert exc.value.message == "max 2 participants"


@pytest.mark.anyio
async def test_api_error_from_backend_envelope():
    req = httpx.Request("GET", "https://x.test/")
    resp = httpx.Response(404, request=req, json={"status_code": 404, "message": "not found"})
    http = _FakeHTTP(error=httpx.HTTPStatusError("404", request=req, response=resp))
    with pytest.raises(TelephonyAPIError) as exc:
        await TelephonyNamespace(http).calls.get("nope")
    assert exc.value.status_code == 404 and exc.value.message == "not found"


@pytest.mark.anyio
async def test_list_get_end_hangup():
    page = {"items": [CALL], "page": 1, "page_size": 50, "total": 1}
    http = _FakeHTTP(
        {
            ("GET", _p("list_calls")): page,
            ("GET", _p("get_call", call_id="c1")): CALL,
        }
    )
    ns = TelephonyNamespace(http)
    res = await ns.calls.list(status="talking", page=1)
    assert res.total == 1 and res.items[0].call_id == "c1"
    assert http.calls[0][2] == {"status": "talking", "page": "1"}
    assert (await ns.calls.get("c1")).metadata == {"ticket": "42"}
    await ns.calls.end("c1")
    assert http.calls[2][:2] == ("DELETE", _p("end_call", call_id="c1"))
    await ns.calls.hangup("c1", reason="done", code=486)
    assert http.calls[3][2] == {"reason": "done", "code": 486}


@pytest.mark.anyio
async def test_play_stop_speak():
    http = _FakeHTTP(
        {
            ("POST", _p("play_audio", call_id="c1")): {"play_id": "p1"},
            ("POST", _p("speak", call_id="c1")): {"play_id": "p2"},
        }
    )
    ns = TelephonyNamespace(http)
    res = await ns.calls.play_audio("c1", url="https://example.test/a.mp3", leg_id="l1", loop=True)
    assert res.play_id == "p1"
    assert http.calls[0][2] == {
        "source": {"type": "url", "url": "https://example.test/a.mp3"},
        "leg_id": "l1",
        "loop": True,
    }
    await ns.calls.stop_audio("c1", leg_id="l1", play_id="p1")
    assert http.calls[1][:2] == ("DELETE", _p("stop_audio", call_id="c1") + "?leg_id=l1&play_id=p1")
    await ns.calls.stop_audio("c1")
    assert http.calls[2][1] == _p("stop_audio", call_id="c1")
    spoken = await ns.calls.speak("c1", "hello", voice="v1", language="en-IN")
    assert spoken.play_id == "p2"
    assert http.calls[3][2] == {"text": "hello", "voice": "v1", "language": "en-IN"}


@pytest.mark.anyio
async def test_play_audio_needs_exactly_one_source():
    ns = TelephonyNamespace(_FakeHTTP())
    with pytest.raises(ValueError):
        await ns.calls.play_audio("c1")
    with pytest.raises(ValueError):
        await ns.calls.play_audio("c1", url="https://a", file="/b.wav")
    with pytest.raises(ValueError):
        await ns.calls.speak("c1", "x" * 1001)


@pytest.mark.anyio
async def test_transfer_dtmf_record_recording():
    rec = {"call_id": "c1", "available": True, "url": "https://r/x.wav", "channels": 2}
    http = _FakeHTTP({("GET", _p("get_recording", call_id="c1")): rec})
    ns = TelephonyNamespace(http)
    # The SBC's TransferRequest/DtmfRequest take `leg`, not `leg_id`
    # (deny_unknown_fields rejects the latter) — UNPO-183.
    await ns.calls.transfer("c1", "+915", type="attended", leg_id="l1")
    assert http.calls[0][2] == {"type": "attended", "target": "+915", "leg": "l1"}
    await ns.calls.transfer_complete("c1", "l9")
    await ns.calls.transfer_cancel("c1", "l9")
    assert [c[1] for c in http.calls[1:3]] == [
        _p("transfer_complete", call_id="c1"),
        _p("transfer_cancel", call_id="c1"),
    ]
    await ns.calls.send_dtmf("c1", "12#", leg_id="l1")
    assert http.calls[3][2] == {"digits": "12#", "leg": "l1"}
    with pytest.raises(ValueError):
        await ns.calls.send_dtmf("c1", "12x")
    await ns.calls.record("c1", transcribe=True)
    assert http.calls[4][2] == {"transcribe": True}
    recording = await ns.calls.get_recording("c1")
    assert recording.available and recording.channels == 2


@pytest.mark.anyio
async def test_inbound_config_trunk_and_did():
    http = _FakeHTTP(
        {
            ("GET", _p("get_did_inbound", number="%2B9180")): {
                "answer_url": "https://a.test/answer",
                "tts_voice": "v1",
                "source": {"answer_url": "trunk"},
            }
        }
    )
    ns = TelephonyNamespace(http)
    await ns.inbound.set_trunk(
        "qa_trunk", InboundConfig(answer_url="https://a.test/answer", answer_fallback="reject_503")
    )
    assert http.calls[0][:3] == (
        "PUT",
        _p("set_trunk_inbound", name="qa_trunk"),
        {"answer_url": "https://a.test/answer", "answer_fallback": "reject_503"},
    )
    await ns.inbound.set_did("+9180", {"tts_language": "hi-IN"})
    assert http.calls[1][1] == _p("set_did_inbound", number="%2B9180")
    assert http.calls[1][2] == {"tts_language": "hi-IN"}
    eff = await ns.inbound.get_did("+9180")
    assert eff.tts_voice == "v1"
    assert eff.model_extra == {"source": {"answer_url": "trunk"}}
    await ns.inbound.get_trunk("qa_trunk")
    assert http.calls[3][1] == _p("get_trunk_inbound", name="qa_trunk")


def test_sync_client_exposes_calls_and_inbound():
    from unpod.client import _SyncTelephonyNamespace

    sync = _SyncTelephonyNamespace(TelephonyNamespace(_FakeHTTP({("POST", _p("create_call")): CALL})))
    assert sync.calls.create("+911", "+912").call_id == "c1"
    assert callable(sync.inbound.get_trunk)


@pytest.mark.anyio
async def test_proxy_detail_error_maps_to_code():
    req = httpx.Request("POST", "https://x.test/")
    resp = httpx.Response(403, request=req, json={"detail": "call_api_disabled"})
    http = _FakeHTTP(error=httpx.HTTPStatusError("403", request=req, response=resp))
    with pytest.raises(TelephonyAPIError) as exc:
        await TelephonyNamespace(http).calls.create("+911", "+912")
    assert exc.value.code == "call_api_disabled" and exc.value.status_code == 403


def test_paths_target_the_sbc_proxy():
    assert CALL_API_PATHS["create_call"] == "/sbc/v1/calls"
    assert CALL_API_PATHS["remove_participant"] == "/sbc/v1/calls/{call_id}/participants/{leg_id}"
    assert all(p.startswith("/sbc/") for p in CALL_API_PATHS.values())


@pytest.mark.anyio
async def test_webhook_secret_get_and_rotate():
    http = _FakeHTTP(
        {
            ("GET", _p("get_webhook_secret")): {"secret": "ab…yz", "version": 1},
            ("POST", _p("rotate_webhook_secret")): {"secret": "full", "version": 2, "pushed_to_sbc": False},
        }
    )
    ns = TelephonyNamespace(http)
    assert (await ns.webhook_secret.get()).version == 1
    rotated = await ns.webhook_secret.rotate()
    assert rotated.secret == "full" and rotated.pushed_to_sbc is False


# ── parse_call_event ─────────────────────────────────────────────────────────

SECRET = "s3cret"
FORM = {
    "EventName": "Hangup",
    "CallUUID": "l1",
    "MPCUUID": "c1",
    "Role": "customer",
    "From": "+911",
    "To": "+912",
    "Duration": "17",
    "PlivoHangupCause": "Busy Line",
    "SipCode": "486",
    "HangupBy": "callee",
    "Sequence": "4",
    "EventId": "e4",
    "Metadata": json.dumps({"ticket": "42"}),
}


def _sign(body: bytes, ts: int | None = None, secret: str = SECRET) -> str:
    ts = int(time.time()) if ts is None else ts
    digest = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={digest}"


def test_parse_form_event():
    body = urlencode(FORM).encode()
    ev = parse_call_event(
        body,
        {"content-type": "application/x-www-form-urlencoded", "X-Webhook-Signature": _sign(body)},
        SECRET,
    )
    assert isinstance(ev, CallEvent)
    assert (ev.event, ev.call_id, ev.leg_id, ev.role) == ("Hangup", "c1", "l1", "customer")
    assert (ev.duration, ev.sip_code, ev.sequence) == (17, 486, 4)
    assert ev.hangup_cause == "Busy Line" and ev.from_ == "+911"
    assert ev.metadata == {"ticket": "42"}
    assert ev.raw["MPCUUID"] == "c1"


def test_parse_json_event_custom_header():
    body = json.dumps({**FORM, "Duration": 17}).encode()
    ev = parse_call_event(
        body.decode(),
        {"Content-Type": "application/json", "X-Sig": _sign(body)},
        SECRET,
        signature_header="X-Sig",
    )
    assert ev.duration == 17 and ev.event_id == "e4"


def test_parse_json_event_snake_case_wire_format():
    # The SBC's actual `status_callback_format="json"` body (`encode_body` in
    # `leg_delivery.rs`) is snake_case, not the Plivo form names — UNPO-183.
    snake = {
        "call_id": "c1",
        "leg_id": "l1",
        "role": "customer",
        "event": "Hangup",
        "event_id": "e4",
        "sequence": 4,
        "timestamp": 1700000000,
        "start_time": 1699999990,
        "answer_time": 1699999995,
        "end_time": 1700000010,
        "hangup_cause": "Busy Line",
        "sip_code": 486,
        "hangup_by": "callee",
        "from": "+911",
        "to": "+912",
        "direction": "outbound",
        "event_name": "Hangup",
        "hangup_time": 1700000010,
        "duration": 20,
        "plivo_hangup_cause": "Busy Line",
    }
    body = json.dumps(snake).encode()
    ev = parse_call_event(body, {"X-Webhook-Signature": _sign(body)}, SECRET)
    assert (ev.event, ev.call_id, ev.leg_id, ev.role) == ("Hangup", "c1", "l1", "customer")
    assert (ev.duration, ev.sip_code, ev.sequence) == (20, 486, 4)
    assert ev.hangup_cause == "Busy Line"
    assert (ev.start_time, ev.answer_time, ev.end_time, ev.timestamp) == (
        1699999990,
        1699999995,
        1700000010,
        1700000000,
    )
    assert ev.hangup_time == "1700000010"


def test_signature_matches_supersbc_signer():
    # Same inputs as SuperSBC's signer: HMAC-SHA256(secret, "<ts>.<body>"), hex.
    body = b'{"EventName":"Ringing"}'
    digest = hmac.new(b"k", b'1700000000.{"EventName":"Ringing"}', hashlib.sha256).hexdigest()
    header = f"t=1700000000,v1={digest}"
    assert verify_signature(body, header, "k", now=1700000010)
    assert not verify_signature(body, header, "k", now=1700000000 + 301)
    assert verify_signature(body, header, "k", tolerance=None, now=0)
    assert not verify_signature(body, header, "other", now=1700000010)


def test_stale_signature_rejected():
    body = urlencode(FORM).encode()
    old = _sign(body, ts=int(time.time()) - 3600)
    with pytest.raises(WebhookSignatureError):
        parse_call_event(body, {"X-Webhook-Signature": old}, SECRET)
    assert parse_call_event(body, {"X-Webhook-Signature": old}, SECRET, tolerance=None).call_id == "c1"


def test_parse_recording_event():
    body = urlencode(
        {"EventName": "RecordingCompleted", "MPCUUID": "c1", "RecordingURL": "https://r/x.wav", "RecordingDuration": "30"}
    ).encode()
    ev = parse_call_event(body, {"X-Webhook-Signature": _sign(body)}, SECRET)
    assert ev.event == "RecordingCompleted" and ev.recording_duration == 30


def test_parse_record_stop_event_record_file():
    # `Event=RecordStop` (answer_url hook.rs) sends both RecordFile (raw SBC
    # path) and RecordUrl (the API route) — UNPO-183/UNPO-181.
    body = urlencode(
        {
            "Event": "RecordStop",
            "MPCUUID": "c1",
            "RecordFile": "/var/lib/supersbc/recordings/c1.wav",
            "RecordUrl": "/api/v1/calls/c1/recording",
            "RecordingDuration": "30",
        }
    ).encode()
    ev = parse_call_event(body, {"X-Webhook-Signature": _sign(body)}, SECRET)
    assert ev.record_file == "/var/lib/supersbc/recordings/c1.wav"
    assert ev.recording_url == "/api/v1/calls/c1/recording"


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Webhook-Signature": "deadbeef"},
        {"X-Webhook-Signature": "sigéé"},
    ],
)
def test_bad_signature_rejected(headers):
    with pytest.raises(WebhookSignatureError):
        parse_call_event(urlencode(FORM).encode(), headers, SECRET)


def test_tampered_body_rejected():
    body = urlencode(FORM).encode()
    sig = _sign(body)
    with pytest.raises(WebhookSignatureError):
        parse_call_event(body.replace(b"Busy", b"Fine"), {"X-Webhook-Signature": sig}, SECRET)


def test_missing_secret_rejected_unless_verify_off():
    body = urlencode(FORM).encode()
    with pytest.raises(WebhookSignatureError):
        parse_call_event(body, {}, None)
    assert parse_call_event(body, {}, None, verify=False).call_id == "c1"


# ── UNPO-493: new call params, recording control, play options, events ───────

@pytest.mark.anyio
async def test_new_kwargs_absent_when_unset():
    http = _FakeHTTP({("POST", _p("create_call")): CALL})
    await TelephonyNamespace(http).calls.create("+911", "+912")
    assert http.calls[0][2] == {"from": "+911", "to": "+912"}


@pytest.mark.anyio
@pytest.mark.parametrize("target", ["create", "add_participant"])
async def test_new_kwargs_serialized(target):
    http = _FakeHTTP(
        {("POST", _p("create_call")): CALL, ("POST", _p("add_participant", call_id="c1")): CALL["legs"][0]}
    )
    calls = TelephonyNamespace(http).calls
    kw = {
        "time_limit": 600,
        "sip_headers": {"Tenant": "acme", "Ref-Id": "42"},
        "answer_url": "https://e.test/a",
        "fallback_url": "https://e.test/f",
        "fallback_method": "GET",
        "status_callback_events": ["mpc-state-changes", "leg-state-changes"],
        "end_mpc_on_exit": True,
    }
    if target == "create":
        await calls.create("+911", "+912", **kw)
    else:
        await calls.add_participant("c1", "+911", "+912", **kw)
    body = http.calls[0][2]
    assert body["time_limit"] == 600
    assert body["sip_headers"] == "Tenant=acme,Ref-Id=42"
    assert body["fallback_url"] == "https://e.test/f" and body["fallback_method"] == "GET"
    assert body["status_callback_events"] == "mpc-state-changes,leg-state-changes"
    assert body["end_mpc_on_exit"] is True


@pytest.mark.anyio
async def test_string_forms_and_false_flag_sent():
    http = _FakeHTTP({("POST", _p("create_call")): CALL})
    await TelephonyNamespace(http).calls.create(
        "+911", "+912", sip_headers="A=1,B=2", status_callback_events="mpc-state-changes", end_mpc_on_exit=False, record=False
    )
    body = http.calls[0][2]
    assert body["sip_headers"] == "A=1,B=2"
    assert body["end_mpc_on_exit"] is False and body["record"] is False


@pytest.mark.anyio
@pytest.mark.parametrize(
    "kw",
    [
        {"time_limit": 0},
        {"time_limit": 86401},
        {"sip_headers": {"bad key": "v"}},
        {"sip_headers": {"k": "a,b"}},
        {"sip_headers": {"k": "x" * 257}},
        {"sip_headers": {f"k{i}": "v" for i in range(11)}},
        {"sip_headers": "novalue"},
        {"fallback_url": "https://e.test/f"},
        {"status_callback_events": ["nope"]},
    ],
)
async def test_new_kwargs_validation(kw):
    http = _FakeHTTP({("POST", _p("create_call")): CALL})
    with pytest.raises(ValueError):
        await TelephonyNamespace(http).calls.create("+911", "+912", **kw)
    assert http.calls == []


@pytest.mark.anyio
async def test_recording_control_paths_and_409():
    http = _FakeHTTP()
    calls = TelephonyNamespace(http).calls
    await calls.stop_recording("c1")
    await calls.pause_recording("c1")
    await calls.resume_recording("c1")
    assert [(m, p) for m, p, *_ in http.calls] == [
        ("DELETE", "/sbc/v1/calls/c1/record"),
        ("POST", "/sbc/v1/calls/c1/record/pause"),
        ("POST", "/sbc/v1/calls/c1/record/resume"),
    ]
    req = httpx.Request("POST", "https://x.test")
    resp = httpx.Response(409, request=req, json={"error": "not recording", "code": "conflict"})
    bad = _FakeHTTP(error=httpx.HTTPStatusError("409", request=req, response=resp))
    with pytest.raises(TelephonyAPIError) as exc:
        await TelephonyNamespace(bad).calls.pause_recording("c1")
    assert exc.value.status_code == 409 and exc.value.code == "conflict"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "loop,expected", [(True, True), (False, False), (0, 0), (1, 1), (3, 3)]
)
async def test_play_audio_loop_length_mix(loop, expected):
    http = _FakeHTTP({("POST", _p("play_audio", call_id="c1")): {"play_id": "p"}})
    await TelephonyNamespace(http).calls.play_audio(
        "c1", url="https://e.test/a.wav", loop=loop, length=5, mix=True
    )
    body = http.calls[0][2]
    assert body["loop"] == expected and type(body["loop"]) is type(expected)
    assert body["length"] == 5 and body["mix"] is True


@pytest.mark.anyio
async def test_play_audio_validation():
    calls = TelephonyNamespace(_FakeHTTP()).calls
    with pytest.raises(ValueError):
        await calls.play_audio("c1", url="https://e.test/a.wav", length=0)
    with pytest.raises(ValueError):
        await calls.play_audio("c1", url="https://e.test/a.wav", loop=-1)


@pytest.mark.anyio
async def test_webhook_secret_reveal():
    http = _FakeHTTP(
        {
            ("GET", _p("reveal_webhook_secret")): _env({"secret": "whsec_full", "version": 3}),
            ("GET", _p("get_webhook_secret")): {"secret": "wh…ll", "version": 3},
        }
    )
    ns = TelephonyNamespace(http)
    full = await ns.webhook_secret.reveal()
    assert full.secret == "whsec_full" and full.version == 3
    assert http.calls[0][:2] == ("GET", "/sbc/webhook-secret/reveal")
    assert (await ns.webhook_secret.get()).secret == "wh…ll"


@pytest.mark.anyio
async def test_webhook_secret_reveal_forbidden():
    req = httpx.Request("GET", "https://x.test")
    resp = httpx.Response(403, request=req, json={"detail": "forbidden", "message": "admin only"})
    http = _FakeHTTP(error=httpx.HTTPStatusError("403", request=req, response=resp))
    with pytest.raises(TelephonyAPIError) as exc:
        await TelephonyNamespace(http).webhook_secret.reveal()
    assert exc.value.status_code == 403


def _signed(body: bytes, ctype: str):
    ts = int(time.time())
    sig = hmac.new(SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return {"X-Webhook-Signature": f"t={ts},v1={sig}", "Content-Type": ctype}


def test_call_event_new_fields_form_and_json():
    form = {
        "EventName": "ParticipantExit",
        "MPCUUID": "m1",
        "RequestUUID": "r1",
        "BillDuration": "7",
        "CallMode": "mpc",
        "ParticipantCallUUID": "pcu1",
        "ParticipantRole": "agent",
        "MPCEndReason": "participant-exit",
    }
    body = urlencode(form).encode()
    ev = parse_call_event(body, _signed(body, "application/x-www-form-urlencoded"), SECRET)
    js = {
        "event_name": "MPCEnd",
        "call_id": "m1",
        "request_uuid": "r1",
        "bill_duration": 7,
        "call_mode": "mpc",
        "participant_call_uuid": "pcu1",
        "participant_role": "agent",
        "mpc_end_reason": "participant-exit",
    }
    jbody = json.dumps(js).encode()
    ev2 = parse_call_event(jbody, _signed(jbody, "application/json"), SECRET)
    for e in (ev, ev2):
        assert (e.request_uuid, e.bill_duration, e.call_mode) == ("r1", 7, "mpc")
        assert (e.participant_call_uuid, e.participant_role) == ("pcu1", "agent")
        assert e.mpc_end_reason == "participant-exit"
        assert e.leg_id is None
    assert ev.event == EventName.PARTICIPANT_EXIT and ev2.event == EventName.MPC_END


def test_parse_digits_request():
    form = {
        "CallUUID": "l1", "MPCUUID": "m1", "From": "+911", "To": "+912",
        "Direction": "outbound", "Digits": "", "InputType": "dtmf",
    }
    body = urlencode(form).encode()
    req = parse_digits_request(body, _signed(body, "application/x-www-form-urlencoded"), SECRET)
    assert isinstance(req, DigitsRequest)
    assert (req.call_id, req.leg_id, req.from_, req.to) == ("m1", "l1", "+911", "+912")
    assert req.digits == "" and req.input_type == "dtmf" and req.direction == "outbound"
    body2 = urlencode({**form, "Digits": "123#"}).encode()
    assert parse_digits_request(body2, _signed(body2, "application/x-www-form-urlencoded"), SECRET).digits == "123#"
    with pytest.raises(WebhookSignatureError):
        parse_digits_request(body, {"X-Webhook-Signature": "t=1,v1=00"}, SECRET)


class _StreamHTTP:
    """Minimal AsyncHTTPClient stand-in backed by an httpx MockTransport."""

    def __init__(self, handler):
        self._client = httpx.AsyncClient(base_url="https://x.test", transport=httpx.MockTransport(handler))

    async def _ensure_client(self):
        return self._client

    def _url(self, path):
        return path

    def _headers(self):
        return {"Authorization": "Bearer t", "Content-Type": "application/json"}


@pytest.mark.anyio
async def test_download_recording_streams_to_file(tmp_path):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, content=b"RIFF....WAVE")

    dest = tmp_path / "rec.wav"
    out = await TelephonyNamespace(_StreamHTTP(handler)).calls.download_recording("c1", dest)
    assert out == dest and dest.read_bytes() == b"RIFF....WAVE"
    assert seen["url"].endswith("/sbc/v1/calls/c1/recording?format=wav") and seen["auth"] == "Bearer t"
    assert [p.name for p in tmp_path.iterdir()] == ["rec.wav"]


@pytest.mark.anyio
async def test_download_recording_404_leaves_no_file(tmp_path):
    def handler(request):
        return httpx.Response(404, json={"error": "created with record=false", "code": "not_found"})

    dest = tmp_path / "rec.wav"
    with pytest.raises(TelephonyAPIError) as exc:
        await TelephonyNamespace(_StreamHTTP(handler)).calls.download_recording("c1", dest)
    assert exc.value.status_code == 404 and "record=false" in exc.value.message
    assert list(tmp_path.iterdir()) == []
