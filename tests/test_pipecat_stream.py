"""Unpod audio streaming for Pipecat: wire protocol, handshake, serializer,
answer builder, stream events, and a real FastAPI transport end to end."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from unpod.telephony import (
    STREAM_DEGRADED,
    STREAM_EVENTS,
    STREAM_FAILED,
    STREAM_STARTED,
    STREAM_STOPPED,
    AnswerResponse,
    CallEvent,
    parse_call_event,
)
from unpod.telephony.stream import (
    ALAW,
    L16,
    MULAW,
    MediaFormat,
    StreamStart,
    checkpoint_message,
    clear_audio_message,
    parse_start,
    play_audio_message,
    send_dtmf_message,
    verify_stream_request,
)

pipecat = pytest.importorskip("pipecat")
fastapi = pytest.importorskip("fastapi")

from fastapi import WebSocket
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    InputAudioRawFrame,
    InputDTMFFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
)
from pipecat.processors.frame_processor import FrameProcessorSetup

from unpod.pipecat import UnpodFrameSerializer, parse_unpod_websocket

SECRET = "whsec_test"


def _start(**over) -> dict:
    start = {
        "callId": "call-1",
        "streamId": "stream-1",
        "accountId": "org-9",
        "tracks": ["inbound"],
        "mediaFormat": {"encoding": "audio/x-mulaw", "sampleRate": 8000},
    }
    start.update(over)
    return {"event": "start", "sequenceNumber": 1, "start": start}


def _media(payload: bytes, seq: int = 2) -> str:
    return json.dumps(
        {
            "event": "media",
            "sequenceNumber": seq,
            "streamId": "stream-1",
            "media": {
                "track": "inbound",
                "timestamp": "0",
                "chunk": seq,
                "payload": base64.b64encode(payload).decode(),
            },
        }
    )


def _sign(
    url: str,
    ts: int | None = None,
    nonce: str = "a1b2c3d4e5f60718",
    secret: str = SECRET,
) -> dict:
    """The ``X-Unpod-Signature`` header SuperSBC puts on the upgrade
    (``signer.rs`` ``stream_signature_header``), computed independently."""
    ts = int(time.time()) if ts is None else ts
    mac = hmac.new(
        secret.encode(), f"ws.{ts}.{nonce}.{url}".encode(), hashlib.sha256
    ).hexdigest()
    return {"X-Unpod-Signature": f"t={ts},n={nonce},v1={mac}"}


async def _setup(serializer: UnpodFrameSerializer, rate: int = 16000) -> None:
    # FrameProcessorSetup's other fields are irrelevant to a serializer.
    setup = FrameProcessorSetup.__new__(FrameProcessorSetup)
    object.__setattr__(setup, "audio_in_sample_rate", rate)
    await serializer.setup(setup)


class _Hangups:
    def __init__(self, exc: Exception | None = None):
        self.calls: list[str] = []
        self.exc = exc

    async def __call__(self, call_id: str) -> None:
        self.calls.append(call_id)
        if self.exc:
            raise self.exc


# ── protocol ────────────────────────────────────────────────────────────────


class TestMediaFormat:
    def test_missing_format_is_the_sbc_default_l16_8k(self):
        # SBC StreamFormat::DEFAULT_CONTENT_TYPE = "audio/x-l16;rate=8000".
        assert MediaFormat.parse(None) == MediaFormat(encoding=L16, sampleRate=8000)
        assert MediaFormat.parse({}) == MediaFormat(encoding=L16, sampleRate=8000)
        assert MediaFormat.parse({"encoding": MULAW}).encoding == MULAW

    def test_reads_start_media_format(self):
        fmt = MediaFormat.parse({"encoding": "audio/x-l16", "sampleRate": 16000})
        assert (fmt.encoding, fmt.sample_rate) == (L16, 16000)

    def test_reads_content_type_with_rate(self):
        fmt = MediaFormat.parse("audio/x-l16;rate=16000")
        assert (fmt.encoding, fmt.sample_rate) == (L16, 16000)

    @pytest.mark.parametrize(
        "enc,rate",
        [(MULAW, 8000), (ALAW, 8000), (L16, 8000), (L16, 16000), (L16, 24000)],
    )
    def test_accepts_what_the_sbc_accepts(self, enc, rate):
        MediaFormat(encoding=enc, sampleRate=rate).validate_supported()

    @pytest.mark.parametrize(
        "enc,rate",
        [
            ("audio/opus", 48000),
            (MULAW, 16000),
            (ALAW, 16000),
            (L16, 0),
            (L16, 44100),
            (L16, 22050),
        ],
    )
    def test_rejects_unsupported(self, enc, rate):
        with pytest.raises(ValueError):
            MediaFormat(encoding=enc, sampleRate=rate).validate_supported()


class TestParseStart:
    def test_parses_ids_format_and_extras(self):
        start = parse_start(_start(customParameters={"lead": "42"}))
        assert start.call_id == "call-1"
        assert start.stream_id == "stream-1"
        assert start.account_id == "org-9"
        assert start.media_format.encoding == MULAW
        assert start.custom == {"customParameters": {"lead": "42"}}

    def test_accepts_json_text(self):
        assert parse_start(json.dumps(_start())).call_id == "call-1"

    def test_rejects_non_start(self):
        with pytest.raises(ValueError, match="start"):
            parse_start({"event": "media"})

    def test_rejects_missing_ids(self):
        with pytest.raises(ValueError, match="callId"):
            parse_start(_start(callId=None))

    def test_reads_top_level_extra_headers(self):
        msg = _start()
        msg["extra_headers"] = "lead=42,tier=gold"
        assert parse_start(msg).extra_headers == "lead=42,tier=gold"
        assert parse_start(_start()).extra_headers == ""


class TestBotMessages:
    """Frames the bot sends, in the shape SuperSBC's ``BotEvent`` parses."""

    def test_checkpoint(self):
        assert json.loads(checkpoint_message("s1", "greeting")) == {
            "event": "checkpoint",
            "streamId": "s1",
            "name": "greeting",
        }
        with pytest.raises(ValueError):
            checkpoint_message("s1", "")
        with pytest.raises(ValueError):
            checkpoint_message("s1", "x" * 257)

    def test_send_dtmf(self):
        assert json.loads(send_dtmf_message("s1", "12#*ab")) == {
            "event": "sendDTMF",
            "streamId": "s1",
            "dtmf": "12#*AB",
        }
        for bad in ("", "12x", "1" * 33):
            with pytest.raises(ValueError):
                send_dtmf_message("s1", bad)


