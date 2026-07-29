# Unpod Python SDK

Developer SDK for [Unpod](https://unpod.ai) voice infrastructure — management, connectivity, and adapters for building voice agents that talk over real phone calls, browsers, and WebRTC.

**Single architectural commitment:** the wire between Unpod infrastructure and your code carries **text, not audio**. You bring the Agent Runner; Unpod brings the voice.

## Installation

```bash
pip install unpod

# With superdialog integration (recommended)
pip install "unpod[dialog]"

# With LangChain adapter
pip install "unpod[langchain]"

# With MCP adapter
pip install "unpod[mcp]"
```

Or with [uv](https://docs.astral.sh/uv/): `uv add unpod` (extras: `uv add "unpod[dialog]"`).

To install the latest unreleased code from source:

```bash
pip install "unpod @ git+https://github.com/unpod-ai/unpod-python-sdk"
```

## What's Inside

```
unpod
├── Management SDK (REST)   numbers, voice profiles, speech pipes, calls,
│                           sessions, trunks, recordings, transcripts, api keys
├── Connectivity SDK (WSS)  AgentRunner, Session, CallContext, hooks
└── Adapters                superdialog, LangChain, OpenAI, Anthropic, HTTP, MCP
```

- **Management SDK** — CRUD against the Unpod Control Plane: manage numbers (sync/attach/release), browse voice profiles, bind Speech Pipes, trigger and inspect calls.
- **Connectivity SDK** — runtime for live calls: a long-lived `AgentRunner` receives plain-text turns over WSS and dispatches them to your agent, regardless of transport (phone, browser, WebRTC).
- **Adapters** — plug any dialog logic into a call: `superdialog` dialog machines, LangChain runnables, your own HTTP endpoint, or an MCP server.

## Quick Example

Configure once — the [Quickstart](https://github.com/unpod-ai/unpod-python-sdk/blob/main/docs/01-quickstart.md)
explains why the REST base is the **bare host** (`pipes`/`calls`/`numbers` spell
the full `/api/v2/platform/speech/...` prefix inside their own request paths, so
the derived `https://<host>/platform` base would double it):

```bash
export UNPOD_BASE_URL="https://api.unpod.ai"          # one knob for the rest
export UNPOD_SERVICE_BASE_URL="https://api.unpod.ai"  # bare host: pipes/calls/numbers
export UNPOD_PLATFORM_TOKEN="..."                     # org-scoped REST auth
export UNPOD_ORG_HANDLE="your-org"
export UNPOD_API_KEY="sk_..."                         # AgentRunner (Bearer)
```

```python
from unpod import AsyncClient, AgentRunner, CallContext

client = AsyncClient()  # picks up the env above; token auth wins over UNPOD_API_KEY

# Management: pick a voice, bind a Speech Pipe to your agent
profiles = await client.voice_profiles.list(language="en")
pipe = await client.pipes.create(
    name="support-line",
    voice_profile=profiles[0].id,   # a catalog name works too
    agent_id="my-voice-agent",
)


# Connectivity: handle every live call with your own logic
async def entrypoint(ctx: CallContext) -> None:
    await ctx.session.say("Hi! How can I help you today?")
    await ctx.session.run()


AgentRunner(entrypoint=entrypoint, agent_id="my-voice-agent").start()
```

`voice_profiles` and `client.telephony.*` read the org-scoped platform plane, so
they need `UNPOD_PLATFORM_TOKEN` + `UNPOD_ORG_HANDLE` — a Bearer `UNPOD_API_KEY`
alone cannot reach them.

## Documentation

| Guide | What it covers |
|-------|----------------|
| [Overview](https://github.com/unpod-ai/unpod-python-sdk/blob/main/docs/00-overview.md) | What Unpod owns vs what you own, the three layers |
| [Quickstart](https://github.com/unpod-ai/unpod-python-sdk/blob/main/docs/01-quickstart.md) | Install to a dispatched call, transcribed from a live run |
| [Run your agent](https://github.com/unpod-ai/unpod-python-sdk/blob/main/docs/02-run-your-agent.md) | Where the runner process lives (local vs Publish), the identity trio, reconnection and failover, the four `call_end` reasons |
| [Management SDK](https://github.com/unpod-ai/unpod-python-sdk/blob/main/docs/03-management-sdk.md) | REST client API reference |
| [Connectivity SDK](https://github.com/unpod-ai/unpod-python-sdk/blob/main/docs/04-connectivity-sdk.md) | AgentRunner, Session, hooks, controls |
| [Adapters](https://github.com/unpod-ai/unpod-python-sdk/blob/main/docs/05-adapters.md) | The `DialogAdapter` protocol led by the `stream()` hot path, the six bundled adapters, and how to write your own |
| [Deployment](https://github.com/unpod-ai/unpod-python-sdk/blob/main/docs/06-deployment.md) | The three shipped ways an agent reaches traffic — LLM endpoint, voice agent, phone number |
| [Browser quickstart](https://github.com/unpod-ai/unpod-python-sdk/blob/main/docs/07-browser-quickstart.md) | Testing an agent in the browser with no phone number, via `examples/browser_playground/` |
| [Architecture](https://github.com/unpod-ai/unpod-python-sdk/blob/main/docs/05-architecture.md) | Package structure, data flow, protocol details — pending revamp, and shares the `05` ordinal with Adapters until its disposition lands |

Full index, including the archive:
[`docs/README.md`](https://github.com/unpod-ai/unpod-python-sdk/blob/main/docs/README.md).

Full platform documentation: [docs.unpod.ai](https://docs.unpod.ai)

## Development

```bash
git clone https://github.com/unpod-ai/unpod-python-sdk
cd unpod-python-sdk
uv sync --extra dev
uv run pytest
```

## License

[Apache-2.0](LICENSE)
