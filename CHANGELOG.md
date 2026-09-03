# Changelog

All notable changes to the `unpod` Python SDK are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and
this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
While the SDK is pre-1.0, breaking changes ship in a **minor** bump.

## [Unreleased]

### Added

- **Noise cancellation is settable per agent.** `noise_cancellation` on
  `client.agents.voice.create`, `client.agents.update`,
  `client.agent.voice.create` and `client.pipes.create`, plus a
  `NoiseCancellation` enum (`rnnoise`, `hush`, `aic`, `krisp`, `bvc`,
  `bvc_telephony`, `none`) exported from `unpod`. `AgentVoice`, `Agent` and
  `Pipe` declare the field, so a backend set through the API reads back.
- It filters the CALLER's audio before STT — what the agent hears, never what
  it says. `hush`/`rnnoise`/`aic`/`krisp` run inside the media pipeline on any
  transport; `bvc`/`bvc-telephony` are LiveKit Cloud's own canceller at the
  room layer.
- **Omitting the field is not `"none"`.** Unset leaves the deployment's
  `SUPERVOICE_NOISE_BACKEND` in charge — what every existing agent runs on —
  while `"none"` is that agent asking for raw audio. There is no way back to
  unset once a value is stored.

### Requires

- A supervoice deployment carrying `sv_agents.noise_cancellation`. Older ones
  reject the key on `/v1/agent/voice` (that model forbids extra keys) and
  ignore it silently on `/v1/pipes`.
- The chosen backend's library/model present on the worker. A missing one
  degrades to no filtering rather than failing the call, so a stored value is
  not by itself proof the model loaded.

## [0.3.1] - 2026-09-02

### Added

- **Background sound is settable from the SDK.** `background_sound`,
  `background_sound_enabled` and `background_sound_volume` on
  `client.agents.voice.create`, `client.agents.update`,
  `client.agent.voice.create` and `client.pipes.create`, plus a
  `BackgroundSound` enum (`office`, `city`, `forest`, `crowded_room`, `none`)
  exported from `unpod`. The three response models (`AgentVoice`, `Agent`,
  `Pipe`) declare the fields, so a bed set through the API reads back.
- `background_sound_volume` is a **gain** in `0.0`-`1.0`, not a percentage, and
  is range-checked at the call site (`ValueError`) rather than on the round
  trip. Omitting it means the platform's default level, not silence; `0.0` is a
  silent bed and `background_sound_enabled=False` is the off switch, which
  keeps the chosen room.

### Requires

- A supervoice deployment carrying the ambience-volume field. Older
  deployments answer 422 on `/v1/agent/voice` for these keys and ignore them
  silently on `/v1/pipes` PATCH.

## [0.3.0] - 2026-08-31

Two things ship here: **domain dictionaries** — the words an agent hears and
says, authored once per domain and reused by every agent tagged with it — and a
round of route fixes that make the management resources actually reach the
platform. Several routes were pointing at paths that are not API paths on the
deployment, and `numbers.attach` was posting to a route that no longer exists.

### Breaking

- **`numbers.attach()` signature changed.** Was
  `attach(number_id, pipe_id, agent_id=None)`; is now
  `attach(number_id, agent_id, number=None)`.

  The binding is the **agent**, not the pipe: supervoice stopped storing a pipe
  pin and resolves the pipe from the agent at call time. The call now posts to
  the speech numbers endpoint
  (`/api/v2/platform/speech/v1/numbers/{id}/attach`) instead of the telephony
  route `/api/v2/platform/telephony/numbers/{id}/attach-numbers/`, which does
  not exist. The speech endpoint delegates to telephony's attach orchestration —
  that is the path that creates the `VoiceBridgeNumber` and the LiveKit trunks
  and pushes the number plus both trunk ids back to supervoice. Posting straight
  to supervoice bound the agent in mongo and left the number with nothing to
  route through.

  The new optional `number` argument is the E.164; pass it when `number_id` is a
  supervoice id so telephony can resolve the row it owns.

  ```python
  # before
  await client.numbers.attach(number_id, pipe.pipe_id)
  # after
  await client.numbers.attach(number_id, pipe.agent_id)
  await client.numbers.attach(number_id, agent_id, number="+919800000001")  # supervoice id
  ```

- **`Call.transcript` now defaults to `None`, not `[]`.** `None` means the turns
  were **not loaded**; `[]` means the call genuinely has no turns.
  `calls.list()` projects the turns out to keep a page small, and the old `[]`
  default made "not loaded" indistinguishable from "silent call". Code doing
  `if not call.transcript:` still works; code doing `len(call.transcript)` on a
  list row must now handle `None`.

### Added

#### Domain dictionaries — `client.domain_dictionaries`

New resource (`unpod.management.domain_dictionaries.DomainDictionariesResource`,
speech route `/api/v2/platform/speech/v1/domain-dictionaries`) plus the models
`DomainDictionary`, `DomainListItem` and `KVItem`, all exported from
`unpod.models`. Present on both `AsyncClient` and the sync `Client`.

A dictionary is per-domain, tenant-scoped and reusable, with three sections:

