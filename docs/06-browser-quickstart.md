# Browser Quickstart — Test Your Agent in Chrome

> **Archived 2026-07-29 — the bring-up below does not run.** Step 3 points at
> `auto_rl`, an unrelated project with no `run.py`; step 2's `.env.example`
> lives at `examples/browser_playground/.env.example`, not the repo root; and
> the UI is served on `:9100`, not `:9000` (`:9000` is the supervoice dev
> speech app's `/connect` + `/ws/audio`). The steps are also not repairable in
> isolation: the dev speech app registers runners through
> `supervoice/dev/relay.py::workers_endpoint`, which acknowledges registration
> without a `transport_ack`, and a default (`dial_out`) runner rejects that
> handshake — `connectivity/runner.py::AgentRunner._control_session` raises
> "orchestrator did not acknowledge dial_out transport". Fixing that is a code
> change, not a doc change.
>
> **The maintained browser path** is the in-repo example
> [`examples/browser_playground/`](../examples/browser_playground/README.md):
> `task playground` serves the UI on `:9100` and runs the agent, against a
> supervoice dev speech app started separately with
> `uv run uvicorn supervoice.dev.app:create_dev_app --factory --port 9000`.
> Its README is the current source of truth; this page is kept only for the
> links that still point at it.

The fastest way to test a voice agent: no phone number, no telephony setup.
The browser connects via WebSocket audio directly to `supervoice`, which routes
speech to your `AgentRunner` over the bridge protocol.

```
Browser mic/speaker
    │  WebSocket audio (protobuf)
    ▼
supervoice.dev  (STT → bridge → TTS)    ← hidden, you never touch this
    │  text only (WSS bridge)
    ▼
AgentRunner  ←  your entrypoint()       ← the only code you write
    │
    ▼
LLMAgent / DialogMachine                ← your brain
```

---

## Step 1 — Install

```bash
# In your project
uv add unpod superdialog
```

## Step 2 — Set API keys

```bash
cp .env.example .env   # fill in your keys
```

Required keys:

| Key | Used for |
|-----|----------|
| `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` | Your LLM |
| `DEEPGRAM_API_KEY` | Speech-to-text (supervoice.dev) |
| `CARTESIA_API_KEY` | Text-to-speech (supervoice.dev) |

## Step 3 — Start the dev server (Terminal 1)

The dev server (`supervoice.dev`) handles all audio infrastructure — STT, TTS,
WebSocket transport. It runs independently of your agent code.

```bash
cd auto_rl
uv run python run.py
```

Leave this running. Open **http://localhost:9000** — you will see the browser UI.

## Step 4 — Write and run your agent (Terminal 2)

This is the only code you write. It connects to the running dev server as a worker.

```python
# agent.py
from unpod import AgentRunner, CallContext
from superdialog import LLMAgent

async def entrypoint(ctx: CallContext) -> None:
    ctx.session.dialog_machine = LLMAgent(
        llm="anthropic/claude-haiku-4-5-20251001",
        system_prompt="You are a helpful voice assistant. Keep answers under 3 sentences.",
    )
    await ctx.session.run()

if __name__ == "__main__":
    AgentRunner(
        entrypoint=entrypoint,
        agent_id="my-agent",
        base_url="ws://127.0.0.1:9000",  # local dev server
        api_key="dev-key",               # local dev only
    ).start()
```

```bash
uv run python agent.py
```

## Step 5 — Connect from the browser

Navigate to **http://localhost:9000**, click **Connect**, allow mic access, speak.

You will see:
- Real-time transcript (your words + agent responses)
- STT latency badge on each user turn
- TTFA (time-to-first-audio) and total latency badges on each agent turn

---

## What connects where

```
http://localhost:9000          → pipe-ui (browser UI, served as static files)

POST /connect?agent_id=X      → supervoice.dev — returns { ws_url }
WS   /ws/audio                → supervoice.dev — audio pipeline (STT/TTS)
WS   /v1/internal/workers     → supervoice.dev — AgentRunner registers here (persistent)
WS   /bridge/agent            → supervoice.dev — per-call bridge (SDK side)
WS   /bridge/voice            → supervoice.dev — per-call bridge (Speech Worker side)
```

Your code only touches `WS /v1/internal/workers`. Everything else is internal.

---

## Switching to production (telephony)

When you're happy with the agent behaviour, move to production:

1. Remove `base_url` — SDK defaults to `wss://api.unpod.ai` automatically
2. Replace `api_key="dev-key"` with your real `UNPOD_API_KEY`
3. Purchase a number and bind the Speech Pipe via the Management SDK

See [Quickstart](01-quickstart.md) for the full telephony path.
