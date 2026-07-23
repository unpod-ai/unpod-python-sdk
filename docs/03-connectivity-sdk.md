# Connectivity SDK — AgentRunner, Session, Hooks & Controls

The Connectivity SDK is the runtime layer that handles live calls. It consists of two main objects:

- **AgentRunner** — long-lived process that registers with Unpod and dispatches incoming calls
- **Session** — per-call object with hooks (observe) and controls (act)

## AgentRunner

### Basic Usage

```python
from unpod import AgentRunner, CallContext

async def entrypoint(ctx: CallContext):
    """Called once per incoming/outgoing call."""
    session = ctx.session
    session.dialog_machine = my_dialog_machine

    await session.say("Hello!")
    await session.run()  # blocks until hangup

AgentRunner(
    entrypoint=entrypoint,
    agent_id="kyc-bot",
    api_key="unpod_sk_...",         # or UNPOD_API_KEY env var
    max_sessions=50,
    dev_mode=True,
).start()
```

### Constructor Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `entrypoint` | `Callable[[CallContext], Awaitable[None]]` | required | Async function invoked per call |
| `agent_id` | `str` | required | The runner's `agent_id` must match the Speech Pipe's `agent_id` in Control Plane |
| `api_key` | `str \| None` | `None` | Falls back to `UNPOD_API_KEY` env var |
| `max_sessions` | `int` | `50` | Hard cap; orchestrator queues beyond this |
| `permits_per_minute` | `int` | `120` | Rate limit on outbound calls |
| `drain_timeout_s` | `int` | `60` | Graceful shutdown window (seconds) |
| `dev_mode` | `bool` | `False` | Hot reload on file change |
| `base_url` | `str \| None` | `None` | Orchestrator base URL. Falls back to `UNPOD_ORCHESTRATOR_URL`, then a `UNPOD_BASE_URL`-derived WSS base, then `wss://api.unpod.ai`. Override for local dev (`ws://127.0.0.1:9000`). SDK appends the worker path internally. |
| `transport` | `str` | `"dial_out"` | `"dial_out"` (v2, default): the runner never listens — it dials OUT per call to the bridge URL delivered in `job.assign`. `"serve"` (legacy, deprecated): the runner hosts a bridge server that media agents dial into; requires `serving_url`/`UNPOD_RUNNER_URL` and `agent_secret`. |

### Methods

```python
# Blocking start (runs event loop)
runner.start()

# Async start (use within existing event loop)
await runner.run()

# Graceful shutdown
await runner.shutdown()
```

### Runner-Level Observability

```python
# Active calls
calls = runner.active_calls()
# → [CallContext, ...]

# Aggregate stats
stats = runner.stats()
# → RunnerStats(
#     in_flight=12,
#     queued=3,
#     completed_last_hour=480,
#     failed_last_hour=7,
#     capacity=50,
#     mean_call_duration_s=84,
# )

# Runner-level hooks
@runner.on("call_start")
async def _(ctx: CallContext):
    log.info("call started", call_id=ctx.call_id)

@runner.on("call_end")
async def _(ctx: CallContext, reason: str):
    log.info("call ended", call_id=ctx.call_id, reason=reason)

@runner.on("metric")
async def _(ctx: CallContext, metric: CallMetrics):
    push_to_grafana(metric)
```

### Lifecycle (dial_out, v2 default)

1. Opens persistent WSS to the Unpod orchestrator (`/v1/internal/workers`)
2. Sends `Register` with `agent_id`, `max_concurrent`, `transport: "dial_out"` — no
   listening socket, no `serving_url`
3. Receives `Registered` ack (heartbeat interval + `transport_ack`); an orchestrator
   that doesn't ack `dial_out` is too old — upgrade it or pass `transport="serve"`
4. Runs the heartbeat loop; on ANY control-socket drop the runner reconnects with
   jittered exponential backoff (1s → 30s cap) and re-registers under the same
   `worker_id`. In-flight calls ride their own bridge sockets and survive control
   drops.
5. On `job.assign` frame:
   - Rejects with `job.ack(accepted=False)` on `agent_id_mismatch` / `at_capacity`
   - Accepts with `job.ack(accepted=True)`, then dials OUT to the frame's
     `bridge_url` (per-call token in the URL), completes the hello handshake,
     receives `call.started`, builds `CallContext`, calls `entrypoint(ctx)`