def test_play_and_clear_messages():
    play = json.loads(play_audio_message("s1", b"\x01\x02", MediaFormat()))
    assert play == {
        "event": "playAudio",
        "streamId": "s1",
        "media": {
            "contentType": MULAW,
            "sampleRate": 8000,
            "payload": base64.b64encode(b"\x01\x02").decode(),
        },
    }
    assert json.loads(clear_audio_message("s1")) == {
        "event": "clearAudio",
        "streamId": "s1",
    }


class TestVerifyStreamRequest:
    URL = "wss://bot.example.com/ws?token=t0k"

    def test_matches_the_sbc_vector(self):
        # Same vector as SuperSBC's signer.rs stream_signature_known_vector.
        from unpod.telephony.stream import stream_signature

        assert (
            stream_signature("s", "wss://h/ws?a=1", 1714060800, "123")
            == "4e160b6c04b3bf13758c9f2f9e5d91011ced0e50892fc0e54f6fc2c0c6acb326"
        )

    def test_valid(self):
        assert verify_stream_request(_sign(self.URL), SECRET, url=self.URL)

    def test_behind_a_proxy_uses_the_configured_public_base(self):
        # TLS ends at the proxy: the server sees ws://localhost but the SBC
        # dialled wss://bot.example.com. The host comes from public_base only.
        h = _sign(self.URL)
        local = "ws://localhost:7860/ws?token=t0k"
        assert verify_stream_request(
            h, SECRET, url=local, public_base="wss://bot.example.com"
        )
        assert not verify_stream_request(h, SECRET, url=local)
        # A Host header naming the signed host no longer helps an attacker.
        assert not verify_stream_request(
            {**h, "host": "bot.example.com"}, SECRET, url=local
        )

    def test_rejects_wrong_secret_url_or_tampering(self):
        h = _sign(self.URL)
        assert not verify_stream_request(h, "other", url=self.URL)
        assert not verify_stream_request(
            h, SECRET, url="wss://bot.example.com/ws?token=other"
        )
        value = h["X-Unpod-Signature"]
        for bad in (
            value.replace("n=a1b2", "n=ffff"),
            value.replace("t=", "t=1"),
            value.split(",v1=")[0],
        ):
            assert not verify_stream_request(
                {"X-Unpod-Signature": bad}, SECRET, url=self.URL
            )

    def test_old_signatures_fail_and_tolerance_can_be_off(self):
        old = _sign(self.URL, ts=int(time.time()) - 3600)
        assert not verify_stream_request(old, SECRET, url=self.URL)
        assert verify_stream_request(old, SECRET, url=self.URL, tolerance=None)

    def test_nonce_cache_rejects_a_replay(self):
        from unpod.telephony.stream import NonceCache

        cache = NonceCache()
        h = _sign(self.URL)
        assert verify_stream_request(h, SECRET, url=self.URL, nonce_cache=cache)
        assert not verify_stream_request(h, SECRET, url=self.URL, nonce_cache=cache)
        # A different nonce is a different request.
        h2 = _sign(self.URL, nonce="0000000000000001")
        assert verify_stream_request(h2, SECRET, url=self.URL, nonce_cache=cache)

    def test_secret_rotation_accepts_either_v1(self):
        h = _sign(self.URL)
        other = _sign(self.URL, secret="old-secret")["X-Unpod-Signature"]
        both = h["X-Unpod-Signature"] + ",v1=" + other.split("v1=")[1]
        assert verify_stream_request({"X-Unpod-Signature": both}, SECRET, url=self.URL)

    def test_webhook_signature_and_plivo_v3_are_not_accepted(self):
        # The old fallback (X-Webhook-Signature over an empty body) let any
        # empty-body webhook signature open a stream; V3 had no timestamp.
        ts = int(time.time())
        webhook = hmac.new(
            SECRET.encode(), f"{ts}.".encode(), hashlib.sha256
        ).hexdigest()
        assert not verify_stream_request(
            {"X-Webhook-Signature": f"t={ts},v1={webhook}"}, SECRET, url=self.URL
        )
        assert not verify_stream_request(
            {"X-Plivo-Signature-V3": "x", "X-Plivo-Signature-V3-Nonce": "1"},
            SECRET,
            url=self.URL,
        )
        assert not verify_stream_request({}, SECRET, url=self.URL)

    def test_empty_secret_never_verifies(self):
        assert not verify_stream_request(_sign(self.URL), "", url=self.URL)


