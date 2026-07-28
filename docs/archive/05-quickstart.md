# Quickstart — 10 Steps to Your First Call

Target: under 10 minutes from install to a live voice call.

## Two Paths

**Browser (fastest — no phone number needed)**
Use the `auto_rl` dev server to test your agent in Chrome before going to production.
→ Jump to [Browser Quickstart](06-browser-quickstart.md)

**Phone (production)**
Sync numbers from a trunk, bind your Speech Pipe, call from any phone.
→ Continue below.

## Step 0 — Install

```bash
pip install unpod[dialog]
```

## Step 1 — API Key

Get your API key from the Unpod dashboard (or create one via the admin endpoint).

```bash
export UNPOD_API_KEY="sk_..."
export UNPOD_SERVICE_BASE_URL="http://localhost:8000/platform"  # or your deployed URL
```

```python
from unpod import AsyncClient

client = AsyncClient()  # reads UNPOD_API_KEY and UNPOD_SERVICE_BASE_URL from env
```

## Step 2 — Pick a Voice Profile

```python
profiles = await client.voice_profiles.list(language="en")
vp = profiles[0]
print(f"{vp.name} — {vp.tts_provider}/{vp.tts_voice}, latency ~{vp.latency_ms}ms")
# Alloy — openai/alloy, latency ~400ms
```

## Step 3 — Set Up a Trunk and Sync Numbers

If you have a LiveKit SIP trunk, register it and sync numbers:

```python
from unpod.models import TrunkCreate

trunk = await client.trunks.create(TrunkCreate(
    name="main-trunk",
    type="livekit",
    provider_trunk_id="your-livekit-trunk-id",
))

# Sync phone numbers from all LiveKit trunks
synced = await client.numbers.sync()
print(f"Found {len(synced)} numbers")
# [Number(number_id="NUM_abc", number="+9180XXXXXXXX", status="available", ...)]
```

Or list numbers that were previously synced:

```python
numbers = await client.numbers.list(status="available")
num = numbers[0]
```

## Step 4 — Build Your Dialog Machine (Local)

```python
from superdialog import DialogMachine, create_dialog_flow, PythonTool

flow = create_dialog_flow(
    prompt="Verify customer KYC. Ask for Aadhaar last 4 digits, then confirm name.",
    llm="openai/gpt-4.1-mini",
)

def lookup_customer(aadhaar_last_4: str) -> dict:
    """Look up customer by Aadhaar last 4 digits."""
    return {"name": "Rajesh Kumar", "status": "pending_kyc"}

dm = DialogMachine(
    flow=flow,
    llm="anthropic/claude-haiku-4-5",
    tools=[PythonTool(fn=lookup_customer)],
)
```

## Step 5 — Create AgentRunner

```python
from unpod import AgentRunner, CallContext

async def entrypoint(ctx: CallContext):
    dm = DialogMachine(flow=flow, llm="anthropic/claude-haiku-4-5", tools=[...])
    ctx.session.dialog_machine = dm
    await ctx.session.say("Namaste! I'm calling from Unpod.")
    await ctx.session.run()

AgentRunner(
    entrypoint=entrypoint,
    agent_id="kyc-bot",
    max_sessions=50,
    dev_mode=True,
).start()
```

## Step 6 — Create a Speech Pipe and Attach Number

```python
pipe = await client.pipes.create(
    name="kyc-bot",
    voice_profile=vp.name,              # name (case-insensitive) or profile_id
    system_prompt="Verify customer KYC. Ask for Aadhaar last 4 digits.",
    first_message="Namaste! Main Unpod se bol raha hoon.",
    first_speaker="agent",
    agent_id="kyc-bot",                 # matches AgentRunner's agent_id
    recording=True,
)
print(f"Speech Pipe {pipe.pipe_id} created")

# Attach a number to the Speech Pipe
number = await client.numbers.attach(num.number_id, pipe.pipe_id)
print(f"Number {number.number} attached → status: {number.status}")
```

## Step 7 — Test Inbound

Call the number from your phone. The full pipeline fires:

```
Your phone → PSTN → SIP → Trunk → Room → STT → Bridge → AgentRunner → Bridge → TTS → Your phone
```

## Step 8 — Trigger Outbound

```python
call = await client.calls.create(
    pipe_id=pipe.pipe_id,
    to_number="+919XXXXXXXXX",
    instructions="Customer prefers Hindi. Confirm Aadhaar.",
    data={"customer_id": "C123", "loan_amount": 50000},
)
print(f"Outbound call {call.call_id} — status: {call.status}")
# status is "pending": calls.create() enqueues and returns immediately. The
# call dispatches once the account has a free slot, then advances to
# ringing → active → completed. Poll calls.get(call_id) to track it.
```

## Step 9 — Monitor

```python
# Active calls
calls = await client.calls.list(status="active")

# Session with transcript after call ends
sessions = await client.transcripts.list()
for session in sessions:
    for turn in session.transcript:
        print(f"[{turn.role}] {turn.content}")

# Sessions with recordings
recordings = await client.recordings.list()
for session in recordings:
    print(f"Recording: {session.recording_url}")
```

## Step 10 — Iterate

Edit your entrypoint or system prompt. With `dev_mode=True`, the runner hot-reloads on file change.

---

## What Just Happened

```
┌─────────────── You Wrote ───────────────┐
│  entrypoint()      (call handler)        │
│  lookup_customer() (business logic)      │
│  system_prompt     (agent instructions)  │
│  ~30 lines of Python                     │
└──────────────────────────────────────────┘

┌─────────── Unpod Handled ───────────────┐
│  SIP trunk + phone numbers               │
│  STT (speech → text)                     │
│  TTS (text → speech)                     │
│  Voice profile (STT+TTS bundle)          │
│  Media server + Room                     │
│  Recording + transcript capture          │
│  Bridge protocol (text routing)          │
└──────────────────────────────────────────┘
```

See [Management SDK](02-management-sdk.md) for the full API reference and [Connectivity SDK](03-connectivity-sdk.md) for hooks and live controls.
