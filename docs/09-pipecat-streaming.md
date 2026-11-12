# Pipecat bots on Unpod calls (audio streaming)

> **Preview.** The SDK side is complete and tested against a simulated SBC.
> It needs SuperSBC's `stream` answer action, which is designed
> (`supersbc` design `2026-11-11-sip-websocket-streaming-design.md`) but not
> shipped yet. Until it ships, the SBC rejects a `stream` action as unknown.

Your Pipecat bot keeps its pipeline. Unpod's SBC answers the call, asks your
`answer_url` what to do, and when you reply "stream", it opens a WebSocket to
your bot and streams the call's audio both ways.

```
caller ──SIP── SuperSBC ──(1) answer_url──▶ your server   → Answer().stream("wss://you/ws")
                  │
                  └──(2) wss://you/ws ─────▶ your Pipecat bot
                       start / media / dtmf  ▶
                       ◀ playAudio / clearAudio
```

## Install

```bash
pip install "unpod[pipecat]"
```

## The pieces

| Piece | Where | What it does |
|---|---|---|
| `Answer().stream(url)` | `unpod.telephony.answer` | Builds the `answer_url` reply, as SBC JSON (`to_json()`) or Plivo-style XML (`to_xml()`) |
| `verify_stream_request(headers, secret)` | `unpod.pipecat` | Checks the SBC's `X-Webhook-Signature` on the WebSocket upgrade, so only Unpod can reach your bot |
| `parse_unpod_websocket(ws)` | `unpod.pipecat` | Reads the `start` event: `call_id`, `stream_id`, `account_id`, `media_format` |
| `UnpodFrameSerializer` | `unpod.pipecat` | Pipecat frames ↔ stream messages. Hangs up through the Unpod call API |
| `STREAM_STARTED` / `STOPPED` / `FAILED` | `unpod.telephony` | Stream lifecycle events, parsed by `parse_call_event` (`CallEvent.stream_id`, `.stream_reason`) |

A full inbound + outbound example lives in
[`examples/pipecat_streaming/`](../examples/pipecat_streaming/).

## Bot side

```python
from unpod import AsyncClient
from unpod.pipecat import UnpodFrameSerializer, parse_unpod_websocket, verify_stream_request

@app.websocket("/ws")
async def ws(websocket: WebSocket):
    if not verify_stream_request(websocket.headers, WEBHOOK_SECRET):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    start = await parse_unpod_websocket(websocket)
    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True, audio_out_enabled=True, add_wav_header=False,
            serializer=UnpodFrameSerializer.from_start(start, client=unpod_client),
        ),
    )
    # ... your pipeline, unchanged
```

Run the pipeline at 8 kHz (`PipelineParams(audio_in_sample_rate=8000,
audio_out_sample_rate=8000)`): the stream is μ-law 8 kHz, so no resampling
happens. Other rates work; the serializer resamples.

**Hang up from the bot.** The SBC keeps the call up when the WebSocket closes.
`UnpodFrameSerializer` hangs up through `client.telephony.calls.hangup` on
`EndFrame`/`CancelFrame` (`auto_hang_up=True`, the default), so pass `client=`
(or `hangup=` with your own coroutine). Turn it off with
`params=UnpodFrameSerializer.InputParams(auto_hang_up=False)`.

## Answer side

Inbound: point the number at your answer server once:

```python
await client.telephony.inbound.set_did("+911234567890", answer_url="https://you/answer")
```

Outbound: `calls.create(from_, to, answer_url="https://you/answer")`. The
answer server replies:

```python
from unpod.telephony.answer import Response as Answer

Answer().stream("wss://you/ws").to_xml()
# <Response><Stream bidirectional="true" contentType="audio/x-mulaw;rate=8000">wss://you/ws</Stream></Response>
```

`speak`, `play`, `wait`, `dial` and `hangup` are available too, e.g.
`Answer().speak("Connecting you").stream(url)`.

## Moving from Plivo

| Plivo | Unpod |
|---|---|
| `PLIVO_AUTH_ID` / `PLIVO_AUTH_TOKEN` | `UNPOD_PLATFORM_TOKEN` / `UNPOD_ORG_HANDLE`, plus the webhook secret |
| Console: number → XML application | `client.telephony.inbound.set_did(number, answer_url=...)` |
| `POST /v1/Account/{id}/Call/` | `client.telephony.calls.create(from_, to, answer_url=...)` |
| `plivoxml.ResponseElement().add(StreamElement(...))` | `Answer().stream(url).to_xml()` (same XML shape) |
| `parse_telephony_websocket(ws)` | `parse_unpod_websocket(ws)` |
| `PlivoFrameSerializer(stream_id, call_id, auth_id, auth_token)` | `UnpodFrameSerializer.from_start(start, client=unpod)` |
| (no signature on `/ws`) | `verify_stream_request(ws.headers, secret)` |
| Plivo `statusCallbackUrl` | `StreamStarted` / `StreamStopped` / `StreamFailed` via `parse_call_event` |

The STT/LLM/TTS pipeline, prompts, VAD and transport don't change.

## Wire protocol

JSON text frames, Plivo's shapes (`unpod.telephony.stream`):

| Direction | Event | Notes |
|---|---|---|
| SBC → bot | `start` | `callId`, `streamId`, `accountId`, `tracks`, `mediaFormat {encoding, sampleRate}` |
| SBC → bot | `media` | base64 audio in `media.payload`, ~20 ms per frame |
| SBC → bot | `dtmf` | `dtmf.digit` |
| bot → SBC | `playAudio` | `media {contentType, sampleRate, payload}` |
| bot → SBC | `clearAudio` | barge-in: drop queued playback |

Formats: `audio/x-mulaw` 8 kHz (default, and the only one SuperSBC v1 streams),
`audio/x-l16` (16-bit little-endian) at 8 or 16 kHz.

## Not yet (SuperSBC v1 scope)

- Outbound streaming: the SBC design builds inbound first. `calls.create(answer_url=...)`
  works once the SBC accepts `stream` on REST-originated calls.
- `checkpoint` / `playedStream` (playback acknowledgements).
- `stream` together with `record` or `dial` on the same call.

## Open points to confirm with the SBC

These are SDK-side assumptions; each is one line to change if the SBC differs.

1. `start.callId` is the call id the call API takes (`calls.hangup(call_id)`).
2. The WebSocket upgrade is signed over `"<t>."` with an empty body.
3. `<Stream contentType="audio/x-mulaw;rate=8000">` (Plivo's form) is accepted.
4. Lifecycle callbacks name the stream `StreamID` / `stream_id` and the cause `Reason` / `reason`.
5. `audio/x-l16` is little-endian.
