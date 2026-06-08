# Management SDK — API Reference

The Management SDK is a REST client for the Unpod Platform API. It provides operations for trunks, numbers, voice profiles, Speech Pipes, calls, sessions, recordings, and transcripts.

All resources are scoped to a `project_id`. In **direct** mode that project is
derived from your supervoice API key; in **proxy** mode it is derived from the
organization (`Org-Handle`) resolved by the unpod backend-core proxy.

## Client Setup

```python
from unpod import AsyncClient, Client

# API key from env (UNPOD_API_KEY); base URL from env (UNPOD_SERVICE_BASE_URL)
client = Client()

# Explicit values
client = Client(api_key="sk_...", base_url="http://localhost:8000/platform")
```

### Direct vs proxy mode

The same resource calls reach the management API two ways — only the constructor
differs (resource paths are identical):

| Mode | Talks to | Auth | Base URL |
|---|---|---|---|
| **Direct** | supervoice (`/platform/v1`) | `BearerAuth(api_key)` (default) | `https://<host>` or `…/platform` |
| **Proxy** | unpod backend-core (`api/v2/platform/speech/v1`) | `JWTAuth(token, org_handle)` | `https://<host>/api/v2/platform/speech` |

```python
from unpod import AsyncClient, BearerAuth, JWTAuth

# Direct → supervoice (Bearer API key)
client = AsyncClient(api_key="sk_...", base_url="https://api.unpod.ai")

# Proxy → backend-core (platform JWT + org). The proxy injects the org's
# supervoice key and applies Django auth/middleware; responses are verbatim.
client = AsyncClient(
    base_url="https://app.unpod.ai/api/v2/platform/speech",
    auth=JWTAuth(token="<user-jwt>", org_handle="acme"),
)

await client.pipes.list()  # identical surface in both modes
```

> **Proxy-mode caveat:** the proxy fronts the **management plane only**. Session
> lifecycle ops (`sessions.end` / `transfer` / `merge`) target the orchestrator
> and are not available through the proxy — use direct mode for those.

Environment variables:
- `UNPOD_API_KEY` — API key (required)
- `UNPOD_SERVICE_BASE_URL` — Base URL (default: `http://localhost:8000/platform`)
- `UNPOD_ORCHESTRATOR_BASE_URL` — Orchestrator base URL (optional; see below)

Both `Client` and `AsyncClient` expose the same resource interfaces. Examples use the async form.

### Orchestrator base URL

Most resources hit the platform service. Session **lifecycle** operations
(`sessions.end`/`transfer`/`merge`) hit a separate **orchestrator** service. Its
base URL is resolved in order:

1. `orchestrator_base_url=` constructor argument
2. `UNPOD_ORCHESTRATOR_BASE_URL` environment variable
3. derived by swapping a trailing `/platform` in `base_url` for `/orchestrator`

```python
client = Client(
    api_key="sk_...",
    base_url="http://localhost:8000/platform",
    orchestrator_base_url="http://localhost:8001/orchestrator",
)
```

If `base_url` has no `/platform` suffix and no override is given, the
orchestrator falls back to `base_url` — set `orchestrator_base_url` explicitly
for non-standard deployments so lifecycle ops do not hit the platform service.

---

## Trunks

SIP trunks connect your project to phone carriers. Two trunk types:
- `livekit` — managed LiveKit SIP trunk (provider-hosted)
- `byo` — Bring Your Own carrier via SIP credentials

### List Trunks

```python
trunks = await client.trunks.list()
# → [Trunk(trunk_id="TK_abc", name="main-trunk", type="livekit", status="active", ...)]
```

### Create LiveKit Trunk

```python
from unpod.models import TrunkCreate

trunk = await client.trunks.create(TrunkCreate(
    name="main-trunk",
    type="livekit",
    provider_trunk_id="lk-trunk-id-from-livekit-dashboard",
))
```

### Create BYO Trunk

```python
from unpod.models import TrunkCreate, ByoConfigCreate

trunk = await client.trunks.create(TrunkCreate(
    name="tata-byo",
    type="byo",
    byo_config=ByoConfigCreate(
        provider="tata",
        sip_domain="sip.tata.in",
        auth_username="user",
        auth_password="secret",     # stored encrypted, never returned
        transport="tls",
    ),
))
```

### Delete Trunk