# ── handshake ───────────────────────────────────────────────────────────────


class _FakeWS:
    def __init__(self, frames: list[str]):
        self.frames = list(frames)

    async def receive_text(self) -> str:
        return self.frames.pop(0)


async def test_handshake_skips_leading_frames_and_consumes_start():
    ws = _FakeWS(
        [json.dumps({"event": "connected"}), json.dumps(_start()), _media(b"\xff")]
    )
    start = await parse_unpod_websocket(ws)
    assert isinstance(start, StreamStart) and start.stream_id == "stream-1"
    assert len(ws.frames) == 1  # media left for the transport


async def test_handshake_gives_up_without_start():
    ws = _FakeWS([json.dumps({"event": "x"})] * 3)
    with pytest.raises(ValueError, match="no 'start'"):
        await parse_unpod_websocket(ws)


# ── serializer ──────────────────────────────────────────────────────────────


class TestSerializerConstruction:
    def test_auto_hang_up_needs_client_or_hangup(self):
        with pytest.raises(ValueError, match="auto_hang_up"):
            UnpodFrameSerializer("s", "c")

    def test_auto_hang_up_off_needs_nothing(self):
        UnpodFrameSerializer(
            "s", "c", params=UnpodFrameSerializer.InputParams(auto_hang_up=False)
        )

    def test_rejects_unsupported_format(self):
        with pytest.raises(ValueError, match="unsupported"):
            UnpodFrameSerializer(
                "s",
                "c",
                hangup=_Hangups(),
                media_format=MediaFormat(encoding="audio/opus", sampleRate=48000),
            )

    def test_from_start_carries_format(self):
        start = parse_start(_start(mediaFormat={"encoding": L16, "sampleRate": 16000}))
        ser = UnpodFrameSerializer.from_start(start, hangup=_Hangups())
        assert (ser.stream_id, ser.call_id) == ("stream-1", "call-1")
        assert (
            ser.input_format.encoding == L16 and ser.output_format.sample_rate == 16000
        )


