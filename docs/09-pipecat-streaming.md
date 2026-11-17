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
| `Answer().stream(url)` | `unpod.telephony.answer` | Builds the `answer_url` reply, as SBC JSON (`to_json()`) or XML (`to_xml()`) |
| `verify_stream_request(headers, secret, url=...)` | `unpod.pipecat` | Checks the SBC's `X-Unpod-Signature` on the WebSocket upgrade (age and, with a `NonceCache`, replays), so only Unpod can reach your bot |
| `parse_unpod_websocket(ws)` | `unpod.pipecat` | Reads the `start` event: `call_id`, `stream_id`, `account_id`, `media_format` |
| `UnpodFrameSerializer` | `unpod.pipecat` | Pipecat frames ↔ stream messages. Hangs up through the Unpod call API |
| `STREAM_STARTED` / `STOPPED` / `FAILED` / `DEGRADED` | `unpod.telephony` | Stream status callbacks (`Event=started\|stopped\|failed\|degraded`), parsed by `parse_call_event` (`CallEvent.stream_id`, `.stream_reason`, `.service_url`, `.error`, `.duration`) |
| `client.telephony.calls.start_stream()` / `list_streams()` / `get_stream()` / `stop_stream()` | `unpod.telephony` | Start or stop a stream on a live call over REST (also on calls created with `calls.create`) |
| `checkpoint_message()` / `send_dtmf_message()` | `unpod.telephony.stream` | Bot frames: ask for a playback acknowledgement, send DTMF to the caller |

A full inbound + outbound example lives in
[`examples/pipecat_streaming/`](../examples/pipecat_streaming/).

## Bot side

```python
from unpod import AsyncClient
from unpod.pipecat import (
    UnpodFrameSerializer,
    parse_unpod_websocket,
    verify_stream_request,
)


@app.websocket("/ws")
async def ws(websocket: WebSocket):
    if not verify_stream_request(
        websocket.headers,
        WEBHOOK_SECRET,
        url=str(websocket.url),
        public_base=PUBLIC_WS_BASE,  # e.g. "wss://bot.example.com" behind a proxy/tunnel
        nonce_cache=NONCES,  # one NonceCache() shared by your handlers
    ):
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

## Wire protocol

JSON text frames (`unpod.telephony.stream`):

| Direction | Event | Notes |
|---|---|---|
| SBC → bot | `start` | `callId`, `streamId`, `accountId`, `tracks`, `mediaFormat {encoding, sampleRate}` |
| SBC → bot | `media` | base64 audio in `media.payload`, ~20 ms per frame |
| SBC → bot | `dtmf` | `dtmf.digit` |
| SBC → bot | `playedStream` | `name`: every audio sent before that `checkpoint` has played |
| SBC → bot | `clearedAudio` | the reply to `clearAudio` |
| bot → SBC | `playAudio` | `media {contentType, sampleRate, payload}` |
| bot → SBC | `clearAudio` | barge-in: drop queued playback |
| bot → SBC | `checkpoint` | `name` (≤ 256 chars); answered with `playedStream` |
| bot → SBC | `sendDTMF` | `dtmf`: `0-9`, `A-D`, `*`, `#`, at most 32 queued |

Every SBC frame also carries a top-level `extra_headers` string (`StreamStart.extra_headers`).
The SBC ends a stream by closing the socket (code 1000); there is no `stop` frame.

In Pipecat, `OutputDTMFFrame` / `OutputDTMFUrgentFrame` become `sendDTMF`, and
`playedStream` / `clearedAudio` arrive as `InputTransportMessageFrame`s.

Formats (what the SBC accepts): `audio/x-mulaw` 8 kHz (default), `audio/x-alaw`
8 kHz (carrier trunks are often A-law, so this avoids a transcode), and
`audio/x-l16` (16-bit little-endian) at 8, 16 or 24 kHz.

`stream()` also takes `stream_timeout`, `status_callback_method`,
`extra_headers` (`[A-Za-z0-9_-]` keys and values, ≤ 512 bytes),
`noise_cancellation` and `noise_cancellation_level` (60–100).

## Verifying the stream signature

The SBC signs every stream WebSocket upgrade with the org's webhook secret
(`client.telephony.webhook_secret`) in one header:

```
X-Unpod-Signature: t=<unix seconds>,n=<nonce>,v1=<hex>
v1 = hex(HMAC-SHA256(secret, "ws." + t + "." + n + "." + url))
```

`url` is the URL exactly as the SBC dialled it (`wss://host/path?query`).
`verify_stream_request` checks the HMAC in constant time, rejects a `t` more
than 300 s from now, and with a `NonceCache` rejects a nonce it has seen.
Behind a proxy or tunnel pass `public_base` (your public `wss://host`); the
host is never taken from request headers. While a secret is being rotated the
header can carry two `v1` values; either may match.

Without the SDK (any language): split the header on `,` and `=`, compute the
HMAC above with your secret, compare to each `v1` in constant time, check
`|now - t| <= 300`, and optionally remember `n` for 10 minutes. It is the same
recipe as the webhook `X-Webhook-Signature` (`t=…,v1=…` over `"<t>." + body`),
with the `ws.` prefix so neither can stand in for the other.

## Status callbacks

The SBC posts form fields to `status_callback_url` (or the `answer_url`):
`Event` (`started`, `stopped`, `failed`, `degraded`), `CallUUID`, `StreamID`,
`Timestamp` (ISO 8601, in `CallEvent.event_time`), `From`, `To`, `Direction`,
`ServiceURL`, `ExtraHeaders`, plus `StatusReason` and `Duration` on
`stopped`/`failed` and `Error` on `failed`. `degraded`
(`StatusReason=slow_connection_30`) is a warning: the bot is reading audio too
slowly and the stream keeps running.

## Open points to confirm with the SBC

1. `start.callId` is the call id the call API takes (`calls.hangup(call_id)`).