6. On `job.cancel`: abandons the assigned job (media side failed)
7. On shutdown signal: stops accepting, drains active calls within `drain_timeout_s`

A laptop runner behind NAT works with zero network setup: both connections are
outbound. Config collapses to `UNPOD_BASE_URL` + `UNPOD_API_KEY`.

The legacy `transport="serve"` lifecycle (runner hosts the bridge; media agents
dial in using an HMAC-signed URL) is unchanged but deprecated — it requires a
publicly reachable `serving_url` and will be removed after the migration window.

### Multi-Replica

Multiple `AgentRunner` processes can register with the same `agent_id` to form a pool:

```
Process 1: AgentRunner(agent_id="kyc-bot", max_sessions=50)
Process 2: AgentRunner(agent_id="kyc-bot", max_sessions=50)
Process 3: AgentRunner(agent_id="kyc-bot", max_sessions=50)
                                                      ↑
                                          Total capacity: 150 calls
```

- Orchestrator round-robins dispatches across replicas
- No shared state between replicas
- Scale horizontally by adding more processes

---

## CallContext

Per-call metadata envelope wrapping Session.

```python
async def entrypoint(ctx: CallContext):
    ctx.call_id          # str — unique call identifier
    ctx.session_id       # str — session identifier
    ctx.agent_id         # str — agent that received the call
    ctx.direction        # "inbound" | "outbound"
    ctx.user_number      # str — caller/callee E.164 number
    ctx.instructions     # str | None — per-call prompt addendum (from calls.create)
    ctx.data             # dict — per-call data (from calls.create)
    ctx.room             # dict — informational media-room metadata (url/token/name)
    ctx.session          # Session — the per-call session object
```

### `ctx.room` — informational only

The dispatch frame carries the media room (`{url, token, name}`) that a media
worker joins to bridge the caller's audio. `ctx.room` exposes that metadata for
logging/observability. Your dialog brain is **text-only**: it never joins the
room, handles audio, or performs SDP negotiation — the worker owns all media.
`ctx.room` defaults to an empty dict when a dispatch omits it.

---

## Session

Per-call object providing hooks (observe) and controls (act).

### Hooks (Observe)

Register handlers to observe call events. Multiple handlers per event are supported.

```python
@session.on("call_start")
async def _():
    """Call answered, ready for first turn."""
    pass

@session.on("user_turn")
async def _(text: str):
    """Final STT transcript from user."""
    if "human" in text.lower():
        await session.transfer_to_human(queue="kyc-escalation")

@session.on("user_partial")
async def _(text: str):
    """Interim STT transcript (off by default).
    Enable via agent config or session.enable_partial_transcripts()."""
    pass

@session.on("agent_turn")
async def _(text: str):
    """Dialog machine emitted text to TTS."""
    pass

@session.on("tool_call")
async def _(name: str, args: dict):
    """Dialog machine invoked a tool."""
    pass

@session.on("tool_result")
async def _(name: str, result: dict):
    """Tool returned a result."""
    pass

@session.on("silence")
async def _(duration_ms: int):
    """User silence beyond threshold."""
    if duration_ms > 5000:
        await session.say("Are you still there?")

@session.on("interruption")
async def _():
    """User barged over agent speech."""
    pass

@session.on("metric")
async def _(m: CallMetrics):
    """Per-turn latency + cost snapshot."""
    pass

@session.on("call_end")
async def _(reason: str):
    """Call ended (hangup from any side)."""
    pass
```

### Hook Event Reference

| Event | Handler Signature | When |
|-------|-------------------|------|
| `call_start` | `()` | Call answered, ready for first turn |
| `user_turn` | `(text: str)` | Final STT transcript |
| `user_partial` | `(text: str)` | Interim STT (off by default) |
| `agent_turn` | `(text: str)` | Agent text sent to TTS |
| `tool_call` | `(name: str, args: dict)` | Tool invoked by dialog machine |
| `tool_result` | `(name: str, result: dict)` | Tool returned result |
| `silence` | `(duration_ms: int)` | User silence beyond threshold |
| `interruption` | `()` | User barged over agent |
| `metric` | `(m: CallMetrics)` | Per-turn latency + cost |
| `call_end` | `(reason: str)` | Call ended |

