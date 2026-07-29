# unpod-sdk documentation

The spine below is the whole guide set, in reading order: what Unpod runs
versus what you run, a quickstart transcribed from a live run, then reference
pages for the two halves of the SDK (management REST and the connectivity
runtime). Every page states what is shipped today and labels anything that is
not. Superseded pages live in [`archive/`](archive/) and keep a banner naming
their replacement.

## Spine

| Doc | What it covers |
|---|---|
| [00-overview.md](00-overview.md) | What Unpod owns versus what you own, the terminology canon (Speech Worker, Agent Runner, Pipe, Playbook, Publish, transports), the three connection paths, and the package scope |
| [01-quickstart.md](01-quickstart.md) | Install → Pipe → Agent Runner → number → first call, transcribed from a live verified run |
| [02-run-your-agent.md](02-run-your-agent.md) | The runtime narrative: local runner versus Publish, the identity trio (`agent_id` / `worker_id` / pool), what `dev_mode` really changes, reconnection and failover, the four `call_end` reasons |
| [03-management-sdk.md](03-management-sdk.md) | REST reference for both planes, the `client.telephony.numbers.attach` verdict, auth precedence (`UNPOD_PLATFORM_TOKEN` beats `UNPOD_API_KEY`), and the per-plane number-status vocabularies |
| [04-connectivity-sdk.md](04-connectivity-sdk.md) | `AgentRunner` and `Session`: constructor parameters, `CallContext`, the hooks that actually fire versus the four that never do, controls and transfers |
| [05-adapters.md](05-adapters.md) | The `dialog_machine` slot: the `DialogAdapter` protocol led by the `stream()` hot path, the six bundled adapters, and how to write your own |
| [06-deployment.md](06-deployment.md) | The three shipped ways an agent reaches traffic — LLM endpoint, voice agent, phone number — which SDK call performs each, plus a hard-labeled roadmap |
| [07-browser-quickstart.md](07-browser-quickstart.md) | Testing an agent in the browser with no phone number: the `examples/browser_playground/` bring-up, its environment, and its rough edges |

## Outside the spine

| Doc | Status |
|---|---|
| [05-architecture.md](05-architecture.md) | Pending revamp — predates the current canon and shares the `05` ordinal with adapters until its disposition lands. Frame-level bridge and dispatch reference; still documents `serve`-mode frames, omits `telephony/` and the OpenAI/Anthropic adapters |
| [plans/](plans/) | Design and implementation plans, including the record of the spine renumber ([plans/2026-07-29-docs-spine-renumber.md](plans/2026-07-29-docs-spine-renumber.md)) |

## Archive

[`archive/`](archive/) holds docs the spine replaced. They are kept because
external links point at them; each opens with a dated banner naming its
replacement and the claims that did not survive a code check.

| Archived doc | Replaced by |
|---|---|
| [archive/05-quickstart.md](archive/05-quickstart.md) | [01-quickstart.md](01-quickstart.md) |

One exception to that convention: `docs/06-browser-quickstart.md` was **removed
rather than archived** when
[07-browser-quickstart.md](07-browser-quickstart.md) replaced it. It documented
a `run.py` in `auto_rl` — an unrelated sibling project — a `.env.example` at
the wrong path, and the speech app's port as the UI's, so no claim in it was
worth preserving behind a banner. Links to `docs/06-browser-quickstart.md`
404; point them at `docs/07-browser-quickstart.md`.

## Conventions

- Code is cited by symbol (`module.py::Class.method`), never by line number.
- Roadmap material appears only under a Roadmap heading or an explicit status
  callout, never mixed into a description of what ships today.
- Known gaps are named where a reader would trip over them, with the symbol
  that causes them.
