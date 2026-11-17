# Telephony — numbers, call control and call events

`client.telephony` is the part of the SDK that works with phone calls on Unpod
numbers. It does three jobs:

| Job | Where | Status |
|---|---|---|
| Attach your numbers to an AI agent (or to your own app) | `client.telephony.numbers` | Shipped |
| Control live calls yourself: dial, bridge two people, play audio, speak text, transfer, DTMF, record | `client.telephony.calls`, `client.telephony.inbound` | **Preview** |
| Receive signed call events on your webhook | `unpod.telephony.parse_call_event`, `client.telephony.webhook_secret` | **Preview** |

The full product guide, with cURL for every request, lives at
[docs.unpod.ai/telephony](https://docs.unpod.ai/telephony/introduction). This page
is the SDK view: what to install, how to configure it, and which method does what.

- [Install](#install)
- [Configure](#configure)
- [Check your setup](#check-your-setup)
- [Numbers](#numbers)
- [Call control (preview)](#call-control-preview)
- [Inbound calls (preview)](#inbound-calls-preview)
- [Call events and webhooks (preview)](#call-events-and-webhooks-preview)
- [Errors](#errors)
- [Blocking or async client](#blocking-or-async-client)
- [Trunks (beta)](#trunks-beta)
- [Troubleshooting](#troubleshooting)
- [Method reference](#method-reference)

## Install

Python 3.12 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install "unpod>=0.6.2"
```

`0.6.2` is the first release on PyPI with `client.telephony.calls`,
`client.telephony.inbound` and `parse_call_event`. Telephony needs no extras.

A few newer methods ship in **0.7.0**, which is not on PyPI yet: recording
control (`stop_recording`, `pause_recording`, `resume_recording`,
`download_recording`), `parse_digits_request`, `EventName`, and the extra
call options (`time_limit`, `sip_headers`, `fallback_url`, ...). They are
marked *0.7.0* below. Until the release, install from GitHub to use them:

```bash
pip install "unpod @ git+https://github.com/unpod-ai/unpod-python-sdk"
```
The webhook example below uses FastAPI, which is **not** an SDK dependency:

```bash
pip install fastapi uvicorn   # only for the webhook receiver
```

## Configure

You need three values. Get both credentials from the Unpod dashboard:

| Variable | Value | Where to find it |
|---|---|---|
| `UNPOD_BASE_URL` | `https://unpod.ai` | Always this host. **Set it**: without it the SDK falls back to `api.unpod.ai`, which does not resolve |
| `UNPOD_PLATFORM_TOKEN` | Your API key | [Settings → Security → API keys](https://unpod.ai/settings/security/api-keys/) → **Generate New**. It acts as you, so keep it secret |
| `UNPOD_ORG_HANDLE` | Your org handle, e.g. `acme` | [Settings → Workspace → General](https://unpod.ai/settings/workspace/general/) → the **Domain** value without the leading `@` |

Put them in a `.env` file next to your script. The SDK loads `.env` by itself
when you `import unpod`:

```bash
# .env
UNPOD_BASE_URL=https://unpod.ai
UNPOD_PLATFORM_TOKEN=<your-api-key>
UNPOD_ORG_HANDLE=<your-org-handle>
```

Then `Client()` / `AsyncClient()` with no arguments picks them up and sends
`Authorization: Token <key>` plus `Org-Handle: <handle>` on every telephony
request.

> **Use the platform token, not `UNPOD_API_KEY`.** Telephony is scoped to an
> org, so it only accepts token auth with an org handle. A Bearer
> `UNPOD_API_KEY` alone gets 401/403. When `UNPOD_PLATFORM_TOKEN` is set it
> always wins over `UNPOD_API_KEY`.

To pass credentials in code instead of the environment:

```python
from unpod import AsyncClient, TokenAuth

client = AsyncClient(
    base_url="https://unpod.ai",
    auth=TokenAuth(token="<your-api-key>", org_handle="acme"),
)
```

## Check your setup

```python
from unpod import Client

with Client() as client:
    for n in client.telephony.overview():
        print(n.number, n.connection_state, n.agent_id)
```

An empty list is fine: it means your org has no numbers attached yet. A 401
means the token is wrong; a 403 usually means the org handle is wrong or you
are not a member of that org.

## Numbers

Every method takes the phone number itself in E.164 form (`+` and country
code, e.g. `+919876543210`), never an internal id.

### List

```python
from unpod import Client

with Client() as client:
    for n in client.telephony.numbers.list():
        print(n.number, n.status)
```

`status` is one of:

| `status` | Meaning |
|---|---|
| `not_assigned` | Free in the pool. You can attach it |
| `assigned` | Already attached to your org |
| `closed` | Not attachable (for example, inactive) |

The list holds the free pool **plus** your org's own numbers. Another org's
numbers never appear.

### Attach

```python
result = client.telephony.numbers.attach("+919876543210", agent_id="sales-agent")
for r in result.numbers:
    print(r.number, r.ok, r.connection_state, r.error)
```

`attach` takes one number or a list. It is **partial-success**: check `ok` and
`error` on every item of `result.numbers`, because one number can fail while
another succeeds.

`attach_type` picks who answers the number:

| `attach_type` | Calls go to | Also pass |
|---|---|---|
| `"agent"` (default) | Your AI agent | `agent_id` |
| `"pipeline"` | A speech pipe | `pipe_id` (required) |
| `"human_call"` | Nobody automatically: your app drives the call with [call control](#call-control-preview) and the [inbound config](#inbound-calls-preview) | Do **not** pass `agent_id` |

```python
# A number your own app answers, with no AI agent
client.telephony.numbers.attach("+919876543210", attach_type="human_call")
```

### Detach

```python
result = client.telephony.numbers.detach("+919876543210")
```

The number is released, not deleted, so you can attach it again later. Also
partial-success.

### Overview

`client.telephony.overview()` returns one `NumberOverview` per number with
`connection_state`, `agent_id`, `termination_kind` and `in_sync` — use it to see
whether a number is ready. A number whose `connection_state` is `NOT_LINKED`
cannot place calls yet.

## Call control (preview)

> **Preview, off by default.** Ask the Unpod team to switch on the call API for
> your org. Until then every call-control request fails with
> `TelephonyAPIError` status 403, code `call_api_disabled`.

`client.telephony.calls` are phone calls you control yourself. They are **not**
`client.calls` — those are calls handled by an AI agent.

### Words used here

| Word | Meaning |
|---|---|
| Call | One conversation, identified by `call_id`. It has at most two participants |
| Leg | One participant's phone connection inside a call, identified by `leg_id` |
| `role` | A free-text label you put on a leg (`"agent"`, `"customer"`, ...). It comes back on that leg's events |
| `from_` | The caller ID. Must be one of **your org's** numbers |
| `to` | Who to dial: an E.164 number or a SIP URI |

### Dial two people and bridge them

```python
import asyncio

from unpod import AsyncClient
from unpod.telephony import TelephonyAPIError

FROM = "+919000000001"      # your Unpod number
AGENT = "+919811111111"     # first person
CUSTOMER = "+919822222222"  # second person


async def main() -> None:
    async with AsyncClient() as client:
        calls = client.telephony.calls

        # 1. Dial the first person. Returns at once with state "initiated";
        #    progress arrives as events at status_callback_url.
        call = await calls.create(
            FROM, AGENT,
            role="agent",
            ring_timeout=30,
            status_callback_url="https://your-app.example.com/call-events",
            status_callback_format="json",
        )
        print("call", call.call_id, call.state)

        try:
            # 2. Dial the second person; Unpod bridges them when they answer.
            leg = await calls.add_participant(call.call_id, FROM, CUSTOMER, role="customer")
            print("leg", leg.leg_id)

            # 3. Say something to both legs.
            await calls.speak(call.call_id, "Connecting you now.")
        except TelephonyAPIError as e:
            print("call API error:", e.status_code, e.code, e.message)
            await calls.end(call.call_id)


asyncio.run(main())
```

In a real app you add the second participant from your webhook, when the
first leg's `Answered` event arrives, instead of right after `create`.

### What you can do on a live call

| Do this | Method |
|---|---|
| Look at a call and its legs | `get(call_id)`; `list(status=, direction=, page=)` for active calls |
| Play an audio file | `play_audio(call_id, url="https://.../hold.mp3", leg_id=None, loop=False)` → `PlayResult.play_id` |
| Stop audio | `stop_audio(call_id, leg_id=None, play_id=None)` |
| Speak text (max 1000 chars) | `speak(call_id, text, voice=None, language=None, leg_id=None)` |
| Send keypad tones | `send_dtmf(call_id, "1234#", leg_id=None)` |
| Transfer | `transfer(call_id, target, type="blind")`; for `type="attended"` finish with `transfer_complete(call_id, consult_leg)` or `transfer_cancel(...)` |
| Record | `record(call_id)` to start; `get_recording(call_id)` → `Recording.url` (two-channel WAV, first leg left, second right) |
| Pause, resume or stop recording *(0.7.0)* | `pause_recording(call_id)`, `resume_recording(call_id)`, `stop_recording(call_id)` |
| Save the recording to a file *(0.7.0)* | `download_recording(call_id, "call.wav")` → `Path` |
| Drop one person | `remove_participant(call_id, leg_id)` |
| End the call | `end(call_id)` ends every leg; `hangup(call_id, reason=, code=)` adds a reason and SIP code |

Omit `leg_id` to act on every leg of the call.

Extra options on `create` and `add_participant` *(0.7.0)*: `time_limit`
(max call seconds, 1–86400), `sip_headers` (sent to the carrier as
`X-PH-<key>`), `fallback_url` / `fallback_method` (used when `answer_url`
fails), `status_callback_events` (which event groups to send),
`end_mpc_on_exit` (this person leaving ends the call). `play_audio` also takes
`loop=<n>` (play n times; `0` or `True` until stopped) and `length=<seconds>`.

### Rules the server enforces

- `from_` must be one of your org's numbers → otherwise 400 `from_not_owned`.
- Every URL you pass (`status_callback_url`, `answer_url`,
  `recording_callback_url`, `play_audio(url=)`, `dial_music`) must be a
  **public `https`** URL of at most 2048 characters → otherwise 400
  `invalid_url`. `dial_music` also accepts `"none"` (silence) or `"real"`
  (normal ringback).
- `play_audio(file=...)` is refused through the hosted API; use `url=`.
- A call holds two participants at most; a third `add_participant` → 409.
- `speak` → 503 `tts_unavailable` when text-to-speech is not configured.
- `get_recording` → 409 while the recording is still being written; retry.

### Safe retries

`create` and `add_participant` dial a phone, so a blind retry could ring
someone twice. The SDK sends a fresh `Idempotency-Key` on every call. If you
retry a request yourself, pass the **same** key both times and the server
returns the first result instead of dialing again:

```python
key = "order-4711-agent-leg"
call = await calls.create(FROM, AGENT, idempotency_key=key)
```

Attach your own ids to a leg with `metadata={"order_id": "4711"}`; they come
back on that leg's events.

## Inbound calls (preview)

For a number attached with `attach_type="human_call"`, the inbound config says
what happens when someone calls it. You can set it for a whole trunk, and
override it per number; the number override wins field by field.

```python
from unpod.telephony import InboundConfig

cfg = InboundConfig(
    answer_url="https://your-app.example.com/answer",       # your app decides what to do
    answer_fallback="static_routing",                       # if your answer URL fails
    status_callback_url="https://your-app.example.com/call-events",
    status_callback_format="json",
)
client.telephony.inbound.set_did("+919000000001", cfg)   # one number
print(client.telephony.inbound.get_did("+919000000001"))  # effective config
```

`set_did` / `get_did` work on one number; `set_trunk` / `get_trunk` on a trunk
by name. A plain `dict` works in place of `InboundConfig`.

When a call arrives, Unpod POSTs to your `answer_url` and your app answers with
instructions (play, speak, dial, ...). The answer format is described in
[Answer URL & XML](https://docs.unpod.ai/telephony/apis/answer-url).

## Call events and webhooks (preview)

Unpod POSTs call events to the `status_callback_url` you set on a call or in
the inbound config: `Initiated`, `Ringing`, `Answered`, `Hangup`, `DTMF`,
`PlayStarted`, ... Each request is signed with your org's **webhook secret**.

### 1. Create the secret once

```python
from unpod import Client

with Client() as client:
    s = client.telephony.webhook_secret.rotate()  # org admin only
    print(s.secret)  # shown in full ONLY here — store it as UNPOD_WEBHOOK_SECRET
```

Each `rotate()` invalidates the previous secret. `webhook_secret.get()` shows
it masked. Until your org has a secret, events go out unsigned.
`webhook_secret.reveal()` *(0.7.0)* is meant to return the current secret
without rotating, but the server does not serve it yet (404).

### 2. Verify and parse each event

```python
import os

from fastapi import FastAPI, HTTPException, Request
from unpod.telephony import WebhookSignatureError, parse_call_event

SECRET = os.environ["UNPOD_WEBHOOK_SECRET"]
app = FastAPI()


@app.post("/call-events")
async def call_events(request: Request):
    body = await request.body()  # the RAW body: do not parse it first
    try:
        ev = parse_call_event(body, request.headers, secret=SECRET)
    except WebhookSignatureError:
        raise HTTPException(401)

    if ev.event == "Answered":
        print(ev.call_id, ev.leg_id, ev.role, "answered")
    elif ev.event == "Hangup":
        print(ev.call_id, ev.hangup_cause, ev.sip_code, ev.duration)
    return {"ok": True}  # any 2xx acknowledges the event
```

Run it with `uvicorn app:app --port 8000` behind a public https URL.

- Pass the raw body and the headers. Re-encoding the body breaks the signature.
- Form-encoded and JSON events both work; `Content-Type` decides.
- `parse_call_event` rejects signatures older than 300 s (`tolerance=`).
- The same event can arrive twice: de-duplicate on `ev.event_id`.
- Reply 2xx within 10 s, or Unpod retries the event.
- `ev.call_id` is the same on every event of a call; `ev.leg_id` differs per
  leg. `ev.raw` keeps every field exactly as received.
- Some requests are not signed yet: recording events and outbound answer-URL
  requests. Parse those with `verify=False` and confirm anything important
  with the API (for example `get_recording(ev.call_id)`).

Event names are also available as constants on `unpod.telephony.EventName`
*(0.7.0)*, e.g. `EventName.MPC_END`; `ev.event` stays a plain string. When your
answer-URL app collects keypad input (`<GetDigits>` / `<GetInput>`), parse the
action request with `parse_digits_request(body, headers, secret)` *(0.7.0)*:
it verifies the same signature and returns `DigitsRequest.digits`.

Every event name and field, with hangup causes, is listed in
[Webhooks](https://docs.unpod.ai/telephony/apis/webhooks).

## Errors

The telephony methods raise two kinds of exceptions:

| Methods | Exception | Read |
|---|---|---|
| `calls.*`, `inbound.*`, `webhook_secret.*` | `unpod.telephony.TelephonyAPIError` | `.status_code`, `.code` (machine code — branch on this), `.message`, `.body` |
| `numbers.*`, `trunks.*`, `overview()` | `httpx.HTTPStatusError` | `e.response.status_code`, `e.response.text` (the reason is in the body) |
| `parse_call_event` | `unpod.telephony.WebhookSignatureError` | signature missing, wrong or too old |

Common `TelephonyAPIError.code` values:

| `code` | Status | Fix |
|---|---|---|
| `call_api_disabled` | 403 | Ask Unpod to switch on the call API for your org |
| `organization_required` | 400 | Set `UNPOD_ORG_HANDLE` |
| `from_not_owned` | 400 | Use one of your org's numbers as `from_` |
| `invalid_url` | 400 | Use a public `https` URL (≤ 2048 chars) |
| `not_found` | 404 | Wrong `call_id`, or the call belongs to another org |
| `participant_limit` / `conflict` | 409 | A call holds two participants; or the recording is not ready yet |
| `tts_unavailable` | 503 | Text-to-speech is not configured; use `play_audio` |
| `rate_limited` | 429 | Slow down and retry |

```python
import httpx

from unpod.telephony import TelephonyAPIError

try:
    await client.telephony.calls.create(FROM, AGENT)
except TelephonyAPIError as e:
    if e.code == "call_api_disabled":
        ...

try:
    await client.telephony.numbers.attach("+919876543210", agent_id="sales-agent")
except httpx.HTTPStatusError as e:
    print(e.response.status_code, e.response.text)
```

## Blocking or async client

Every method exists on both clients with the same name and arguments:

```python
# Blocking: scripts, Django views, CLIs
from unpod import Client

with Client() as client:
    print(client.telephony.numbers.list())
```

```python
# Async: FastAPI, asyncio apps
import asyncio

from unpod import AsyncClient


async def main() -> None:
    async with AsyncClient() as client:
        print(await client.telephony.numbers.list())


asyncio.run(main())
```

`Client` cannot run inside an event loop that is already running (Jupyter,
FastAPI handlers): it raises `RuntimeError`. Use `AsyncClient` there.

## Trunks (beta)

`client.telephony.trunks` (`list`, `create`, `get`, `delete`,
`attach_numbers`, `detach_numbers`) is for bringing **your own SIP carrier**.
It is beta and not needed for Unpod numbers: to wire a number to an agent use
`numbers.attach`. Trunk methods take numeric `number_ids`, not E.164 numbers.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `httpx.ConnectError` / name resolution failure for `api.unpod.ai` | `UNPOD_BASE_URL` not set | Set `UNPOD_BASE_URL=https://unpod.ai` |
| 401 on every request | Wrong token, or only `UNPOD_API_KEY` set | Set `UNPOD_PLATFORM_TOKEN` |
| 403 on `numbers.*` / `overview()` | Wrong org handle, or not a member | Check `UNPOD_ORG_HANDLE` (no `@`) |
| `TelephonyAPIError 403 call_api_disabled` | Call API off for your org | Ask the Unpod team |
| `TelephonyAPIError 400 from_not_owned` | `from_` is not your number | Use a number from `numbers.list()` with `status="assigned"` |
| `TelephonyAPIError 403` "from is not a number available to this organization" | `from_` number not ready yet | Check `overview()`: `connection_state` must not be `NOT_LINKED` |
| `WebhookSignatureError` on every event | Body was parsed before verifying, or old secret after `rotate()` | Pass `await request.body()`; update `UNPOD_WEBHOOK_SECRET` |
| `RuntimeError: Client cannot be used from a running event loop` | Blocking `Client` inside async code | Use `AsyncClient` |
| Values from `.env` ignored | `.env` is not in the working directory | Run from the folder that holds `.env`, or `export` the variables |

Turn on request logging to see every URL, status and error body:

```python
import unpod

unpod.enable_logging("DEBUG")  # prints to stderr; error bodies are logged at WARNING
```

## Method reference

All under `client.telephony`. Source: `src/unpod/telephony/`.

| Method | Returns |
|---|---|
| `overview()` | `list[NumberOverview]` |
| `numbers.list()` | `list[Number]` |
| `numbers.attach(numbers, *, agent_id=, attach_type=, pipe_id=, bridge_slug=, region=)` | `AgentAttachResult` |
| `numbers.detach(numbers)` | `AgentDetachResult` |
| `calls.create(from_, to, *, role=, caller_name=, ring_timeout=, dial_music=, status_callback_url=, status_callback_format=, recording_callback_url=, record=, answer_url=, metadata=, idempotency_key=, ...)` | `TelephonyCall` |
| `calls.list(*, page=, page_size=, status=, direction=, caller=, callee=, number=, since=)` | `CallList` |
| `calls.get(call_id)` | `TelephonyCall` |
| `calls.add_participant(call_id, from_, to, *, ...same options as create)` | `Leg` |
| `calls.remove_participant(call_id, leg_id)` | `None` |
| `calls.play_audio(call_id, *, url=, leg_id=, loop=, length=, interrupt_on_dtmf=)` | `PlayResult` |
| `calls.stop_audio(call_id, *, leg_id=, play_id=)` | `None` |
| `calls.speak(call_id, text, *, voice=, language=, leg_id=, loop=)` | `PlayResult` |
| `calls.send_dtmf(call_id, digits, *, leg_id=)` | `None` |
| `calls.transfer(call_id, target, *, type="blind", leg_id=)` | `None` |
| `calls.transfer_complete(call_id, consult_leg)` / `calls.transfer_cancel(call_id, consult_leg)` | `None` |
| `calls.record(call_id, *, transcribe=)` | `None` |
| `calls.pause_recording(call_id)` / `resume_recording` / `stop_recording` *(0.7.0)* | `None` |
| `calls.download_recording(call_id, path)` *(0.7.0)* | `pathlib.Path` |
| `calls.get_recording(call_id)` | `Recording` |
| `calls.end(call_id)` / `calls.hangup(call_id, *, reason=, code=)` | `None` |
| `inbound.set_did(number, config)` / `inbound.get_did(number)` | `InboundConfig` |
| `inbound.set_trunk(name, config)` / `inbound.get_trunk(name)` | `InboundConfig` |
| `webhook_secret.get()` / `webhook_secret.rotate()` | `WebhookSecret` |
| `webhook_secret.reveal()` *(0.7.0, server route not live yet)* | `WebhookSecret` |
| `unpod.telephony.parse_call_event(body, headers, secret, *, tolerance=300, verify=True)` | `CallEvent` |
| `unpod.telephony.verify_signature(body, signature, secret, *, tolerance=300)` | `bool` |
| `unpod.telephony.parse_digits_request(body, headers, secret, *, tolerance=300, verify=True)` *(0.7.0)* | `DigitsRequest` |

Every model accepts fields it does not know yet, so a newer server never
breaks an older SDK.

A runnable script: [`examples/sbc_call_control.py`](../examples/sbc_call_control.py).
