# Connectivity — Ownership, Boundaries & End-to-End Flow

Companion to [`architecture.md`](./architecture.md). That doc maps the *data planes*
hop-by-hop; this one answers a blunter question: **which process owns what, where the
boundaries are, and who dials whom.**

Three processes, six sockets. The brain lives in the harness (`:8765`), the speech lives
in supervoice (`:9000`), the UI is the browser. The relay is a *patch panel* that joins two
sockets together.

## 1 · Ownership map — 3 processes, hard boundaries

```
╔═══════════════════╗   ╔═══════════════════════════════════╗   ╔═══════════════════════════════════╗
║  BROWSER          ║   ║  PLAYGROUND HARNESS               ║   ║  SUPERVOICE DEV                   ║
║  (one Chrome tab) ║   ║  repo: unpod-sdk                  ║   ║  repo: supervoice                 ║
║                   ║   ║  proc: python -m playground.run   ║   ║  proc: uvicorn dev.app            ║
╠═══════════════════╣   ╠═══════════════════════════════════╣   ╠═══════════════════════════════════╣
║ LISTENS: nothing  ║   ║ LISTENS:                          ║   ║ LISTENS:                          ║
║                   ║   ║   :9100  FastAPI control + events ║   ║   :9000  dev.app (audio + relay)  ║
║ OWNS (web/):      ║   ║   :8765  worker bridge SERVER     ║   ║                                   ║
║  AgentView.tsx    ║   ║                                   ║   ║ OWNS — component A: audio_server  ║
║  SupervoiceClient ║   ║ OWNS — control plane:             ║   ║   pipecat pipeline (STT▸LLM▸TTS)  ║
║  MicCapture       ║   ║   api.py  /playground/sessions    ║   ║   + AgentBridgeClient  ◄═ dials   ║
║  AudioPlayer      ║   ║   control.py  /…/control          ║   ║                                   ║
║                   ║   ║   events.py  EventBus             ║   ║ OWNS — component B: relay.py      ║
║                   ║   ║ OWNS — in-proc WORKER (AgentRunner)║   ║   voice_endpoint  /bridge/voice   ║
║                   ║   ║   session.py + DialogMachine      ║   ║   connect_to_worker_bridge        ║
║                   ║   ║   serves bridge SERVER on :8765   ║   ║   _workers registry + 2 queues    ║
╚═══════════════════╝   ╚═══════════════════════════════════╝   ╚═══════════════════════════════════╝
   the UI only            the BRAIN (dialog/flows) + control      the EARS/MOUTH (speech) + the PIPE
```

## 2 · The 5 wires — who **dials** whom (arrow = direction of the *dial*)

> **Updated 2026-06-11:** the relay's single-slot `/bridge/voice` pipe was removed.
> `audio_server` now dials the brain's `serving_url` (`:8765`) **directly**, mirroring the
> production media-worker path (`worker/agent_adapter.py` → `AgentBridgeClient(runner_url)`).
> Old wires ④ (loopback) + ⑤ (relay→worker) collapse into one direct media→brain wire.

```
①  REGISTER   WORKER(harness) ──dial──▶ :9000 /v1/internal/workers     once at startup
②  CONTROL    BROWSER ──────────dial──▶ :9100 HTTP /sessions,/connect,/control
③  AUDIO·web  BROWSER ──────────dial──▶ :9000 /ws/audio          (PCM frames, both directions)
④  AUDIO·brain audio_server ────dial──▶ :8765 worker bridge      DIRECT per-session (was relay pipe)
⑤  EVENTS     BROWSER ──────────dial──▶ :9100 /playground/events (server pushes transcript/metrics)
```

The non-obvious one:
- **④ is per-session and direct** — `audio_server.build_voice_bridge_client()` resolves the
  brain via `relay._resolve_worker(agent_id)` and points its pipecat `AgentBridgeClient` straight
  at `:8765`. The brain's `_bridge_handler` sends `hello` (advertising `metric`), the client
  replies `hello.ack` + `call.started` (carrying `flow`/`voice_profile`). Each call owns its own
  connection, so there is no shared slot to contend for — concurrent calls can't evict each other.

## 3 · End-to-end connectivity flow (one call, top to bottom)

```
BROWSER            HARNESS :9100              WORKER :8765            SUPERVOICE :9000
  (web/)           (api + brain)              (in harness)           (audio_server | relay)
   │                    │                         │                        │
   │                    │  ①  AgentRunner.run(): worker dials relay, "Register{serving_url::8765}"
   │                    │         ───────────────────────────────────────▶ relay._workers[id]=:8765
   │                    │                         │   serves bridge SERVER on :8765
   │                    │                         │                        │
   │ ② POST /sessions,/connect                    │                        │
   │ ─────────────────▶ api: asks supervoice for an audio URL ───────────▶ audio_server.connect()
   │ ◀───────────────── {ws_url = :9000/ws/audio} ◀──────────────────────  returns ws_url
   │                    │                         │                        │
   │ ③ new WebSocket(/ws/audio)  ──────────────────────────────────────▶ audio_ws() opens
   │                    │                         │                        │  │ builds pipeline
   │                    │                         │   ⑤ relay dials worker :8765
   │                    │                         │ ◀───────────────────── connect_to_worker_bridge()
   │                    │                         │   hello / hello.ack / call.started
   │                    │                         │                        │  │
   │                    │                         │   ④ audio_server's AgentBridgeClient
   │                    │                         │      dials relay /bridge/voice  ⟲ (loopback)
   │                    │                         │                        │  └─ pipeline runs
   │                    │                         │                        │
   ├── speak ─③──────────────────────────────────────────────────────────▶ STT ▸ UserTextEvent
   │                    │                         │  ◀── ④ relay queue ◀── AgentBridgeClient sends
   │                    │                         │  ◀── ⑤ relay pipes ──▶ worker Session.run()
   │                    │                         │      DialogMachine.stream() → agent.text
   │                    │                         │  ── ⑤ ──▶ relay queue ── ④ ──▶ TTS ▸ PCM
   │ ◀── reply audio ③ ◀──────────────────────────────────────────────────── audio_server
   │                    │                         │                        │
   │                    │  ⑥ worker hooks → EventBus → /playground/events   │
   │ ◀═ transcript/metrics/flow-node ═ /playground/events ═ (separate WS) ══│
   │                    │                         │                        │
```