```python
await client.trunks.delete(trunk.trunk_id)
```

---

## Numbers

Phone numbers are sourced from SIP trunks (synced from LiveKit) and attached to Speech Pipes. Numbers are never purchased directly.

**Lifecycle:** `available` → `assigned` (attached to Speech Pipe) → `in_call` (active call) → `assigned` (call ends)

### List Numbers

```python
numbers = await client.numbers.list()
# → [Number(number_id="NUM_abc", number="+9180XXXXXXXX", trunk_type="livekit", status="available", ...)]

# Filter
numbers = await client.numbers.list(status="available", country="IN", trunk_type="livekit")
```

### Sync Numbers from LiveKit Trunk

```python
synced = await client.numbers.sync()
# Discovers numbers from all LiveKit trunks in this project.
# Upserts — does not downgrade status of already-assigned numbers.
```

### Attach Number to Speech Pipe

```python
number = await client.numbers.attach(number_id="NUM_abc", pipe_id="pipe_xyz")
# → Number(status="assigned", pipe_id="pipe_xyz", ...)
```

Attach fails with `409` if the number is not `available`.

### Detach Number from Speech Pipe

```python
number = await client.numbers.detach(number_id="NUM_abc")
# → Number(status="available", pipe_id=None, ...)
```

Detach fails with `409` if the number is currently `in_call`.

---

## Voice Profiles

Read-only catalog of STT + TTS bundles. Profiles are global (available to all projects) or project-scoped.

### List Profiles

```python
profiles = await client.voice_profiles.list()
# → [VoiceProfile(profile_id="VP_openai_alloy", name="Alloy", tts_provider="openai", ...)]

# Filter by language (BCP-47 code)
profiles = await client.voice_profiles.list(language="hi")
```

### Get Profile

```python
profile = await client.voice_profiles.get("VP_openai_alloy")
```

Voice profiles expose full STT/TTS config. Use `profile.name` or `profile.profile_id` when creating Speech Pipes.

---

## Speech Pipes

A Speech Pipe bundles a voice profile + dialog brain config. Numbers are attached separately via the Numbers API.

### Create Speech Pipe

```python
pipe = await client.pipes.create(
    name="kyc-bot",
    voice_profile="vp_en_female_hd",    # profile_id from voice_profiles.list()
    agent_id="kyc-bot",                 # matches AgentRunner's agent_id
    recording=True,
)
# → Pipe(pipe_id="pipe_xyz", name="kyc-bot", voice_profile_id="VP_openai_alloy", ...)
```

Note: `number` and `number_id` are set via `numbers.attach()`, not here.

### Update Speech Pipe

```python
pipe = await client.pipes.update(
    "pipe_xyz",
    voice_profile="VP_sarvam_anika_hi",
    system_prompt="Updated prompt.",
)
```

### Get / List / Delete

```python
pipe = await client.pipes.get("pipe_xyz")
pipes = await client.pipes.list()
await client.pipes.delete("pipe_xyz")
# Deleting releases any attached number back to "available"
```

---

## Calls

Outbound calls are created via the platform; inbound calls are handled automatically when a call arrives on an attached number.

### Create Outbound Call

```python
call = await client.calls.create(
    pipe_id="pipe_xyz",
    to_number="+919XXXXXXXXX",
    instructions="Customer prefers Hindi. Confirm Aadhaar.",
    data={"customer_id": "C123", "loan_amount": 50000},
)
# → Call(call_id="SCL_abc", status="pending", pipe_id="pipe_xyz", ...)
```

`calls.create()` is **asynchronous**: it enqueues the call and returns
immediately with `status="pending"`. The call is dispatched on a worker once
the account has a free concurrency slot, then advances
`pending → ringing → active → completed`. Poll `calls.get(call_id)` (or use
hooks) to watch it progress.

Number resolution order:
1. `from_number` override (if provided)
2. Speech Pipe's attached number
3. `400` — no from_number available

### List / Get

```python
calls = await client.calls.list()
calls = await client.calls.list(status="active", pipe_id="pipe_xyz")
call = await client.calls.get("SCL_abc")
```

### Hangup

```python
call = await client.calls.hangup("SCL_abc")
# → Call(status="completed", end_reason="hangup", ...)
```

---

## Sessions

