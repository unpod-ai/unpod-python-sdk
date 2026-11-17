# Pipecat audio streaming on Unpod — SDK design

## Intent

A client running a Pipecat bot wants to use Unpod with minimal changes. Unpod
already covers the call side (`calls.create`, `answer_url`, inbound config).
The missing piece is `<Stream>`: the carrier opening a WebSocket to the bot and
streaming the call's audio.

This is Unpod's own streaming method: our SBC, our SDK, our serializer, using
the JSON event shapes the SBC design specifies.

## Ownership

- **SuperSBC (not this repo):** the `stream` answer action and the WebSocket
  client that dials the bot (`supersbc` `feat/unpo-284-relay-bridge`,
  `docs/superpowers/specs/2026-11-11-sip-websocket-streaming-design.md`).
- **This SDK:** everything the customer's bot and answer server touch.
- **backend-core:** no change. The `stream` action travels from the
  customer's answer server straight to the SBC; the proxy only sees
  `answer_url`, already accepted as an https URL.

## Decisions

| Decision | Choice | Why |
|---|---|---|
| Wire format | JSON event shapes, as the SBC design specifies | SBC already chose it |
| Where the protocol lives | `unpod.telephony.stream` (no Pipecat import) | The answer builder and non-Pipecat bots can use it without pulling Pipecat |
| Pipecat adapter | `unpod.pipecat`, optional extra `unpod[pipecat]` | Keeps Pipecat out of the core install |
| Audio format | Read from `start.mediaFormat`; μ-law 8k default; L16 8k/16k supported | Assuming μ-law 8k garbles anything else |
| Hangup | Through `client.telephony.calls.hangup`, on End/Cancel | SBC keeps the call up when the socket closes |
| Hangup failures | Logged, never raised; 404/409/410 treated as already ended | Must not break pipeline shutdown |
| Auth | `verify_stream_request` over the SBC's `X-Webhook-Signature` on the upgrade | An unsigned `/ws` is open to anyone |
| G.711 | Own lookup tables, byte-identical to Pipecat's reference codec | Same behaviour on every Pipecat 1.x; Pipecat 1.16 deprecated the old helpers |
| Pipecat versions | `pipecat-ai>=1.0`; tested on 1.3.0 and 1.16.0 | supervoice runs 1.3; current is 1.16 |
| Answer builder | `Response` with stream/speak/play/wait/dial/hangup → JSON or XML | XML mirrors `src/proxy/answer_url/xml.rs` |

## Non-goals

- SBC-side implementation.
- `checkpoint`/`playedStream`, outbound-only features, `stream`+`record`/`dial`
  — out of SBC v1 scope.

## Assumptions to confirm with the SBC owner

Listed in `docs/09-pipecat-streaming.md` ("Open points"): call id semantics,
upgrade signature payload, `contentType` with `;rate=`, lifecycle field names,
L16 endianness.

## Verification

`tests/test_pipecat_stream.py`: protocol, handshake, serializer (μ-law and
L16, interruption, DTMF, hangup paths), answer builder (JSON/XML/escaping),
stream events, and an end-to-end run of Pipecat's real
`FastAPIWebsocketTransport` against a simulated, signed SBC connection.