class TestSerializerAudio:
    async def test_mulaw_in_is_pcm_at_pipeline_rate(self):
        ser = UnpodFrameSerializer("stream-1", "call-1", hangup=_Hangups())
        await _setup(ser, 16000)
        # Pipecat 1.3's stream resampler releases ~100 ms at a time, so feed
        # 200 ms of 20 ms chunks and check what comes out.
        frames = [
            await ser.deserialize(_media(b"\x7f" * 160, seq=i)) for i in range(10)
        ]
        frames = [f for f in frames if f is not None]
        assert frames and all(isinstance(f, InputAudioRawFrame) for f in frames)
        assert all(f.sample_rate == 16000 and f.num_channels == 1 for f in frames)

    async def test_outbound_track_is_not_fed_to_the_pipeline(self):
        ser = UnpodFrameSerializer("stream-1", "call-1", hangup=_Hangups())
        await _setup(ser, 8000)
        msg = json.loads(_media(b"\x7f" * 160))
        msg["media"]["track"] = "outbound"
        assert await ser.deserialize(json.dumps(msg)) is None
        assert isinstance(
            await ser.deserialize(_media(b"\x7f" * 160)), InputAudioRawFrame
        )

    async def test_pcm_out_is_mulaw_play_audio(self):
        ser = UnpodFrameSerializer("stream-1", "call-1", hangup=_Hangups())
        await _setup(ser, 8000)
        msg = json.loads(
            await ser.serialize(
                OutputAudioRawFrame(
                    audio=b"\x00\x00" * 160, sample_rate=8000, num_channels=1
                )
            )
        )
        assert msg["event"] == "playAudio" and msg["streamId"] == "stream-1"
        assert (
            msg["media"]["contentType"] == MULAW and msg["media"]["sampleRate"] == 8000
        )
        assert (
            len(base64.b64decode(msg["media"]["payload"])) == 160
        )  # 1 byte per sample

    async def test_alaw_in_and_out(self):
        fmt = MediaFormat(encoding=ALAW, sampleRate=8000)
        ser = UnpodFrameSerializer("s", "c", hangup=_Hangups(), media_format=fmt)
        await _setup(ser, 8000)
        frame = await ser.deserialize(_media(b"\xd5" * 160))  # A-law silence
        assert isinstance(frame, InputAudioRawFrame) and frame.sample_rate == 8000
        assert max(abs(v) for v in memoryview(frame.audio).cast("h")) <= 8
        msg = json.loads(
            await ser.serialize(
                OutputAudioRawFrame(
                    audio=b"\x00\x00" * 160, sample_rate=8000, num_channels=1
                )
            )
        )
        assert msg["media"]["contentType"] == ALAW
        assert base64.b64decode(msg["media"]["payload"]) == b"\xd5" * 160

    async def test_l16_same_rate_is_passthrough(self):
        ser = UnpodFrameSerializer(
            "s",
            "c",
            hangup=_Hangups(),
            media_format=MediaFormat(encoding=L16, sampleRate=16000),
        )
        await _setup(ser, 16000)
        pcm = bytes(range(256)) * 2
        frame = await ser.deserialize(_media(pcm))
        assert frame.audio == pcm
        msg = json.loads(
            await ser.serialize(
                OutputAudioRawFrame(audio=pcm, sample_rate=16000, num_channels=1)
            )
        )
        assert base64.b64decode(msg["media"]["payload"]) == pcm
        assert msg["media"]["contentType"] == L16

    async def test_bad_media_is_dropped(self):
        ser = UnpodFrameSerializer("s", "c", hangup=_Hangups())
        await _setup(ser)
        assert (
            await ser.deserialize(json.dumps({"event": "media", "media": {}})) is None
        )
        assert (
            await ser.deserialize(
                json.dumps({"event": "media", "media": {"payload": "!!"}})
            )
            is None
        )
        assert await ser.deserialize("not json") is None
        assert await ser.deserialize(json.dumps({"event": "stop"})) is None


