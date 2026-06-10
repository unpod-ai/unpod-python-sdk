# Unpod Voice Agent Playground

Pick an agent, talk to it in the browser, and watch the live transcript — all on
top of the **supervoice** API contract. Agent flows are authored with
**superdialog**; the call runtime is the **unpod-sdk** `AgentRunner`. See
[`PRD.md`](PRD.md) for the full product spec and milestones.

> **Status: M1 (WS single-agent).** Connect, talk, live transcript over the
> supervoice WS path. Metrics, flow graph, live controls, the LiveKit transport,
> and side-by-side compare are later milestones.

## Architecture (M1)

```
Browser (web/) ──audio (WS /ws/audio, protobuf)──► remote supervoice (SUPERVOICE_URL)
   │                                                         ▲
   │  POST /playground/sessions  ┌── harness (api.py) ───────┘ AgentRunner registers
   │  WS  /playground/events  ◄──┤   side-channel: session hooks → browser
   └─────────────────────────────┘
```

- **Audio** rides the supervoice WS bridge directly (16 kHz mic up, 24 kHz
  playback down).
- **Transcript / flow node** ride the harness's **own** side-channel
  (`/playground/events`), fed by SDK `Session` hooks — *not* the audio bridge
  (per PRD R2).

### Strict-transport deviation (no third-party voice client)

`web/` re-implements the WS audio protocol from scratch — there is **no**
`@pipecat-ai/*` (or any voice-client) dependency. The wire codec lives in
[`web/src/transport/protobuf.ts`](web/src/transport/protobuf.ts) (a small proto3
`Frame` encoder/decoder) and Web Audio capture/playback in
[`web/src/transport/audio.ts`](web/src/transport/audio.ts). The playground is
grep-clean of `pipecat` / `rtvi`.

## Prerequisites

The playground is **self-contained from a fresh clone** — `superdialog` installs
from PyPI (no sibling `../superdialog` checkout needed). You only need:

- An LLM key: `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`.
- A **speech backend** the harness can reach for audio. It is resolved in this
  order (see `harness/api.py`):
  1. `SUPERVOICE_URL` (explicit) — e.g. a local dev server.
  2. else `UNPOD_BASE_URL` → the **hosted** `wss://<host>` speech service
     (a fresh clone needs no local supervoice).
  3. else `ws://127.0.0.1:9000` (a local `supervoice-dev`).

  Copy [`.env.example`](.env.example) to `.env` at the repo root and set your
  keys. For hosted voice with zero local backend: `UNPOD_BASE_URL=api.unpod.ai`.

> The harness boots and serves the UI even with no speech backend reachable —
> only clicking **Connect** (which opens an audio call) needs one.

## Run it

From `unpod-sdk/` (with `.env` configured):

```bash
task pg        # builds web/dist, then serves UI + in-process agent on :9100
```

Or manually:

```bash
cd playground/web && npm install && npm run build && cd ../..
uv run --extra playground python -m playground.run
```

Open <http://localhost:9100>, click **Connect**, allow the mic, and talk.

### Fully-local dev (own supervoice + editable superdialog)

```bash
task supervoice-dev    # local supervoice on :9000 (needs ../supervoice)
task pg-local          # playground with an editable ../superdialog overlay
# or: task pg-stack    # boots a local supervoice-dev only if ../supervoice exists
#                        and no SUPERVOICE_URL/UNPOD_BASE_URL is set
```

### Frontend dev loop (hot reload)

Run the harness on `:9100` and Vite separately on `:5173` (it proxies
`/playground/*` to the harness):

```bash
uv run python -m playground.run            # terminal 1 — harness :9100
cd playground/web && npm run dev            # terminal 2 — Vite :5173
```

## Configuration

| Variable           | Default               | Purpose                                       |
| ------------------ | --------------------- | --------------------------------------------- |
| `SUPERVOICE_URL`   | `ws://127.0.0.1:9000` | Remote speech service + agent `base_url`.     |
| `PLAYGROUND_PORT`  | `9100`                | Harness server port.                          |
| `AGENT_NAME`       | `faq_bot`             | Catalog agent to run (see `agents/catalog.py`).|
| `UNPOD_API_KEY`    | `dev-key`             | Agent registration key.                       |
| `OPENAI_API_KEY`   | —                     | LLM key (or `ANTHROPIC_API_KEY`).             |

## Adding an agent

1. Add `agents/<name>.py` exposing `build(llm: str) -> <dialog machine>`.
2. Add one `AgentSpec` line to `CATALOG` in `agents/catalog.py`.

## Layout

```
playground/
├── agents/      # superdialog flows + builders (catalog.py, faq_bot.py)
├── harness/     # FastAPI control plane (api, runner, control, events, provisioning)
├── web/         # React + hand-rolled WS transport (no pipecat/rtvi deps)
└── run.py       # launcher
```