| section | plane | `key` | `value` |
|---|---|---|---|
| `vocabulary` | STT | the term | an optional misheard variant |
| `pronunciation` | TTS | the term | its respelling |
| `fillers` | runtime | a language code | its phrases, one per line |

Methods: `list()`, `get(domain)`, `upsert(domain, vocabulary=…,
pronunciation=…, fillers=…, settings=…)`, `attach(domain, agent_id)`,
`detach(domain, agent_id)`, `clone(source_domain, new_domain)`,
`delete(domain)`. Sections accept a mapping (`{"IRDAI": "irda i"}`), a list of
`{"key":…, "value":…}` rows, or a list of `KVItem` — the dict is the ergonomic
spelling, the list is what a fetched dictionary round-trips as.

**Both steps are required — authoring a dictionary alone changes nothing:**

```python
await client.domain_dictionaries.upsert(
    "gamestop", vocabulary={"PowerUp Rewards": "power up rewords"}
)
await client.agents.voice.create("support", brain=Prompt(...), domain="gamestop")
```

The `domain` tag **on the agent** is what the runtime resolves; nothing looks a
dictionary up by name at call time. `attach()`/`detach()` write only the
dictionary's reverse index — to change which dictionary an agent actually
speaks with, pass `domain=` to `agents.voice.create()` or `agents.update()`.

Behaviour worth knowing before you call it:

- Reads are **merged**: a bundled seed (`banking`, `real_estate`, `hospital`)
  unioned with your rows, tenant winning per key. `seed_vocabulary` /
  `seed_pronunciation` expose the bundled half read-only so you can tell what
  you inherited from what you typed.
- `upsert()` **replaces** this tenant's layer — it is not a merge. To add one
  term, `get()` first and send back the edited list. The seed is untouched.
  Omitting `settings` leaves the stored filler knobs alone.
- `attach()` is **exclusive**: an agent has exactly one dictionary, so it is
  pulled out of every other domain.
- `clone()` returns **409** when the target already holds rows, rather than
  silently overwriting them.
- `delete()` on a **seeded** domain resets it (seed stays, tagged agents keep
  working); on a **custom** domain it ceases to exist and the agents and voice
  profiles pointing at it are untagged.
- `DomainDictionary.keyterms` mirrors the server's `to_keyterms`: a row's key
  and its variant both boost recognition, deduped, order preserved.
- Domain names are free text stored verbatim, so each is percent-encoded as
  exactly one path segment — a value containing `/` or `..` cannot re-point the
  request at a different route while carrying your credentials.

#### `domain=` on the agent-creating surfaces

- `agents.voice.create(..., domain=...)` and `AgentVoice.domain`
- `agents.update(..., domain=...)` — retags every voice; pass `""` to detach,
  omit to leave the current tag alone
- `agent.voice.create(..., domain=...)` — also stamped on the playbook doc when
  the brain is a playbook
- `pipes.create(..., domain=...)` (deprecated route, writes a complete agent
  row, so it tags identically), plus `Pipe.domain`, `PipeCreate.domain` and
  `PipeUpdate.domain` (`None` leaves the tag alone, `""` detaches)

#### Other

- `Call.transcript_turns: int` — turn count, present on list rows as well as
  detail reads, so a list row can say how much there is to fetch before you
  spend a `calls.get(call_id)`.
- `AgentVoice.voice_profile_name: str | None` — the voice profile's display
  name, joined server-side. Note `AgentVoice.name` is the **agent's** name; the
  two are unrelated and were easy to confuse when only the opaque
  `voice_profile_id` came back.

### Fixed

- **Speech routes now use the canonical external mount.** `sessions`,
  `transcripts` and `recordings` used the short `/v1/...` form, which is not an
  API path on the deployment — the edge 308s it to the Next.js app, so the SDK
  received a redirect or HTML where it expected JSON. All of these now use
  `/api/v2/platform/speech/v1/...` (`calls` already did, which is why it worked
  while its siblings did not):
  - `sessions.list()`, `sessions.get()`, `sessions.create_token()`
  - `transcripts.list()`, `transcripts.get()`
  - `recordings.list()`

  Session **lifecycle** calls (`end`, `transfer`, `merge`) are unchanged — they
  target the orchestrator service on a different base url and keep the bare
  `/v1/...` form.

### Docs

- `docs/03-management-sdk.md` documents the domain-dictionary surface end to
  end, including the two-step authoring/tagging flow.
- `examples/browser_agent.py` attaches numbers by `agent_id` and explains why
  the binding is the agent.
- Docstrings on `calls.list` / `calls.get` spell out the `None` vs `[]`
  transcript distinction and point at `transcript_turns`.

### Known issues

- 13 tests in `tests/` still assert the pre-migration routes and pre-`.env`
  auth behaviour and fail locally (385 passed / 13 failed; v0.2.1 was 358
  passed / 19 failed). No regression is introduced by this release; the
  remaining stale assertions are tracked for a follow-up.
- `ruff format --check` reports 28 pre-existing unformatted files, unrelated to
  this release. `ruff check` passes.

## [0.2.1]

See the git history for releases prior to this changelog.