class TestSerializerControl:
    async def test_interruption_is_clear_audio(self):
        ser = UnpodFrameSerializer("stream-1", "c", hangup=_Hangups())
        assert json.loads(await ser.serialize(InterruptionFrame())) == {
            "event": "clearAudio",
            "streamId": "stream-1",
        }

    async def test_interruption_and_rate_change_reset_output_resampler(self):
        ser = UnpodFrameSerializer("s", "c", hangup=_Hangups())
        await _setup(ser, 16000)
        await ser.serialize(
            OutputAudioRawFrame(
                audio=b"\x01\x00" * 320, sample_rate=16000, num_channels=1
            )
        )
        first = ser._out_resampler
        await ser.serialize(InterruptionFrame())
        assert ser._out_resampler is not first
        second = ser._out_resampler
        await ser.serialize(
            OutputAudioRawFrame(
                audio=b"\x01\x00" * 480, sample_rate=24000, num_channels=1
            )
        )
        assert ser._out_resampler is not second

    async def test_dtmf(self):
        ser = UnpodFrameSerializer("s", "c", hangup=_Hangups())
        frame = await ser.deserialize(
            json.dumps({"event": "dtmf", "dtmf": {"digit": "5"}})
        )
        assert isinstance(frame, InputDTMFFrame) and frame.button.value == "5"
        assert (
            await ser.deserialize(json.dumps({"event": "dtmf", "dtmf": {"digit": "Z"}}))
            is None
        )

    async def test_end_hangs_up_once(self):
        hangups = _Hangups()
        ser = UnpodFrameSerializer("s", "call-7", hangup=hangups)
        assert await ser.serialize(EndFrame()) is None
        assert await ser.serialize(CancelFrame()) is None
        assert hangups.calls == ["call-7"]

    async def test_end_waits_for_queued_audio_then_hangs_up(self, monkeypatch):
        # 0.4 s of mu-law sent just before EndFrame: hang up only after it played.
        import unpod.pipecat.serializer as mod

        slept: list[float] = []

        async def fake_sleep(secs):
            slept.append(secs)

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
        hangups = _Hangups()
        ser = UnpodFrameSerializer("s", "c", hangup=hangups)
        await _setup(ser, 8000)
        for _ in range(20):  # 20 x 20 ms
            await ser.serialize(
                OutputAudioRawFrame(
                    audio=b"\x00\x00" * 160, sample_rate=8000, num_channels=1
                )
            )
        await ser.serialize(EndFrame())
        assert hangups.calls == ["c"]
        assert len(slept) == 1 and 0.3 < slept[0] <= 0.4

    async def test_cancel_and_interruption_do_not_wait(self, monkeypatch):
        import unpod.pipecat.serializer as mod

        slept: list[float] = []

        async def fake_sleep(secs):
            slept.append(secs)

        monkeypatch.setattr(mod.asyncio, "sleep", fake_sleep)
        ser = UnpodFrameSerializer("s", "c", hangup=_Hangups())
        await _setup(ser, 8000)
        frame = OutputAudioRawFrame(
            audio=b"\x00\x00" * 8000, sample_rate=8000, num_channels=1
        )
        await ser.serialize(frame)
        await ser.serialize(CancelFrame())
        assert slept == []
        ser = UnpodFrameSerializer("s", "c", hangup=_Hangups())
        await _setup(ser, 8000)
        await ser.serialize(frame)
        await ser.serialize(InterruptionFrame())  # clearAudio empties the queue
        await ser.serialize(EndFrame())
        assert slept == []

    async def test_no_hangup_when_disabled(self):
        hangups = _Hangups()
        ser = UnpodFrameSerializer(
            "s",
            "c",
            hangup=hangups,
            params=UnpodFrameSerializer.InputParams(auto_hang_up=False),
        )
        await ser.serialize(EndFrame())
        assert hangups.calls == []

    async def test_hangup_failure_does_not_raise(self):
        class Gone(Exception):
            status_code = 404

        for exc in (Gone(), RuntimeError("boom")):
            ser = UnpodFrameSerializer("s", "c", hangup=_Hangups(exc))
            assert await ser.serialize(CancelFrame()) is None

    async def test_client_hangup_uses_call_api(self):
        seen = []

        class Calls:
            async def hangup(self, call_id):
                seen.append(call_id)

        class Client:
            class telephony:
                calls = Calls()

        ser = UnpodFrameSerializer("s", "call-3", client=Client())
        await ser.serialize(EndFrame())
        assert seen == ["call-3"]

    async def test_output_dtmf_becomes_send_dtmf(self):
        from pipecat.audio.dtmf.types import KeypadEntry
        from pipecat.frames.frames import OutputDTMFUrgentFrame

        ser = UnpodFrameSerializer("s", "c", hangup=_Hangups())
        out = await ser.serialize(OutputDTMFUrgentFrame(button=KeypadEntry.POUND))
        assert json.loads(out) == {"event": "sendDTMF", "streamId": "s", "dtmf": "#"}

    async def test_played_and_cleared_surface_as_transport_messages(self):
        from pipecat.frames.frames import InputTransportMessageFrame

        ser = UnpodFrameSerializer("s", "c", hangup=_Hangups())
        played = {
            "event": "playedStream",
            "sequenceNumber": 9,
            "streamId": "s",
            "name": "a",
        }
        frame = await ser.deserialize(json.dumps(played))
        assert isinstance(frame, InputTransportMessageFrame) and frame.message == played
        cleared = {"event": "clearedAudio", "sequenceNumber": 10, "streamId": "s"}
        frame = await ser.deserialize(json.dumps(cleared))
        assert isinstance(frame, InputTransportMessageFrame)

    async def test_transport_messages(self):
        ser = UnpodFrameSerializer("s", "c", hangup=_Hangups())
        out = await ser.serialize(
            OutputTransportMessageFrame(message={"event": "checkpoint", "name": "a"})
        )
        assert json.loads(out) == {"event": "checkpoint", "name": "a"}
        rtvi = OutputTransportMessageFrame(message={"label": "rtvi-ai", "type": "x"})
        assert await ser.serialize(rtvi) is None


