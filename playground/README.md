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

- A running **supervoice** dev speech service exposing `POST /connect` +
  `WS /ws/audio`:
  ```bash
  cd supervoice && uv run uvicorn supervoice.dev.app:create_dev_app --factory --port 9000
  ```
- An LLM key: `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`.

## Run it

From `unpod-sdk/`:

```bash
# 1. Build the web UI (served by the harness from web/dist)
cd playground/web && npm install && npm run build && cd ../..

# 2. Start the harness + in-process agent (serves UI on :9100)
uv run python -m playground.run
```

Open <http://localhost:9100>, click **Connect**, allow the mic, and talk.

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
