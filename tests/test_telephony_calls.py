"""Tests for SBC call control: ``client.telephony.calls`` / ``inbound`` and
``parse_call_event``.

The proxy routes are provisional (``CALL_API_PATHS``), so these tests check
request shapes against the OpenAPI contract and look routes up from that table
instead of hard-coding them.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from urllib.parse import urlencode

import httpx
import pytest
from unpod.telephony import (
    CALL_API_PATHS,
    CallEvent,
    InboundConfig,
    Leg,
    TelephonyAPIError,
    TelephonyCall,
    TelephonyNamespace,
    WebhookSignatureError,
    parse_call_event,
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
    await ns.calls.transfer("c1", "+915", type="attended", leg_id="l1")
    assert http.calls[0][2] == {"type": "attended", "target": "+915", "leg_id": "l1"}
    await ns.calls.transfer_complete("c1", "l9")
    await ns.calls.transfer_cancel("c1", "l9")
    assert [c[1] for c in http.calls[1:3]] == [
        _p("transfer_complete", call_id="c1"),
        _p("transfer_cancel", call_id="c1"),
    ]
    await ns.calls.send_dtmf("c1", "12#", leg_id="l1")
    assert http.calls[3][2] == {"digits": "12#", "leg_id": "l1"}
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


def _sign(body: bytes, b64: bool = False) -> str:
    digest = hmac.new(SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode() if b64 else digest.hex()


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


def test_parse_json_event_base64_signature_custom_header():
    body = json.dumps({**FORM, "Duration": 17}).encode()
    ev = parse_call_event(
        body.decode(),
        {"Content-Type": "application/json", "X-Sig": "sha256=" + _sign(body, b64=True)},
        SECRET,
        signature_header="X-Sig",
    )
    assert ev.duration == 17 and ev.event_id == "e4"


def test_parse_recording_event():
    body = urlencode(
        {"EventName": "RecordingCompleted", "MPCUUID": "c1", "RecordingURL": "https://r/x.wav", "RecordingDuration": "30"}
    ).encode()
    ev = parse_call_event(body, {"X-Webhook-Signature": _sign(body).upper()}, SECRET)
    assert ev.event == "RecordingCompleted" and ev.recording_duration == 30


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
