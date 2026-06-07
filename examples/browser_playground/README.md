# Browser Voice Playground

A self-contained dev playground for testing your voice agent in the browser.
It serves a small web UI, runs your agent in-process, and connects to a **remote
supervoice** speech service (STT/TTS/audio bridge) referenced only by URL — nothing
about supervoice is imported here.

```
Browser ──► playground server (:9100) ──► serves UI, proxies /connect
   │                                          │
   └── mic/audio over WebSocket ──────────────┴──► remote supervoice (SUPERVOICE_URL)
                                              ▲
        your agent (agent.py) registers ──────┘
```

## Prerequisites

- A running **supervoice** speech service reachable at `SUPERVOICE_URL` (local or
  remote). The browser routes (`/connect`, `/ws/audio`) are served by the dev app:
  `cd supervoice && uv run uvicorn supervoice.dev.app:create_dev_app --factory --port 9000`.
  (Note: this is the dev speech app, not `supervoice.main:app` — the platform API
  does not expose the browser-audio routes.)
- An LLM key: `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`.

## Run it

From `unpod-sdk/`:

```bash
cp examples/browser_playground/.env.example examples/browser_playground/.env
# edit .env — set SUPERVOICE_URL and your LLM key
task playground
```

This builds the UI, starts the server on `http://localhost:9100`, and runs your
agent in-process. Open the page, click **Connect**, allow the mic, and talk.

## Configuration (`.env`)

| Variable          | Default               | Purpose                                            |
| ----------------- | --------------------- | -------------------------------------------------- |
| `SUPERVOICE_URL`  | `ws://127.0.0.1:9000` | Remote speech service + agent `base_url`.          |
| `PLAYGROUND_PORT` | `9100`                | Local UI server port (kept off `:9000`).           |
| `UNPOD_API_KEY`   | `dev-key`             | Agent registration key.                            |
| `AGENT_ID`        | `browser-playground`  | Agent identifier.                                  |
| `OPENAI_API_KEY`  | —                     | LLM key (or `ANTHROPIC_API_KEY`).                  |
| `FLOW_JSON_PATH`  | — (empty)             | Optional flow JSON → drives a `DialogMachine`.     |

With `FLOW_JSON_PATH` unset, the agent uses a plain `LLMAgent`. Set it to a flow
JSON file to drive a `DialogMachine` (with a spoken greeting on call start).

## Two-process mode (run your own agent)

To iterate on `agent.py` separately from the server, start the server without its
in-process agent and run the agent yourself:

```bash
# terminal 1 — server + UI only
EXTERNAL_AGENT=1 task playground

# terminal 2 — your agent
cd examples/browser_playground
uv run python -c "import asyncio, agent; asyncio.run(agent.run_agent())"
```

## Files

- `run.py` — server: serves `pipe-ui/dist`, proxies `/connect`, runs the agent.
- `agent.py` — the agent you edit (`entrypoint` + `AgentRunner`).
- `_urls.py` — derives the audio/connect URLs from `SUPERVOICE_URL`.
- `pipe-ui/` — Vite/TypeScript frontend (`task playground-ui-build` rebuilds `dist/`).
