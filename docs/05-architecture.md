# Architecture

## Package Structure

```
unpod-sdk/
├── pyproject.toml
├── src/unpod/
│   ├── __init__.py                  # Public exports: Client, AsyncClient, AgentRunner, Session
│   ├── client.py                    # Client (sync) + AsyncClient (async) — REST entry points
│   │
│   ├── management/                  # REST resource groups
│   │   ├── __init__.py
│   │   ├── numbers.py               # client.numbers.*
│   │   ├── voice_profiles.py        # client.voice_profiles.*
│   │   ├── pipes.py                 # client.pipes.*
│   │   ├── calls.py                 # client.calls.*
│   │   ├── recordings.py            # client.recordings.*
│   │   ├── transcripts.py           # client.transcripts.*
│   │   └── _http.py                 # Shared httpx client, auth, retries, base URL
│   │
│   ├── connectivity/                # WSS runtime
│   │   ├── __init__.py
│   │   ├── runner.py                # AgentRunner
│   │   ├── session.py               # Session (per-call)
│   │   ├── call_context.py          # CallContext (metadata + session)
│   │   ├── bridge.py                # Bridge WSS client (text events)
│   │   ├── hooks.py                 # Hook event types + @session.on() registry
│   │   └── metrics.py               # CallMetrics, CostBreakdown, TokenUsage
│   │
│   ├── adapters/                    # dialog_machine slot adapters
│   │   ├── __init__.py
│   │   ├── base.py                  # DialogAdapter protocol
│   │   ├── superdialog.py           # SuperDialogAdapter (wraps superdialog.DialogMachine)
│   │   ├── langchain.py             # LangChainAdapter (wraps Runnable)
│   │   ├── http.py                  # HTTPAdapter (external brain endpoint)
│   │   └── mcp.py                   # MCPAdapter (MCP server tools)
│   │
│   ├── models/                      # Shared Pydantic models
│   │   ├── __init__.py
│   │   ├── pipe.py                  # Pipe, PipeCreate
│   │   ├── call.py                  # Call, CallCreate
│   │   ├── number.py                # Number
│   │   ├── voice_profile.py         # VoiceProfile
│   │   ├── recording.py             # Recording
│   │   ├── transcript.py            # Transcript
│   │   └── session.py               # SessionData, CallMetrics
│   │
│   └── _protocol.py                 # Bridge protocol frames (mirrored from supervoice)
│
├── tests/
├── docs/
└── uv.lock
```

## Design Principles

### 1. Text-Only Boundary

Audio never crosses the WSS to the developer. The SDK sends and receives text events over the bridge protocol. This means:

- No audio codec dependencies
- No STT/TTS vendor selection
- No SIP configuration
- Lightweight install (`pip install unpod`)

### 2. Protocol Mirroring (No Import Dependency)

`unpod-sdk` mirrors the bridge protocol frames from `supervoice/shared/dispatch_protocol.py` and `supervoice/worker/bridge/protocol.py` in its own `_protocol.py`. There is **no import dependency** on `supervoice`. The two packages communicate over WSS using shared frame definitions.

### 3. Sync + Async Clients

The Management SDK provides both:

- **`Client`** — synchronous, for scripts, notebooks, and one-off operations
- **`AsyncClient`** — asynchronous, used internally by the connectivity layer

Both share the same resource interface (`client.numbers.*`, `client.pipes.*`, etc.).

### 4. Auto-Wrapping Adapters

When you assign a `superdialog.DialogMachine` to `session.dialog_machine`, the SDK detects the type and auto-wraps it in `SuperDialogAdapter`. Explicit wrapping is also supported for custom adapters.

## Data Flow

### Inbound Call

```
Phone (PSTN)
  │
  ▼
Unpod Infrastructure (supervoice)
  ├── Telephony → SIP → Room
  ├── Speech Service → STT (audio → text)
  ├── Agent Bridge → text event
  │
  ▼ WSS: user.text
unpod-sdk (AgentRunner)
  ├── CallContext created
  ├── entrypoint(ctx) invoked
  ├── Session receives user.text
  ├── @session.on("user_turn") fires
  ├── session.dialog_machine.turn(text)
  │   └── DialogAdapter → superdialog / LangChain / HTTP / MCP
  │
  ▼ WSS: agent.text.delta + agent.text.end
Unpod Infrastructure
  ├── Agent Bridge → text to Speech Service
  ├── Speech Service → TTS (text → audio)
  └── Room → Phone (PSTN)
```

### Outbound Call

```
Developer code
  │
  ▼
client.calls.create(pipe_id=..., user_number=...)
  │ REST POST
  ▼
Unpod Control Plane
  ├── Telephony originates SIP call
  ├── Room created
  ├── Worker dispatched → bridge opened
  │
  ▼ WSS: job.assign (control) → runner dials bridge (data)
AgentRunner receives dispatch
  ├── CallContext created
  ├── entrypoint(ctx) invoked
  └── Same flow as inbound from here
```

## Protocol Details

### Worker Dispatch Protocol

Between AgentRunner and Unpod Orchestrator over persistent WSS (`/v1/internal/workers`).