**Audio data path (end to end):**
`Browser ⇄③ audio_server(pipecat) ⇄④ relay queues ⇄⑤ worker(brain)`

**Side data path (transcript/metrics):**
`worker(brain) → EventBus →⑥ Browser` — a *different socket on :9100*, never touches audio.

## 4 · Where the boundaries actually are

| Boundary | Left owns | Right owns | Joined by |
|----------|-----------|------------|-----------|
| Browser ↔ Harness | UI, mic/speaker | control plane + brain + events | ②②⑥ (HTTP + events WS) |
| Browser ↔ Supervoice | UI, mic/speaker | speech (STT/TTS) | ③ (`/ws/audio`, PCM) |
| Supervoice-internal | `audio_server` (speech) | `relay` (patch panel) | ④ (`/bridge/voice` loopback) |
| Supervoice ↔ Harness | `relay` (patch panel) | worker brain | ⑤ (`:8765`) + ① (register) |

The **relay is the meeting point**: its two queues (`_voice_to_agent`, `_agent_to_voice`)
bolt wire ④ (speech) onto wire ⑤ (brain). Everything else is point-to-point.

## 5 · History — the single-slot storm and its removal

**The bug (pre-2026-06-11):** `relay.py` held **one** global voice slot (`_voice_task`) and
bridged it to the worker through shared queues. A second `/ws/audio` session opened a second
pipecat `AgentBridgeClient` to `/bridge/voice`; the relay evicted the first; the evicted client
treated the eviction as a network drop and reconnected in 200 ms (its backoff never escalated
because `attempt` resets on every successful connect — `worker/bridge/client.py:238`), which
evicted the other — a mutual-eviction reconnect storm (`total_reconnects` climbed forever).

**The fix:** the relay pipe was redundant. Production never had a relay — a media worker dials
the brain's `runner_url` directly (`worker/agent_adapter.py:252`). Dev now does the same:
`audio_server.build_voice_bridge_client()` dials the brain's `serving_url` directly, one
connection per session. The relay shrank to a registry (`_workers` + `_resolve_worker` +
`workers_endpoint`); `voice_endpoint`, `connect_to_worker_bridge`, `_pipe_worker`, the queues,
`_voice_task`, `_worker_*`, and `_evict_worker` were deleted. With no shared slot, the storm is
structurally impossible. Regression coverage: `supervoice/tests/test_audio_server_direct_bridge.py`.

## 6 · Call lifecycle — kill on disconnect, fresh on connect

The invariant: **a brain session lives exactly as long as its browser call.** Enforced at
three layers (defense in depth):

1. **Browser** — `SupervoiceWSTransport` registers a `pagehide` listener on connect that
   synchronously `ws.close()`es `/ws/audio` (browsers won't await async cleanup on unload);
   `disconnect()` removes it. Every connect mints a fresh `session_id`/`job_id`.
2. **Server cascade** — `audio_ws` wraps the whole call in `try/finally`: every exit path
   (connect failure, missing API key, pipeline end, exception) closes the bridge client,
   which closes the brain's bridge WS, which ends the brain `Session`
   (`handle_bridge_connection` → `on_call_end` → `_active_calls.pop`).
3. **Protocol keepalive** — both WS layers ping by default (uvicorn `websockets` impl and
   the `websockets` library: `ping_interval=20s`, `ping_timeout=20s`), so a crashed tab or
   half-open TCP is detected ≤40 s and feeds the same cascade. No custom watchdog needed.

## 7 · Reachability design note — when brains leave the cloud pool

The media→brain hop is the system's only inbound dial. Today's model (managed pool, brains
reachable in-VPC) is correct for latency/footprint/scale: **keep direct dial.** When
customer-hosted/edge brains (behind NAT) become a requirement, the migration path is:

- `WorkerCapabilities.serving_url` is already `Optional` — let `serving_url=None` at
  registration mean "not directly reachable."
- Add a stateless **gateway tier**: edge brains hold one outbound WSS to a gateway; media
  dials the gateway, which routes bridge frames by `session_id`. Orchestrator stays off the
  data path (do NOT tunnel bridge frames through the orchestrator's registration WSS — that
  puts the control plane on the per-token hot path).
- Hybrid end state: direct dial when `serving_url` is set, gateway when it isn't —
  branch in one place, the `runner_url` resolution (`composition.py:243`).