# ── G.711 ───────────────────────────────────────────────────────────────────


def test_alaw_matches_the_reference_codec():
    import numpy as np

    from unpod.pipecat.g711 import alaw_to_pcm, pcm_to_alaw

    every_code = bytes(range(256))
    assert pcm_to_alaw(alaw_to_pcm(every_code)) == every_code  # 256/256 round-trip
    assert pcm_to_alaw(b"\x00\x00") == b"\xd5"  # A-law silence
    assert pcm_to_alaw(b"\x00") == b""
    samples = np.arange(-32768, 32768, dtype="<i2").tobytes()
    try:
        import audioop  # stdlib up to Python 3.12
    except ImportError:
        pytest.skip("reference codec (audioop) needs Python <= 3.12")
    assert alaw_to_pcm(every_code) == audioop.alaw2lin(every_code, 2)
    assert pcm_to_alaw(samples) == audioop.lin2alaw(samples, 2)


def test_g711_matches_pipecat_codec_and_round_trips():
    import numpy as np

    from unpod.pipecat.g711 import pcm_to_ulaw, ulaw_to_pcm

    every_code = bytes(range(256))
    pcm = ulaw_to_pcm(every_code)
    assert len(pcm) == 512
    assert pcm_to_ulaw(pcm) == pcm_to_ulaw(ulaw_to_pcm(pcm_to_ulaw(pcm)))  # stable
    samples = np.arange(-32768, 32768, 7, dtype="<i2").tobytes()
    try:
        from pipecat.audio import g711 as ref  # Pipecat >= 1.16
    except ImportError:
        pytest.skip("reference codec needs Pipecat >= 1.16")
    assert ulaw_to_pcm(every_code) == ref.ulaw_to_pcm(every_code)
    assert pcm_to_ulaw(samples) == ref.pcm_to_ulaw(samples)
    assert pcm_to_ulaw(b"\x00") == b""


# ── answer builder ──────────────────────────────────────────────────────────