**Runner → Orchestrator:**

| Frame | Purpose |
|-------|---------|
| `Register` | Initial hello: agent_id, max_concurrent, `transport: "dial_out"` |
| `Heartbeat` | Periodic liveness: active_jobs count + worker_id |
| `JobAck` | Accept/reject a `job.assign` (reject reasons: `agent_id_mismatch`, `at_capacity`) |

**Orchestrator → Runner:**

| Frame | Purpose |
|-------|---------|
| `Registered` | Ack with heartbeat interval + `transport_ack` |
| `JobAssign` | Call assignment (v2): `job_id`, `call_id`, `agent_id`, `bridge_url`, `call_token`, `deadline_ms`, `metadata` |
| `JobCancel` | Abandon an assigned job (media side failed) |

#### JobAssign frame fields

| Field | Type | Meaning |
|-------|------|---------|
| `job_id` | `str` | Orchestrator job identifier (echoed in `JobAck`) |
| `call_id` | `str` | Per-call session identifier |
| `agent_id` | `str` | The CALL's agent — the runner rejects a mismatch instead of answering as the wrong agent |
| `bridge_url` | `str` | The speech worker's per-call bridge endpoint the runner dials OUT to |
| `call_token` | `str` | Single-active-connection bearer token, valid for the job lifetime (redial with the same token after a drop) |
| `deadline_ms` | `int` | Ack deadline; a late ack forfeits the job to failover |
| `metadata` | `dict` | Per-call data/instructions forwarded onto `CallContext` (incl. `playbook_id`, `org_id`) |

The legacy serve-mode frames (`Dispatch` with `runner_url`/`agent_secret`,
`DispatchAck`) remain for `transport="serve"` runners during the migration
window and are removed with it.

##### `room` is informational — worker owns media, brain is text-only

The `room` block tells the media worker which LiveKit room to join to bridge the
caller's audio. The SDK surfaces it as `CallContext.room` purely for
logging/observability. **The dialog brain never joins the room, handles audio,
or performs SDP negotiation** — the media worker owns all media, and the brain
communicates only over the text-only bridge protocol. `CallContext.room`
defaults to an empty dict when a dispatch omits it.

### Bridge Protocol (Per-Call)

Between Session and Unpod Bridge over per-session WSS. Authenticated via HMAC-SHA256 signed URL.

**Upstream (Bridge → Session):**

| Event | Payload |
|-------|---------|
| `user.text` | `{text, is_final}` |
| `user.interrupted` | `{}` |
| `error` | `{severity, source, code, message}` |
| `metric` | `{ttfa_ms, asr_p95_ms, tts_p95_ms, turns, cost_usd_so_far}` |

**Downstream (Session → Bridge):**

| Verb | Payload |
|------|---------|
| `agent.text.delta` | `{text}` — streaming token |
| `agent.text.end` | `{}` — turn complete |
| `agent.say` | `{text}` — bypass dialog_machine |
| `agent.transfer` | `{transfer_type, target, mode}` — routing (cold by default) |
| `agent.end_call` | `{reason}` — terminate session |
| `agent.dispatch` | `{agent_id, metadata}` — hand off to another agent |
| `agent.add_participant` | `{participant_type, config}` — add a participant |
| `agent.remove_participant` | `{participant_id}` — remove a participant |
| `agent.merge` | `{source_session_id}` — merge another session into this call |

**Handshake:**

1. Runner sends `hello` with protocol_version, supported_events, supported_verbs
2. Bridge responds `hello.ack` with negotiated capabilities, call_id, session_id

## Concurrency Model

```
AgentRunner (1 process, N calls)
├── Persistent WSS to orchestrator
├── Heartbeat loop
├── Dispatch handler
│   ├── Capacity check (active < max_sessions)
│   ├── Spawn CallContext + Session
│   ├── Open per-call bridge WSS
│   └── Invoke entrypoint(ctx)
└── Graceful shutdown (drain_timeout_s)
```

**Multi-replica:**

- N AgentRunner processes register with the same `agent_id`
- Orchestrator round-robins dispatches across replicas
- No shared state between replicas
- Each replica advertises its own `max_sessions`

## Relationship to supervoice

| Concern | supervoice (backend) | unpod-sdk (this package) |
|---------|---------------------|--------------------------|
| Audio pipeline | PipeCat + STT/TTS providers | None — text only |
| Room management | RoomEngine (LiveKit / in-process) | Not exposed |
| Worker lifecycle | Worker + JobRunner + AgentAdapter | AgentRunner + Session + CallContext |
| Bridge protocol | AgentBridgeClient (server-side) | bridge.py (client-side) |
| Dispatch protocol | WorkerDispatcher + WorkerRegistry | _protocol.py (mirrored frames) |
| Voice profiles | VoiceProfileCatalog (YAML) | client.voice_profiles.list() (REST) |
| Numbers | NumberMappingCache | client.numbers.* (REST) |
| Session state | Session state machine (orchestrator) | Session (developer-facing controls) |

The two packages **never import each other**. They communicate exclusively over WSS (dispatch + bridge protocols) and REST (management API).
