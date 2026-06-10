# Playground Architecture — Touchpoints & Data Planes

How a browser call flows through the voice playground, with the function name at
every hop. The system spans three processes and four independent data planes
(each on its own socket — audio never shares a channel with transcript/metrics).

> Transports only: telephony, LiveKit, the platform DB, auth, and the
> orchestrator/queue are **not** on this path. The playground exercises the real
> speech pipeline + bridge protocol; the UI is just a connecting interface.

## Processes & ports

```
┌──────────────┐   ┌─────────────────────────────────────┐   ┌──────────────────────────────────┐
│  BROWSER     │   │  PLAYGROUND HARNESS  (:9100)         │   │  SUPERVOICE DEV  (:9000)         │
│  web/ (React)│   │  unpod-sdk/playground/…              │   │  super/supervoice/dev/…          │
│              │   │  ┌────────────────────────────────┐  │   │  ┌────────────┐  ┌────────────┐  │
│              │   │  │ FastAPI control plane (api.py)  │  │   │  │ audio_server│  │  relay.py  │  │
│              │   │  ├────────────────────────────────┤  │   │  │   .py      │  │            │  │
│              │   │  │ in-process AgentRunner WORKER   │  │   │  │ (pipecat)  │  │ (pairs     │  │
│              │   │  │ bridge server on :8765          │◄─┼───┼──┤            │  │  voice⇄wkr)│  │
│              │   │  │ unpod/connectivity/{runner,     │  │   │  └────────────┘  └────────────┘  │
│              │   │  │   session}.py                   │  │   └──────────────────────────────────┘
│              │   │  └────────────────────────────────┘  │
└──────────────┘   └─────────────────────────────────────┘
```

The SDK **worker** (`AgentRunner`) runs as an asyncio task *inside* the harness
process, but plays a distinct role: it registers with supervoice's relay and
serves its own bridge server on `:8765`, which the relay connects out to.

## 1. REGISTER plane — worker advertises itself (once, at startup)

```
HARNESS lifespan()                         SUPERVOICE relay.py
  api.lifespan()
   └ build_runner()  (runner.py)
       └ AgentRunner.run()
           │  WS  /v1/internal/workers       ──►  workers_endpoint()
           │  Register{capabilities:{                 │ caps.get("agent_id")
           │    agent_id:"playground-flows",          │ caps.get("serving_url")
           │    serving_url:"ws://…:8765"}}            └► _workers[agent_id] = serving_url
           │                                  ◄──  {"type":"registered"}
           └ serves bridge server on :8765   (relay later connects OUT to this)
```

The relay keeps a registry `_workers: dict[agent_id → serving_url]`, so multiple
workers can register with one dev service and calls route to the right one.

## 2. CONNECT plane — browser opens a call (HTTP, then WS)

```
BROWSER                         HARNESS api.py                       SUPERVOICE audio_server.py
AgentView.connect()
 └ SupervoiceClient.connect()
    └ SupervoiceWSTransport
        .connect()
        .createSession()
        POST /playground/sessions ──► create_session()
            ?agent=playground-flows      │ resolve_agent(agent)
                                         │ POST /connect ───────────► connect(agent_id, voice_profile_id)
                                         │   ?agent_id&voice_profile      └► returns {ws_url}
                                         │ ◄── {session_id,…}
                                         └ ws_url = …/ws/audio
                                              ?agent_id=playground-flows
                              ◄── {ws_url, transport:"ws", agent}
        new WebSocket(ws_url) ───────────────────────────────────► audio_ws(agent_id, voice_profile_id)
                                                                       │ connect_to_worker_bridge(agent_id=…)
                                                                       │   └ _resolve_worker(agent_id) → _workers[…]
                                                                       │       └ ws.connect(serving_url :8765)
                                                                       │         hello / hello.ack / call.started
                                                                       │ build_pipeline(PipelineConfig)
                                                                       │ bridge_processor.attach_client() + .start()
                                                                       │ PipelineRunner.run(task)
        MicCapture.start()    (audio.ts)                               AudioPlayer in browser
```

## 3. AUDIO plane — bidirectional media (meets at the relay's queues)