class TestAnswerResponse:
    def test_stream_json(self):
        resp = (
            AnswerResponse()
            .speak("Connecting you")
            .stream("wss://bot.example.com/ws", status_callback_url="https://x/cb")
        )
        assert resp.to_json() == {
            "actions": [
                {"type": "speak", "text": "Connecting you"},
                {
                    "type": "stream",
                    "url": "wss://bot.example.com/ws",
                    "bidirectional": True,
                    "content_type": "audio/x-mulaw;rate=8000",
                    "keep_call_alive": True,
                    "audio_track": "inbound",
                    "status_callback_url": "https://x/cb",
                },
            ]
        }

    def test_stream_xml_is_well_formed_and_escaped(self):
        xml = AnswerResponse().stream("wss://bot/ws?a=1&b=2").to_xml()
        assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<Response>')
        assert (
            '<Stream bidirectional="true" keepCallAlive="true" audioTrack="inbound" '
            'contentType="audio/x-mulaw;rate=8000">'
            "wss://bot/ws?a=1&amp;b=2</Stream>"
        ) in xml

    def test_stream_l16_rate_rides_on_content_type(self):
        action = (
            AnswerResponse()
            .stream("wss://b/ws", content_type="audio/x-l16", sample_rate=16000)
            .actions[0]
        )
        assert action["content_type"] == "audio/x-l16;rate=16000"
        assert "sample_rate" not in action

    def test_bidirectional_needs_inbound_track(self):
        with pytest.raises(ValueError):
            AnswerResponse().stream("wss://b/ws", audio_track="both")
        AnswerResponse().stream("wss://b/ws", bidirectional=False, audio_track="both")

    def test_other_verbs_xml(self):
        xml = (
            AnswerResponse()
            .play("https://a/b.mp3", loop=True)
            .wait(1)
            .dial("+15550100", caller_id="+15550199", ring_timeout=20)
            .hangup(reason="done")
            .to_xml()
        )
        assert '<Play loop="true">https://a/b.mp3</Play>' in xml
        assert '<Wait length="1"/>' in xml
        assert (
            '<Dial callerId="+15550199" timeout="20"><Number>+15550100</Number></Dial>'
            in xml
        )
        assert '<Hangup reason="done"/>' in xml

    def test_loop_is_a_bool_as_the_sbc_reads_it(self):
        # SBC PlayAction / SpeakAction: `loop: Option<bool>`; XML 0 = loop, 1 = once.
        for given, sent in ((True, True), (False, False), (0, True), (1, False)):
            action = AnswerResponse().play("https://a/b.mp3", loop=given).actions[0]
            assert action["loop"] is sent
            action = AnswerResponse().speak("hi", loop=given).actions[0]
            assert action["loop"] is sent
        with pytest.raises(ValueError, match="repeat count"):
            AnswerResponse().play("https://a/b.mp3", loop=2)

    def test_stream_validation(self):
        with pytest.raises(ValueError, match="ws://"):
            AnswerResponse().stream("https://bot/ws")
        with pytest.raises(ValueError):
            AnswerResponse().stream("wss://bot/ws", content_type="audio/opus")


def test_stream_answer_all_sbc_attributes():
    r = AnswerResponse().stream(
        "wss://bot/ws",
        content_type=ALAW,
        stream_timeout=60,
        extra_headers={"lead": "42"},
        status_callback_url="https://x.test/cb",
        status_callback_method="GET",
        noise_cancellation=True,
        noise_cancellation_level=80,
    )
    xml = r.to_xml()
    for attr in (
        'contentType="audio/x-alaw;rate=8000"',
        'streamTimeout="60"',
        'extraHeaders="lead=42"',
        'statusCallbackMethod="GET"',
        'noiseCancellation="true"',
        'noiseCancellationLevel="80"',
    ):
        assert attr in xml
    # JSON keys are the SBC's StreamAction fields (answer_url/actions.rs).
    action = r.to_json()["actions"][0]
    assert action["stream_timeout"] == 60 and action["extra_headers"] == "lead=42"
    assert action["status_callback_method"] == "GET"
    assert action["noise_cancellation_level"] == 80


@pytest.mark.parametrize(
    "kw",
    [
        {"stream_timeout": 0},
        {"status_callback_method": "PUT"},
        {"noise_cancellation_level": 59},
        {"noise_cancellation_level": 101},
        {"extra_headers": "a b=1"},
        {"extra_headers": {"lead": "4 2"}},
        {"extra_headers": "x" * 600 + "=1"},
        {"content_type": ALAW, "sample_rate": 16000},
    ],
)
def test_stream_answer_rejects_what_the_sbc_rejects(kw):
    with pytest.raises(ValueError):
        AnswerResponse().stream("wss://bot/ws", **kw)


# ── stream lifecycle events ─────────────────────────────────────────────────


# The exact fields the SBC posts (``encode_stream_fields`` in SuperSBC's
# ``src/proxy/webhook/stream_events.rs``; always form-encoded).
_SBC_STREAM_COMMON = {
    "CallUUID": "e01892a9-5cb6-1240-99ac-00163e594ee6-caller",
    "StreamID": "b7e1c9d2-3f4a-4b5c-8d6e-7f8091a2b3c4",
    "Timestamp": "2026-11-16T23:18:04Z",
    "From": "+15550100001",
    "To": "+15550100002",
    "Direction": "inbound",
    "ServiceURL": "wss://bot.example/ws",
    "ExtraHeaders": "a=1",
}


def _form_event(fields: dict) -> CallEvent:
    return parse_call_event(
        urlencode(fields).encode(),
        {"Content-Type": "application/x-www-form-urlencoded"},
        None,
        verify=False,
    )


