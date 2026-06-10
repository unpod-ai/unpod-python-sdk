# Conversation State — Single Source of Truth

**Date:** 2026-06-10
**Status:** Design approved, ready for implementation
**Scope:** `super/supervoice` (speech service) + `unpod-sdk` (SDK + playground)

## Problem

The playground's conversation state — *is the user speaking, is the agent
thinking, is the agent speaking, or idle* — is not tracked from any real signal
and has no single source of truth, so the pipeline animation, the status pills,
and the waveforms desync.

Today the true state lives in **supervoice's pipecat pipeline** as discrete
frames (`UserStartedSpeakingFrame`, `TranscriptionFrame`, `BotStartedSpeakingFrame`,
`BotStoppedSpeakingFrame`, `InterruptionFrame`), but **none of those transitions
cross the bridge** — `AgentBridgeProcessor` only uses them for `CallMetrics`.
What reaches the browser side-channel is coarse and terminal: `user_turn` /
`agent_turn` (final text), `interruption`, `metric`. The frontend then
**synthesizes** state in three independent places:

1. `PipelinePanel` phase — a **timer-based** machine (`pipeAt(550/700/2200/4000…)`).
2. `StatusPanel` "Agent" pill — `speaking = botLevel > 0.04`.
3. Waveforms / tab-live dot — ad-hoc reads.

There is **no "thinking" state at all**, and real event logs show
`user.interrupted → agent.text.delta → Bot started speaking → disconnect`
interleaving within ~4s — which timers cannot track.

## Decision summary