A session (RM_ prefix) is the media layer for a call — created when the call connects to a worker. Sessions hold transcript and recording data.

### List / Get

```python
sessions = await client.sessions.list()
session = await client.sessions.get("RM_abc")
# → Session(session_id, call_id, transcript=[TranscriptEntry(role, content, timestamp)], ...)
```

`list`/`get`/`create_token` are platform reads/writes. The lifecycle operations
below (`end`/`transfer`/`merge`) act on the **live** session and target the
**orchestrator** service, not the platform — see [Orchestrator base URL](#orchestrator-base-url).

### End

```python
result = await client.sessions.end("RM_abc")
# → OrchestratorSession-style result with the post-end state
```

### Transfer

Move the live session to a new target participant. Supports a cold transfer
(drop immediately) or a warm handoff:

```python
result = await client.sessions.transfer(
    "RM_abc",
    to_type="sip",                      # "sip" | "agent" | "webrtc" | "livekit"
    to_config={"number": "+15551230000"},
    mode="warm",                        # "cold" (default) | "warm"
    warm_handoff_ms=4000,
    drop_participant_id=None,           # optionally drop an existing participant
)
```

### Merge

Conference one or more secondary sessions into a primary session (e.g. bring a
supervisor in):

```python
result = await client.sessions.merge(
    primary_session_id="RM_primary",
    secondary_session_ids=["RM_other"],
)
```

The merge response reports a per-session outcome (merged / failed) for each
secondary session.

---

## API Keys

Provision a new API key scoped to an organisation (and optionally a project).
The returned `raw_key` is the plaintext secret and is shown **once only** at
creation time — store it immediately.

```python
key = await client.api_keys.create(name="ci-pipeline", org_id="ORG_abc")
# → ApiKey(key_id="AK_...", name="ci-pipeline", org_id="ORG_abc",
#          project_id="ORG_abc", status="active", raw_key="sk_...")

# Scope to a specific project (otherwise defaults to org_id server-side)
key = await client.api_keys.create(
    name="prod-key", org_id="ORG_abc", project_id="PRJ_xyz"
)
```

---

## Recordings

Convenience view of sessions where a recording was captured.

```python
sessions_with_recordings = await client.recordings.list()
# → [Session(session_id, recording_url="https://...", ...)]
```

Access `session.recording_url` to stream or download the audio.

---

## Transcripts

Convenience view of sessions where a transcript was captured.

```python
sessions_with_transcripts = await client.transcripts.list()
for session in sessions_with_transcripts:
    for turn in session.transcript:
        print(f"[{turn.role}] {turn.content}")
```

---

## Models Reference

### Trunk

| Field | Type | Description |
|-------|------|-------------|
| `trunk_id` | `str` | Unique identifier (`TK_...`) |
| `project_id` | `str` | Project scope |
| `name` | `str` | Display name |
| `type` | `str` | `"livekit"` or `"byo"` |
| `status` | `str` | `"active"` or `"inactive"` |
| `provider_trunk_id` | `str \| None` | Provider-side trunk ID (LiveKit) |
| `byo_config_provider` | `str \| None` | BYO carrier name |
| `byo_config_sip_domain` | `str \| None` | SIP domain |
| `byo_config_auth_username` | `str \| None` | SIP username |
| `byo_config_transport` | `str \| None` | `"tls"` or `"tcp"` |
| `created` | `datetime` | Creation timestamp |
| `modified` | `datetime` | Last modification |

### Number

| Field | Type | Description |
|-------|------|-------------|
| `number_id` | `str` | Unique identifier (`NUM_...`) |
| `project_id` | `str` | Project scope |
| `number` | `str` | E.164 phone number |
| `trunk_id` | `str \| None` | Associated trunk |
| `provider_trunk_id` | `str \| None` | Provider-side trunk ID |
| `trunk_type` | `str` | `"livekit"` or `"byo"` |
| `country` | `str \| None` | ISO country code |
| `capabilities` | `list[str]` | e.g. `["voice"]` |
| `status` | `str` | `"available"`, `"assigned"`, `"in_call"`, `"disabled"` |
| `pipe_id` | `str \| None` | Attached Speech Pipe |
| `active_call_id` | `str \| None` | Current call (when `in_call`) |

### VoiceProfile

| Field | Type | Description |
|-------|------|-------------|
| `profile_id` | `str` | Unique identifier (`VP_...`) |
| `project_id` | `str \| None` | `None` = global profile |
| `name` | `str` | Display name (e.g. `"Alloy"`) |
| `gender` | `str \| None` | `"male"`, `"female"`, `"neutral"` |
| `quality` | `str \| None` | `"standard"`, `"high"`, `"ultra"` |
| `languages` | `list[str]` | BCP-47 language codes |
| `stt_provider` | `str \| None` | STT provider |
| `stt_model` | `str \| None` | STT model |
| `tts_provider` | `str \| None` | TTS provider |
| `tts_voice` | `str \| None` | TTS voice name |
| `tts_language` | `str \| None` | TTS language code |
| `estimated_cost_per_min_usd` | `float \| None` | Estimated per-minute cost |
| `latency_ms` | `int \| None` | Expected TTS latency |

### Pipe

| Field | Type | Description |
|-------|------|-------------|
| `pipe_id` | `str` | Unique identifier (`pipe_...`) |
| `project_id` | `str` | Project scope |
| `name` | `str` | Speech Pipe name |
| `voice_profile_id` | `str \| None` | Resolved voice profile |
| `agent_id` | `str \| None` | AgentRunner's `agent_id` (the dev brain) |
| `agent_endpoint` | `str \| None` | WSS endpoint URL |
| `recording` | `bool` | Recording enabled |
| `max_call_duration_s` | `int` | Max call duration (default 3600) |
| `number_id` | `str \| None` | Attached number |
| `number` | `str \| None` | Attached phone number (E.164) |

### Call

| Field | Type | Description |
|-------|------|-------------|
| `call_id` | `str` | Unique identifier (`SCL_...`) |
| `project_id` | `str` | Project scope |
| `pipe_id` | `str \| None` | Speech Pipe that handled the call |
| `direction` | `str` | `"outbound"` or `"inbound"` |
| `from_number` | `str \| None` | Caller number |
| `to_number` | `str \| None` | Called number |
| `number_id` | `str \| None` | Platform number used |
| `trunk_id` | `str \| None` | Trunk used |
| `session_id` | `str \| None` | Associated session (RM_...) |
| `instructions` | `str \| None` | Per-call prompt override |
| `data` | `dict` | Arbitrary per-call data |
| `started_at` | `datetime \| None` | Start timestamp |
| `ended_at` | `datetime \| None` | End timestamp |
| `duration_s` | `int \| None` | Call duration |
| `status` | `str` | `"pending"`, `"ringing"`, `"active"`, `"completed"`, `"failed"`, `"cancelled"` |
| `end_reason` | `str \| None` | Why the call ended, e.g. `"completed"`, `"bridge_unreachable"`, `"media_unavailable"` |

### Session

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `str` | Unique identifier (`RM_...`) |
| `project_id` | `str` | Project scope |
| `call_id` | `str \| None` | Associated call |
| `pipe_id` | `str \| None` | Speech Pipe that handled the session |
| `status` | `str` | `"active"`, `"closed"`, `"failed"` |
| `end_reason` | `str \| None` | Why the session ended. `"media_unavailable"` means no media backend could serve the call (dispatch returned `503`); `"bridge_unreachable"` means the agent runner could not be reached |
| `transcript` | `list[TranscriptEntry]` | Ordered conversation turns |
| `recording_url` | `str \| None` | Recording URL |
| `started_at` | `datetime \| None` | Session start |
| `ended_at` | `datetime \| None` | Session end |
| `duration_s` | `int \| None` | Session duration |

### TranscriptEntry

| Field | Type | Description |
|-------|------|-------------|
| `role` | `str` | `"agent"` or `"user"` |
| `content` | `str` | Spoken text |
| `timestamp` | `datetime \| None` | When the turn occurred |

### ApiKey

| Field | Type | Description |
|-------|------|-------------|
| `key_id` | `str` | Unique key identifier (`AK_...`) |
| `name` | `str` | Human-readable label |
| `org_id` | `str` | Organisation the key is scoped to |
| `project_id` | `str` | Project scope (defaults to `org_id`) |
| `status` | `str` | `"active"` or `"revoked"` |
| `created` | `datetime \| None` | Creation timestamp |
| `raw_key` | `str \| None` | Plaintext secret — present **only** on creation |