def test_stream_started_from_the_sbc_wire_format():
    ev = _form_event({**_SBC_STREAM_COMMON, "Event": "started"})
    assert ev.event == STREAM_STARTED and ev.event in STREAM_EVENTS
    assert ev.stream_id == "b7e1c9d2-3f4a-4b5c-8d6e-7f8091a2b3c4"
    assert ev.leg_id == "e01892a9-5cb6-1240-99ac-00163e594ee6-caller"
    assert ev.service_url == "wss://bot.example/ws"
    assert ev.extra_headers == "a=1"
    assert ev.event_time == "2026-11-16T23:18:04Z" and ev.timestamp is None
    assert ev.stream_reason is None


def test_stream_stopped_and_failed_carry_reason_duration_error():
    ev = _form_event(
        {
            **_SBC_STREAM_COMMON,
            "Event": "stopped",
            "StatusReason": "api_request",
            "Duration": "9",
        }
    )
    assert (ev.event, ev.stream_reason, ev.duration) == (
        STREAM_STOPPED,
        "api_request",
        9,
    )
    ev = _form_event(
        {
            **_SBC_STREAM_COMMON,
            "Event": "failed",
            "StatusReason": "connection_failed",
            "Duration": "0",
            "Error": "http 503",
        }
    )
    assert (ev.event, ev.stream_reason, ev.error) == (
        STREAM_FAILED,
        "connection_failed",
        "http 503",
    )
    ev = _form_event(
        {
            **_SBC_STREAM_COMMON,
            "Event": "degraded",
            "StatusReason": "slow_connection_30",
        }
    )
    assert ev.event == STREAM_DEGRADED and ev.stream_reason == "slow_connection_30"


def test_stream_event_json_keys():
    ev = parse_call_event(
        json.dumps(
            {"event": "failed", "stream_id": "s", "status_reason": "connection_failed"}
        ),
        {"Content-Type": "application/json"},
        None,
        verify=False,
    )
    assert (ev.event, ev.stream_id, ev.stream_reason) == (
        "failed",
        "s",
        "connection_failed",
    )


# ── end to end through Pipecat's real FastAPI transport ─────────────────────


def test_fastapi_transport_echo_end_to_end():
    """A fake SBC streams caller audio in; an echo bot plays it back."""
    from fastapi.testclient import TestClient
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.runner import PipelineRunner
    from pipecat.pipeline.task import PipelineParams, PipelineTask
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )

    class Echo(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, InputAudioRawFrame):
                await self.push_frame(
                    OutputAudioRawFrame(
                        audio=frame.audio,
                        sample_rate=frame.sample_rate,
                        num_channels=frame.num_channels,
                    )
                )
            else:
                await self.push_frame(frame, direction)

    hangups = _Hangups()
    app = fastapi.FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        if not verify_stream_request(websocket.headers, SECRET, url=str(websocket.url)):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        start = await parse_unpod_websocket(websocket)
        transport = FastAPIWebsocketTransport(
            websocket=websocket,
            params=FastAPIWebsocketParams(
                audio_in_enabled=True,
                audio_out_enabled=True,
                add_wav_header=False,
                serializer=UnpodFrameSerializer.from_start(start, hangup=hangups),
            ),
        )
        task = PipelineTask(
            Pipeline([transport.input(), Echo(), transport.output()]),
            params=PipelineParams(
                audio_in_sample_rate=8000, audio_out_sample_rate=8000
            ),
        )

        @transport.event_handler("on_client_disconnected")
        async def _gone(transport, client):
            await task.cancel()

        await PipelineRunner(handle_sigint=False).run(task)

    client = TestClient(app)
    # TestClient connects to ws://testserver/ws.
    with client.websocket_connect("/ws", headers=_sign("ws://testserver/ws")) as sbc:
        sbc.send_text(json.dumps(_start()))
        for i in range(10):
            sbc.send_text(_media(b"\x7f" * 160, seq=i + 2))
        reply = json.loads(sbc.receive_text())
    assert reply["event"] == "playAudio" and reply["streamId"] == "stream-1"
    assert reply["media"]["contentType"] == MULAW and base64.b64decode(
        reply["media"]["payload"]
    )


def test_fastapi_transport_rejects_unsigned():
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    app = fastapi.FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        if not verify_stream_request(websocket.headers, SECRET, url=str(websocket.url)):
            await websocket.close(code=1008)
            return
        await websocket.accept()

    with (
        pytest.raises(WebSocketDisconnect) as err,
        TestClient(app).websocket_connect("/ws") as sbc,
    ):
        sbc.receive_text()
    assert err.value.code == 1008
