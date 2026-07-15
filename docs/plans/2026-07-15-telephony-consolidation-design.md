# Telephony Consolidation — SDK + Django V2 Design

**Date:** 2026-07-15
**Repos:** `unpod-sdk` (this repo), `unpod/apps/backend-core` (Django)
**Status:** Approved design, not yet implemented

## Problem

A merge of the SDK's telephony surfaces was started and abandoned mid-flight:

1. The SDK carries two overlapping planes: `client.telephony.*` (Django V2
   `/api/v2/platform/telephony/`) and top-level management resources
   (`client.numbers`, `client.trunks`, `client.calls`, `client.pipes`, …)
   that target supervoice directly. Two `NumbersResource` classes, two
   `TrunksResource` classes, two `Number`/`Trunk` models (int vs str ids),
   and a semantic collision: `telephony.numbers.attach(agent_id=…)` binds a
   number to an agent in Django; `numbers.attach(pipe_id=…)` binds it to a
   pipe in supervoice.
2. The 2026-07-11 commits (`f4da18a` + four `['SDK changes']`) hardcoded
   `/api/v2/platform/speech/v1/…` into 4 of 9 management resources
   (`numbers`, `calls`, `pipes`, `voice_profiles`). httpx appends paths to
   the base URL, so these resolve to doubled, broken URLs in both direct and
   proxy mode. The other 5 resources still use bare `/v1/…`, and
   `tests/test_management.py` still asserts the bare form — the suite is
   stale against the source.
3. Django V2 telephony writes reach supervoice only through an outbox row
   drained by a 1-minute cron, and both sync flags (`SPEECH_SYNC_ENABLED`,
   `SUPERSBC_SYNC_ENABLED`) default off. In a default deployment, nothing
   reaches supervoice.

## Decisions

| Decision | Choice |
| --- | --- |
| SDK shape | One plane via Django; no direct supervoice access from the SDK |
| Django push model | Inline push on commit from V2 endpoints + existing outbox/cron backstop |
| Break policy | Break the SDK now (pre-1.0); no deprecation aliases |
| Django V1 | Untouched — changes land only in V2 and the speech app |

## Target SDK surface

```
client.telephony.*        → Django /api/v2/platform/telephony/   (Django-owned lifecycle)
    .numbers              pool list, attach-to-agent
    .trunks               carrier trunk CRUD, attach/detach-numbers
    .overview()

client.speech.*           → Django /api/v2/platform/speech/v1/   (proxied supervoice)
    .numbers              supervoice inventory: list, sync, attach-to-pipe, detach, release
    .pipes                CRUD
    .calls                list / get / create / hangup
    .voice_profiles, .sessions, .recordings, .transcripts
```

**Removed outright:**

- Top-level `client.numbers`, `client.trunks`, `client.calls`,
  `client.pipes`, `client.voice_profiles`, `client.recordings`,
  `client.transcripts` — the supervoice resources move under
  `client.speech`.
- `client.trunks` dies entirely: the speech proxy deliberately excludes
  trunks (Django owns the trunk lifecycle via `client.telephony.trunks`).
- `client.api_keys` — internal-only, unproxied.
- `BearerAuth`-to-supervoice for management resources, the
  `UNPOD_SERVICE_BASE_URL` supervoice-direct escape hatch, and the third
  HTTP client.

**Unchanged:**

- `client.speech.sessions.end/transfer/merge` keeps its orchestrator HTTP
  client (runtime plane, not management).
- `AgentRunner` / the WSS worker plane.

The two "numbers" resources both survive because they are different
records: `telephony.numbers` is the Django pool (int ids, agent binding);
`speech.numbers` is supervoice's synced inventory (str ids, pipe binding).
The namespace now names which one the caller holds.

## Routing mechanics

- All `client.speech.*` resources revert to bare `/v1/<resource>` paths —
  path-transparency is the proxy contract; the base URL decides where a
  request lands.
