# Unpod SDK — Overview

## What is Unpod SDK?

Unpod SDK (`pip install unpod`) is the developer-facing Python package for building voice agents on Unpod's infrastructure. It handles everything between your brain (dialog logic) and a live phone call — without exposing audio, SIP, STT, or TTS internals.

**Single architectural commitment:** The wire between Unpod infrastructure and your code carries **text, not audio**. You bring the brain; Unpod brings the voice.

## What Unpod Owns vs What You Own

| Unpod (invisible)                        | You (the developer)              |
|------------------------------------------|----------------------------------|
| PSTN / SIP / carriers                    | LLM choice                      |
| Phone numbers                            | Prompts & flows                  |
| Media server / Room                      | Tools & business logic           |
| STT + TTS + provider rotation            | Conversation memory              |
| Voice profile catalog                    | Per-call business logic          |
| Channel adapters (WA, SMS, widget)       | Cost optimization (model swaps)  |
| Recording capture                        | Your own LLM billing             |
| Billing for voice minutes                |                                  |

## Three Layers

```
LAYER 1 (Local)      Your brain — DialogMachine / LangChain / HTTP / MCP
                     ↕ text via WSS (Bridge protocol)
LAYER 2 (Infra)      AgentRunner + Session (connectivity)
                     supervoice — speech service (STT, TTS, WebSocket, telephony, WebRTC)
                     Management SDK: numbers, voice profiles, Speech Pipes, calls
                     ↕ REST
LAYER 3 (Control)    Unpod Control Plane — UI, dashboards, recordings
```

## Connectivity Options

supervoice exposes your agent to the outside world via three transports — you pick one:

| Transport | Use case | How browser/phone connects |
|-----------|----------|---------------------------|
| WebSocket | Browser testing, web widget | `POST /connect?agent_id=X` → `WS /ws/audio` |
| Telephony | Phone calls (PSTN/SIP) | Number synced/attached via Management SDK |
| WebRTC | Native mobile / embedded | Room join via supervoice room engine |

All three converge at the same bridge: your `AgentRunner` receives plain text turns regardless of transport.

## Package Scope

`unpod-sdk` contains two major components:

### 1. Management SDK (REST Client)

CRUD operations against Unpod's Control Plane:

- **Numbers** — list, sync from SIP trunks, attach/detach, release, bring-your-own
- **Voice Profiles** — browse catalog (read-only, per-minute pricing)
- **Speech Pipes** — create bindings (number + voice profile + brain endpoint)
- **Calls** — trigger outbound, list, get status, hangup
- **Recordings** — list, download
- **Transcripts** — retrieve per-call transcripts

### 2. Connectivity SDK (WSS Runtime)

Runtime components for handling live calls:

- **AgentRunner** — long-lived process, persistent WSS to Unpod orchestrator, dispatches incoming calls to your entrypoint
- **Session** — per-call object with hooks (observe) and controls (act)
- **CallContext** — per-call metadata envelope wrapping Session
- **DialogAdapter** — protocol for plugging any brain into Session's `dialog_machine` slot

### What It Does NOT Contain

- No STT / TTS / audio processing (that's Unpod infrastructure)
- No dialog machine internals (that's `superdialog`, a separate package)
- No orchestrator / worker internals (that's `supervoice`, the backend service)
- No SIP / FreeSWITCH configuration

## Installation

```bash
# Core SDK — management client + connectivity runtime
pip install unpod

# With superdialog integration (recommended)
pip install unpod[dialog]

# With LangChain adapter
pip install unpod[langchain]

# With MCP adapter
pip install unpod[mcp]

# superdialog standalone (no Unpod infra, text-only)
pip install superdialog
```

## Relationship to Other Packages

```
┌─────────────────────────────────────────────────────┐
│  Developer Process                                  │
│                                                     │
│  ┌──────────┐    ┌──────────────────────────────┐   │
│  │superdialog│───▶│  unpod (this SDK)             │   │
│  │(optional) │    │  ├── Management (REST)        │   │
│  └──────────┘    │  ├── Connectivity (WSS)       │   │
│                  │  └── Adapters                  │   │
│  ┌──────────┐    │      ├── SuperDialogAdapter   │   │
│  │langchain │───▶│      ├── LangChainAdapter     │   │
│  │(optional) │    │      ├── HTTPAdapter          │   │
│  └──────────┘    │      └── MCPAdapter           │   │
│                  └──────────────┬─────────────────┘   │
│                                │ WSS (text only)      │
└────────────────────────────────┼─────────────────────┘
                                 │
┌────────────────────────────────┼─────────────────────┐
│  Unpod Infrastructure          │                      │
│                                ▼                      │
│  ┌──────────────────────────────────────────────┐    │
│  │  supervoice (backend service)                 │    │
│  │  ├── Orchestrator (dispatch, sessions, rooms) │    │
│  │  ├── Workers (PipeCat pipeline, STT/TTS)      │    │
│  │  ├── Bridge (text routing)                    │    │
│  │  └── Control Plane (numbers, profiles, auth)  │    │
│  └──────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────┘
```

## Target Experience

- **Time to first call:** < 10 minutes
- **Post-onboarding engineering effort:** < 2 hours/month
- **Iteration cycle:** `git push` → next call uses new logic. No tickets.

## Next Steps

- [Architecture](01-architecture.md) — package structure, data flow, protocol details
- [Management SDK](02-management-sdk.md) — REST client API reference
- [Connectivity SDK](03-connectivity-sdk.md) — AgentRunner, Session, hooks, controls
- [Adapters](04-adapters.md) — DialogAdapter protocol and bundled adapters
- [Quickstart](05-quickstart.md) — 10 steps to your first call