```
            BROWSER                SUPERVOICE audio_server (pipecat)        relay.py            WORKER :8765
 ── UP (user speech) ─────────────────────────────────────────────────────────────────────────────────────►
 MicCapture(onChunk)            transport.input → STT(Deepgram/Soniox)
   └ encodeAudioFrame()  ─PCM─►   → UserTurnProcessor (EOU/SmartTurn)
     ws.send (/ws/audio)         → AgentBridgeProcessor._consume… sends
                                   UserTextEvent via AgentBridgeClient
                                     └ WS /bridge/voice ──────────────► voice_endpoint._reader
                                                                          └ _voice_to_agent.put()
                                                                        _pipe_worker._to_worker
                                                                          └ ws.send ───────────► Session.run()
                                                                                                  recv UserTextEvent
                                                                                                  fire "user_turn"
                                                                                                  DialogMachine.stream()
 ◄── DOWN (agent reply) ────────────────────────────────────────────────────────────────────────────────────
 AudioPlayer.enqueue()         transport.output ← TTS(Cartesia)        _pipe_worker._from_worker   agent.text.delta
   ◄ decodeFrame() ◄─PCM─       ← AgentBridgeProcessor pushes            ◄ _agent_to_voice.put() ◄── agent.text.end
     ws.onmessage                 LLMText frames                        voice_endpoint._writer
     (/ws/audio)                  ← AgentBridgeClient recv  ◄─ WS /bridge/voice ◄┘
```

## 4. SIDE-CHANNEL plane — transcript / metrics / flow-node (separate WS)

```
WORKER (in harness)  unpod/…                 HARNESS runner.py hooks         events.py        BROWSER
Session.run() fires:                          (registered in entrypoint)
  "call_start" ───────────────────────────►  _on_start  → bus.publish("ready")
                                              + _publish_flow_node            EventBus
  "user_turn" ────────────────────────────►  _on_user   → publish("user_turn")  .publish()
  "agent_turn" ───────────────────────────►  _on_agent  → publish("agent_turn") fan-out to
  "metric"  ◄─ MetricEvent from supervoice    _on_metric → publish("metric")     subscriber
     AgentBridgeProcessor._emit_metric()       _on_interruption                  queues
     (CallMetrics.snapshot, every 1.5s)        _on_end    → publish("disconnected")  │
                                                                                     │ WS /playground/events
                                                                                     ▼   events()  ── send_json ──►
                                                                              SupervoiceClient.openSideChannel()
                                                                                onmessage → emit(type) → onAny()
                                                                                AgentView: setMetrics / addTurn /
                                                                                  setCurrentNode / pushEvent
```

## 5. CONTROL plane — live flow switch (HTTP)

```
BROWSER                          HARNESS api.py / control.py            WORKER (in-process)
Sidebar.onSelect(flowId)
 └ AgentView.selectFlow()
    └ config.switchFlow()
      POST /playground/sessions/active/control ──► control()
        {action:"switch_flow",                       │ SessionRegistry.resolve("active")
         params:{flow, preserve_memory}}             │ SessionRegistry.apply()
                                                     │   └ SuperDialogAdapter.switch_flow()
                                                     │       └ DialogMachine.switch_flow(name)   (FlowSet)
                                                     │ adapter.state → bus.publish("flow_node_changed")
                                  ◄── {ok:true}      └────────────────────────► (side-channel plane #4)
```

## The relay is the meeting point

`relay.py` is where the two bridge sockets join and are piped bidirectionally by
`_pipe_worker` through the `_voice_to_agent` / `_agent_to_voice` queues:

- **voice side** — `voice_endpoint` (`WS /bridge/voice`) ⇄ supervoice's
  `AgentBridgeClient` (inside the pipecat pipeline).
- **agent side** — `connect_to_worker_bridge` ⇄ the worker's bridge server on
  `:8765` (served by `AgentRunner`).

The relay also drives the voice client's v2 handshake (sends `HelloEvent`
advertising `metric`) so per-turn metrics are negotiated rather than dropped.

## File map

| Role | File |
| --- | --- |
| Browser UI + orchestration | `web/src/pages/AgentView.tsx` |
| Browser client / transport / codec | `web/src/client/SupervoiceClient.ts`, `web/src/transport/{SupervoiceWSTransport,audio,protobuf}.ts` |
| Flows sidebar + control calls | `web/src/components/Sidebar.tsx`, `web/src/config.ts` |
| Harness control plane | `playground/harness/api.py` |
| Worker wiring + side-channel hooks | `playground/harness/runner.py` |
| Live control registry | `playground/harness/control.py` |
| Side-channel bus | `playground/harness/events.py` |
| Flow discovery + FlowSet machine | `playground/agents/flows.py`, `playground/agents/catalog.py` |
| SDK worker / session | `unpod/connectivity/{runner,session}.py`, `unpod/adapters/superdialog.py` |
| Dev speech service (pipecat) | `super/supervoice/dev/audio_server.py` |
| Dev relay (voice ⇄ worker) | `super/supervoice/dev/relay.py` |
| Pipeline + bridge + metrics | `super/supervoice/worker/{pipeline/builder,bridge/processor,bridge/client}.py` |
```