### Controls (Act)

#### Speech Controls

```python
# Speak directly (bypasses dialog_machine)
await session.say("Please hold while I check.")

# Cut off current TTS playback
await session.interrupt()

# Set filler text for latency masking
await session.set_filler("Let me check that for you...")
```

#### Dialog Machine Steering

```python
# Inject system instruction for next turn
session.dialog_machine.assist("User is upset, be empathetic")

# Hot-swap LLM model
session.dialog_machine.set_llm("anthropic/claude-haiku-4-5")

# Switch conversation flow (superdialog only)
session.dialog_machine.switch_flow(escalation_flow)
```

#### Routing

```python
# Transfer to human agent queue
await session.transfer_to_human(queue="tier2")

# Transfer to another AI agent
await session.transfer_to_agent(agent_id="senior-bot")
```

#### Session State

```python
# Read/write per-call state (available in dialog_machine context)
session.data["verified"] = True
session.data["customer_tier"] = "premium"
```

#### Recording

```python
# Pause recording (e.g., during PII collection)
await session.recording.pause(reason="pii")

# Resume recording
await session.recording.resume()
```

#### Termination

```python
# End the call
await session.end(reason="completed")
```

### Dialog Machine Slot

The `dialog_machine` slot accepts any object implementing the `DialogAdapter` protocol (see [Adapters](04-adapters.md)). Raw `superdialog.DialogMachine` instances are auto-wrapped.

```python
from superdialog import DialogMachine

dm = DialogMachine(flow=flow, llm="anthropic/claude-haiku-4-5")
session.dialog_machine = dm  # auto-wrapped in SuperDialogAdapter

# Or explicit
from unpod.adapters import SuperDialogAdapter
session.dialog_machine = SuperDialogAdapter(dm)
```

### Metrics

```python
m = session.metrics.live()
# → CallMetrics(
#     duration_s=47.2,
#     turns=8,
#     stt_p95_ms=320,
#     llm_p95_ms=680,
#     tts_p95_ms=240,
#     cost=CostBreakdown(voice=1.96, llm=0.04, total=2.00),
#     tokens=TokenUsage(input=1840, output=320),
#     active_llm="anthropic/claude-haiku-4-5",
# )
```

---

## Advanced Patterns

### Dynamic Model Swap Per Turn

```python
@session.on("user_turn")
async def _(text: str):
    if len(text) > 200 or detect_complexity(text) > 0.7:
        session.dialog_machine.set_llm("anthropic/claude-sonnet-4-5")
    else:
        session.dialog_machine.set_llm("anthropic/claude-haiku-4-5")
```

### Capacity-Aware Downgrade

```python
@runner.on("call_start")
async def _(ctx: CallContext):
    load = runner.stats().in_flight / runner.stats().capacity
    if load > 0.8:
        ctx.session.dialog_machine.set_llm("anthropic/claude-haiku-4-5")
```

### Per-Call Context from Outbound

```python
# Trigger outbound with per-call data
call = await client.calls.create(
    pipe_id=pipe.pipe_id,
    user_number="+919XXXXXXXXX",
    instructions="Customer prefers Hindi",
    data={"customer_id": "C123", "loan_amount": 50000},
)

# In entrypoint, access it
async def entrypoint(ctx: CallContext):
    customer_id = ctx.data["customer_id"]
    loan_amount = ctx.data["loan_amount"]
    ctx.session.dialog_machine.assist(
        f"Customer {customer_id} has a loan of ₹{loan_amount}"
    )
```

### Silence Handling with Escalation

```python
silence_count = 0

@session.on("silence")
async def _(duration_ms: int):
    nonlocal silence_count
    silence_count += 1
    if silence_count >= 3:
        await session.say("It seems like you might need more time. Let me connect you with a person.")
        await session.transfer_to_human(queue="general")
    elif silence_count >= 2:
        await session.say("Are you still there?")
```

### Recording PII Protection

```python
@session.on("agent_turn")
async def _(text: str):
    if "aadhaar" in text.lower() or "card number" in text.lower():
        await session.recording.pause(reason="pii")

@session.on("user_turn")
async def _(text: str):
    # Resume after PII collection
    await session.recording.resume()
```