| Decision | Choice |
| --- | --- |
| Source of truth | **Backend-authoritative** — supervoice computes state from real frames |
| Authority | **supervoice `AgentBridgeProcessor`** (it sees every relevant frame) |
| State set | **5 states**: `idle · listening · thinking · speaking · interrupted` |
| Delivery to UI | **Both**: down the WS transport (UI's source) **and** upstream as an SDK hook (agent observability) |
| Negotiation gate (bridge sink) | Keep it (mirror `metric`); the WS sink needs no relay/negotiation |

The single authority is supervoice's `_conv_state`; it has **two emit sinks**
fed from the same transition, so they cannot disagree in value.

## Architecture & data flow

```
supervoice AgentBridgeProcessor            ← THE authority (state machine over pipecat frames)
  │  _emit_state(state) on each transition (coalesced)
  ├── sink A: MessageFrame{type:"state",…} ──► /ws/audio ──► Browser transport ──► convState   (UI source of truth)
  └── sink B: StateEvent{state,…} upstream ──► relay ──► worker ──► Session.on("state")
                                                                      └─► harness bus.publish ─► /playground/events
                                                                          (shown in EVENTS log; NOT bound to convState)
```

- **Sink A (WS transport):** state is born in supervoice; the browser already
  holds `/ws/audio`; supervoice pushes it down that connection. Lowest latency,
  production-realistic (a direct client with no harness still gets state). The
  browser's hand-rolled `protobuf.ts` already decodes `MessageFrame` (field 4),
  so no client-codec change.
- **Sink B (SDK hook):** exposes state as a `Session.on("state")` hook for SDK
  users / agent logic, mirroring the proven `metric` path. The playground
  *logs* it in EVENTS to prove the hook fires; it never drives `convState`.

⚠️ **Implementation risk to verify:** that pipecat's `ProtobufFrameSerializer`
emits the proto `MessageFrame` for our control frame. If it does not, `audio_server`
serializes and sends it explicitly over the same WS. We stay off RTVI to keep
the browser `pipecat`-free (per the playground's strict-transport rule).

## State machine (in `AgentBridgeProcessor`)

States: `idle`, `listening`, `thinking`, `speaking`, `interrupted`.

| From | Trigger (frame the processor already handles) | To |
| --- | --- | --- |
| idle / thinking / speaking | `UserStartedSpeakingFrame` / `VADUserStartedSpeaking` | listening (from speaking → via interrupted) |
| listening | turn finalized & `user.text` dispatched (SmartTurn EOU, existing `_dispatch_turn`) | thinking |
| thinking | `BotStartedSpeakingFrame` | speaking |
| speaking | `BotStoppedSpeakingFrame` | idle |
| speaking | `InterruptionFrame` | interrupted → listening |
| thinking | watchdog (no `BotStarted` within cap, or call end) | idle |

- "thinking" starts at the **EOU/dispatch** point, not raw VAD-stop, so
  mid-utterance pauses don't false-trigger.
- The **watchdog** (thinking with no reply) lives here, in the authority — state
  can't get stuck "lit" the way the old frontend timers did.
- Emit only on change (coalesced). Greeting-first flows start
  `idle → speaking → idle → listening …` naturally.

## Wire protocol & wiring

**Bridge event (sink B), mirrors `MetricEvent`:**

```
StateEvent { event: "state", call_id, state, turn_id }
   state ∈ idle | listening | thinking | speaking | interrupted
```

**supervoice edits:**
1. `worker/bridge/processor.py` — state machine + `_emit_state()` (WS frame
   always; bridge `StateEvent` gated on `"state" in negotiated_events`).
2. `worker/bridge/protocol.py` — `StateEvent` model + `BridgeEvent` union + `_TYPE_MAP`.
3. `worker/bridge/client.py` — add `"state"` to `_WORKER_SUPPORTED_EVENTS`.
4. `dev/relay.py` — add `"state"` to `_VOICE_SUPPORTED_EVENTS` (negotiation only;
   the event is piped as raw JSON, not a handshake frame). *Dev-only shim.*
5. `dev/audio_server.py` — only if the serializer doesn't carry `MessageFrame`.

**unpod-sdk edits:**
6. `src/unpod/_protocol.py` — parallel `StateEvent` + union + `_BRIDGE_EVENT_MAP`;
   advertise `"state"` in the worker's `supported_events`.
7. `src/unpod/connectivity/session.py` `run()` — `isinstance(event, StateEvent)
   → fire("state", event)` (mirrors the metric branch).
8. `src/unpod/connectivity/hooks.py` — add `"state"` to `VALID_EVENTS`.

**harness edit:**
9. `playground/harness/runner.py` — `@ctx.session.on("state")` →
   `bus.publish("state", state=evt.state, turn_id=evt.turn_id)` (EVENTS log only).

**frontend edits:**
10. `transport/Transport.ts` + `SupervoiceWSTransport.ts` — parse the `MessageFrame`
    in `ws.onmessage`; add `onState(state)` callback.
11. `client/SupervoiceClient.ts` — surface a `state` client event from `onState`.
12. `pages/AgentView.tsx` — `const [convState, setConvState]`; bind to the
    WS-transport `state` event (single writer + disconnect reset). **Delete** the
    timer phase machine (`pipeTimers`/`clearPipeTimers`/`pipeAt`/`phaseRef`, all
    `pipeAt(…)`), `PipelineState`/`IDLE_PIPELINE`/`pipe`, and `botLevel`-as-state.
13. `components/PipelinePanel.tsx` — take `convState`; derive `sttOn/dmOn/ttsOn`.
14. `components/StatusPanel.tsx` — "Agent" pill from `convState`.

## Frontend state → UI mapping (pure, no timers)

| convState | Pipeline | Agent pill | Waveform / dot |
| --- | --- | --- | --- |
| idle | dim mic | Ready | off |
| listening | mic + STT lit | Listening | mic active |
| thinking | DialogMachine pulsing | Thinking | spinner |
| speaking | TTS + "Caller hears" lit | Speaking | bot wave (amp = botLevel) |
| interrupted | brief flash → listening | Interrupted | — |

Turn **text** still comes from `user_turn`/`agent_turn`; the readout gates
display on `convState`. `botLevel`/`micLevel` survive **only** as waveform
amplitude — never as state.

## Testing

- **supervoice** — unit-test the `_conv_state` reducer over frame sequences
  (incl. interrupt path and coalescing); `StateEvent` JSON round-trip; the WS
  `MessageFrame` payload shape; watchdog reset.
- **unpod-sdk** — `Session.run` fires the `state` hook on a `StateEvent` (mirror
  the metric-hook test); `VALID_EVENTS` includes `state`.
- **frontend** — transport parses a state `MessageFrame` → `onState`; extract the
  `convState → {pipeline booleans, pill label}` mapping as a pure function and
  unit-test it; tsc-strict build.

## Edge cases & degradation

- **Greeting-first:** `idle → speaking → idle → listening` falls out naturally.
- **Thinking with no reply:** watchdog in supervoice resets `thinking → idle`.
- **Interrupted:** two real backend transitions (`interrupted` → `listening`),
  no frontend flash-timer.
- **State vs. turn-text ordering:** `convState` (media) may precede `agent_turn`
  text (agent layer); the readout shows a generic label until text lands.
- **Disconnect:** `appState` resets `convState` to `idle`.
- **Degradation:** state is additive; the bridge sink is negotiated; old
  supervoice → no `MessageFrame` → `convState` stays `idle` (neutral, not
  guessed). No timer fallback means it **cannot desync**.

## Single-source invariants (assert in review)

- `convState` has exactly **one writer** (the WS `state` handler) plus the
  disconnect reset.
- **No timer** writes `convState`.
- `botLevel` / `micLevel` are **never** written to `convState`.
- supervoice `_conv_state` is the sole authority; both sinks emit from the same
  transition.
