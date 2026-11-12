"""Unpod audio streaming for Pipecat: wire protocol, handshake, serializer,
answer builder, stream events, and a real FastAPI transport end to end."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

import pytest

from unpod.telephony import STREAM_STARTED, AnswerResponse, parse_call_event
from unpod.telephony.stream import (
    L16,
    MULAW,
    MediaFormat,
    StreamStart,
    clear_audio_message,
    parse_start,
    play_audio_message,
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


def _sign(ts: int, body: bytes = b"") -> str:
    mac = hmac.new(
        SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={ts},v1={mac}"


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
    def test_defaults_to_mulaw_8k(self):
        assert MediaFormat.parse(None) == MediaFormat(encoding=MULAW, sampleRate=8000)

    def test_reads_start_media_format(self):
        fmt = MediaFormat.parse({"encoding": "audio/x-l16", "sampleRate": 16000})
        assert (fmt.encoding, fmt.sample_rate) == (L16, 16000)

    def test_reads_plivo_content_type(self):
        fmt = MediaFormat.parse("audio/x-l16;rate=16000")
        assert (fmt.encoding, fmt.sample_rate) == (L16, 16000)

    @pytest.mark.parametrize(
        "enc,rate", [("audio/opus", 48000), (MULAW, 16000), (L16, 0)]
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
    def test_valid_signature(self):
        headers = {"x-webhook-signature": _sign(int(time.time()))}
        assert verify_stream_request(headers, SECRET)

    def test_wrong_secret_missing_header_and_stale(self):
        now = int(time.time())
        assert not verify_stream_request({"X-Webhook-Signature": _sign(now)}, "other")
        assert not verify_stream_request({}, SECRET)
        assert not verify_stream_request(
            {"X-Webhook-Signature": _sign(now - 3600)}, SECRET
        )

    def test_empty_secret_never_verifies(self):
        assert not verify_stream_request(
            {"X-Webhook-Signature": _sign(int(time.time()))}, ""
        )


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

    async def test_transport_messages(self):
        ser = UnpodFrameSerializer("s", "c", hangup=_Hangups())
        out = await ser.serialize(
            OutputTransportMessageFrame(message={"event": "checkpoint", "name": "a"})
        )
        assert json.loads(out) == {"event": "checkpoint", "name": "a"}
        rtvi = OutputTransportMessageFrame(message={"label": "rtvi-ai", "type": "x"})
        assert await ser.serialize(rtvi) is None


# ── G.711 ───────────────────────────────────────────────────────────────────


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
                    "content_type": MULAW,
                    "sample_rate": 8000,
                    "status_callback_url": "https://x/cb",
                },
            ]
        }

    def test_stream_xml_is_plivo_shaped_and_escaped(self):
        xml = AnswerResponse().stream("wss://bot/ws?a=1&b=2").to_xml()
        assert xml.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<Response>')
        assert (
            '<Stream bidirectional="true" contentType="audio/x-mulaw;rate=8000">'
            "wss://bot/ws?a=1&amp;b=2</Stream>"
        ) in xml

    def test_other_verbs_xml(self):
        xml = (
            AnswerResponse()
            .play("https://a/b.mp3", loop=2)
            .wait(1)
            .dial("+15550100", caller_id="+15550199", ring_timeout=20)
            .hangup(reason="done")
            .to_xml()
        )
        assert '<Play loop="2">https://a/b.mp3</Play>' in xml
        assert '<Wait length="1"/>' in xml
        assert (
            '<Dial callerId="+15550199" timeout="20"><Number>+15550100</Number></Dial>'
            in xml
        )
        assert '<Hangup reason="done"/>' in xml

    def test_stream_validation(self):
        with pytest.raises(ValueError, match="ws://"):
            AnswerResponse().stream("https://bot/ws")
        with pytest.raises(ValueError):
            AnswerResponse().stream("wss://bot/ws", content_type="audio/opus")


# ── stream lifecycle events ─────────────────────────────────────────────────


def test_stream_event_parses():
    body = b"Event=StreamStarted&MPCUUID=call-1&StreamID=stream-1"
    ev = parse_call_event(
        body, {"Content-Type": "application/x-www-form-urlencoded"}, None, verify=False
    )
    assert (
        ev.event == STREAM_STARTED
        and ev.call_id == "call-1"
        and ev.stream_id == "stream-1"
    )
    ev = parse_call_event(
        json.dumps({"event": "StreamFailed", "stream_id": "s", "reason": "connect"}),
        {"Content-Type": "application/json"},
        None,
        verify=False,
    )
    assert (ev.stream_id, ev.stream_reason) == ("s", "connect")


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
        if not verify_stream_request(websocket.headers, SECRET):
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
    headers = {"X-Webhook-Signature": _sign(int(time.time()))}
    with client.websocket_connect("/ws", headers=headers) as sbc:
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
        if not verify_stream_request(websocket.headers, SECRET):
            await websocket.close(code=1008)
            return
        await websocket.accept()

    with (
        pytest.raises(WebSocketDisconnect) as err,
        TestClient(app).websocket_connect("/ws") as sbc,
    ):
        sbc.receive_text()
    assert err.value.code == 1008