- One Django base (`<host>/api/v2/platform`) serves both namespaces:
  `client.telephony.*` appends `/telephony/…`, `client.speech.*` appends
  `/speech/v1/…`. Two HTTP clients total: platform + orchestrator.
- Auth is Django-first everywhere: `TokenAuth`/`JWTAuth` + `Org-Handle`.
  The SDK never talks to supervoice's management plane directly.

## Django-side inline push (V2 + speech app only)

1. Add `provision_inline(org, vbn)` to `unpod/telephony/provisioning.py` —
   an additive wrapper around the existing `provision()` with the same
   best-effort, isolated semantics.
2. V2 write endpoints (`numbers/attach`, `connect-provider`,
   `disconnect-provider`, `trunks/{id}/attach-numbers`, `detach-numbers`)
   register it via `transaction.on_commit(…)` after
   `attach_number`/`detach_number` returns. A failed push logs and moves
   on; the API response never blocks on a supervoice outage.
3. The signal still writes the outbox row, so the cron drain and reconcile
   jobs remain the retry backstop. The drain's fingerprint-based upserts
   make double-fire (inline + cron) safe.
4. `SPEECH_SYNC_ENABLED` / `SUPERSBC_SYNC_ENABLED` stay the single kill
   switch; the inline path respects both.
5. V1 writes keep today's behavior exactly: outbox + cron only.

## Redundancy consolidation

**Django V2** (`apiV2Platform/views_telephony.py`), no route or contract
changes:

1. The 12-field provider-config transform, copy-pasted 4× in
   `ProviderConfigurationsViewSet`, becomes one
   `_shape_provider_config(payload)` helper.
2. The near-identical attach loops in `numbers/attach` and
   `trunks/{id}/attach-numbers` (bridge resolution, available-number
   filtering, partial-success loop) extract into one shared
   `_attach_numbers_flow(org, numbers, kind, …)`; thin wrappers keep each
   endpoint's request/response shape.
3. V2-over-V1 delegation **stays**. It is the single-implementation
   pattern; reimplementing it in V2 would duplicate logic and risk drift
   with live V1 clients. It dies when V1 retires (task 6.3 era).

**Not consolidated:** the three "trunk" meanings on the Django side. V1
`BridgeProviderConfig` trunks are frozen for live clients; renaming models
is out of scope. Glossary:

| Term | Meaning |
| --- | --- |
| V1 `telephony/trunks/` | `BridgeProviderConfig` — internal SBC/LiveKit projection, frozen |
| V2 `telephony/trunks/` | `ProviderCredential(type=sip)` — carrier credential |
| supervoice trunks | never proxied; not an external surface |

## Testing and rollout

Django lands first; the SDK's new default route needs the proxy contract
verified current.

1. **Django:** land the V2 dedup helpers (existing V2 tests must pass
   unchanged — that proves behavior preservation), then `provision_inline`
   + `on_commit` wiring. New tests: inline push fires on V2 attach/detach
   commit, respects both flags, and a supervoice failure never fails the
   API response. V1 suites (`test_phase*_*.py`) pass untouched.
2. **Flags:** per the speech README playbook —
   `reconcile_supervoice --dry-run` → verify parity → flip
   `SPEECH_SYNC_ENABLED` per env.
3. **SDK:** restructure to `client.telephony` + `client.speech`, revert the
   four hardcoded paths, delete `client.api_keys` / direct mode / the third
   HTTP client. `test_management.py` → `test_speech.py`: bare-path
   assertions become valid again, plus full-URL composition tests
   (resolved base × path) for both namespaces — a permanent tripwire for
   the bug class that caused the half-merge. Version bump with
   breaking-change notes.

## Success criteria

- One SDK code path per resource; no duplicate resource classes.
- A default-configured SDK reaches supervoice data only via Django.
- A V2 attach is visible in supervoice within the request lifecycle, not
  up to a minute later.
- Zero diffs to Django V1 APIs or behavior.
