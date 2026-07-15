# Telephony Consolidation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** One telephony plane via Django — SDK restructured to `client.telephony` + `client.speech`, Django V2 pushes to supervoice inline with a new `pipeline` connect type, pipe verification, and dispatch rules targeting the env pipe agent.

**Architecture:** Three repos, strict order: supervoice lands the `connect_type` field first, Django backend-core lands the attach-model + inline push second, the SDK breaking restructure lands last. Design contract: `docs/plans/2026-07-15-telephony-consolidation-design.md` (read it first).

**Tech Stack:** Python 3.12, httpx, pydantic (SDK) · Django/DRF, Mongo-backed supervoice client (backend-core) · FastAPI + Mongo (supervoice).

**Repos and working directories:**

| Phase | Repo | Working dir |
| --- | --- | --- |
| 1 | supervoice | `/Users/parvbhullar/Drives/Vault/Projects/Unpod/super/supervoice` |
| 2 | backend-core | `/Users/parvbhullar/Drives/Vault/Projects/Unpod/unpod/apps/backend-core` |
| 3 | unpod-sdk | `/Users/parvbhullar/Drives/Vault/Projects/Unpod/super/unpod-sdk` |

Each phase is a separate feature branch + PR in its repo. Never commit to `main`.

**Key discovery the plan relies on:** LiveKit dispatch rules are created ONLY in backend-core (`telephony/utils.py`), and the rule's `agent_name` is already a fixed global worker (`get_global_configs("worker-agent")`) — the customer agent rides in `agent_entry.metadata.agent_id`. Supervoice never touches dispatch rules. So `kind=pipeline` = the same rule machinery with a different worker name (`prod-/qa-supervoice-pipe-agent-v1`) and `connect_type` added to the metadata.

---

## Phase 1 — supervoice: `connect_type` on sv_numbers

Branch: `feat/sv-numbers-connect-type` in the supervoice repo.

### Task 1.1: Add `connect_type` to the Number model

**Files:**
- Modify: `src/supervoice/platform/models/number.py` (Number model ~line 43, `NumberUpdateRequest` ~line 66)
- Test: supervoice's existing numbers router tests (find with `grep -r "update_number\|NumberUpdateRequest" tests/`)

**Step 1: Write the failing test** — PATCH accepts `connect_type`:

```python
async def test_patch_number_connect_type(client, seeded_number):
    resp = await client.patch(
        f"/platform/v1/numbers/{seeded_number['number_id']}",
        json={"agent_id": "agent-1", "connect_type": "pipeline"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["agent_id"] == "agent-1"
    assert body["connect_type"] == "pipeline"
```

Follow the shape of the existing `update_number` tests in that file exactly (fixtures, auth headers).

**Step 2: Run it, verify it fails** (422 or missing field).

**Step 3: Implement.** In `models/number.py`:

```python
class Number(BaseModel):
    ...
    agent_id: str | None = None
    # How the number is served: "agent" (LiveKit agent worker) or
    # "pipeline" (pipe agent joins via PipeCat). None = legacy/agent.
    connect_type: str | None = None
```

```python
class NumberUpdateRequest(BaseModel):
    agent_id: str | None
    connect_type: str | None = None  # optional; absent = leave unchanged
```

In `routers/numbers.py` `update_number` (~line 145): include `connect_type` in the atomic update only when the field was provided (`body.model_fields_set`), mirroring how `agent_id` flows into the `find_one_and_update` pipeline.

**Step 4: Run tests, verify pass.**

**Step 5: Commit** — `feat(numbers): accept connect_type on PATCH /numbers/{id}`.

### Task 1.2: Pipe agent worker (separate deliverable — verify, don't build here)

The `prod-supervoice-pipe-agent-v1` / `qa-supervoice-pipe-agent-v1` worker (a LiveKit agent that reads `agent_metadata.agent_id`, resolves agent → pipe like `InboundCallResolver.resolve` in `src/supervoice/composition.py:212`, and joins via PipeCat transport) is a supervoice runtime deliverable owned by the team (Vaibhav's track per the discussion). This plan only requires that **the worker name is registered and running in each env** before Phase 4 rollout. Add a checklist item; do not build it in this plan.

---

## Phase 2 — backend-core: dedup, inline push, attach model

Branch: `feat/telephony-v2-inline-push-pipeline` in the unpod repo. All changes live in `unpod/apiV2Platform/`, `unpod/telephony/` (additive only), and `unpod/speech/` — **zero diffs to V1 route behavior** (`unpod/telephony/views.py`, `views_config.py`, `unpod/apiV1/`).

Run Django tests from `apps/backend-core` (check the repo's runner first: `ls scripts/ Makefile` — else plain `pytest`).

### Task 2.1: Dedup — `_shape_provider_config`

**Files:**
- Modify: `unpod/apiV2Platform/views_telephony.py` (the 12-field transform is copy-pasted 4× inside `ProviderConfigurationsViewSet`, starting ~line 560)
- Test: existing V2 provider-configuration tests (find: `grep -rl "provider-configurations" unpod/apiV2Platform/tests/ tests/`)

**Step 1: Run the existing V2 provider-config tests first** and record they pass — they are the behavior-preservation contract. No new tests.

**Step 2: Extract.** Add one module-level helper directly above the viewset:

```python
def _shape_provider_config(payload: dict) -> dict:
    """Reshape/mask a V1 provider-credential payload for the V2 contract."""
    # <move the copy-pasted 12-field block here verbatim — field order matters>
```

Replace all four copies with calls. Nothing else changes.

**Step 3: Re-run the same tests, verify pass unchanged.**

**Step 4: Commit** — `refactor(apiV2): extract _shape_provider_config, dedupe 4x transform`.

### Task 2.2: Dedup — `_attach_numbers_flow`

**Files:**
- Modify: `unpod/apiV2Platform/views_telephony.py` — `TelephonyNumbersViewSet.attach` (~lines 294–476) and `TelephonyTrunksViewSet.attach_numbers` (~lines 1982–2163)

Same recipe as 2.1: run existing tests for both endpoints first, extract the shared bridge-resolution / available-number-filter / partial-success loop into one module-level `_attach_numbers_flow(org, numbers, termination_kind, ...)`, keep each endpoint's request parsing and response shape in the endpoint, re-run tests unchanged, commit — `refactor(apiV2): extract shared _attach_numbers_flow`.

**Warning:** the two loops have small deliberate differences (agent-kind vs sip-kind termination, `origin_endpoint` in the trunk response). Parameterize; do not "fix" differences — tests unchanged is the acceptance bar.

### Task 2.3: `provision_inline` + `on_commit` wiring

**Files:**
- Modify: `unpod/telephony/provisioning.py` (additive function only)
- Modify: `unpod/apiV2Platform/views_telephony.py` (the five V2 write endpoints)
- Test: create `unpod/apiV2Platform/tests/test_inline_provision.py` (or the repo's existing V2 test dir)

**Step 1: Write failing tests:**

```python
from unittest.mock import patch

# 1. attach → provision fires after commit
@patch("unpod.telephony.provisioning.provision")
def test_v2_attach_fires_inline_provision(mock_provision, api_client, org, agent):
    resp = api_client.post("/api/v2/platform/telephony/numbers/attach/", {...})
    assert resp.status_code == 200
    mock_provision.assert_called_once()          # with (org, vbn)

# 2. a provision exception never fails the response
@patch("unpod.telephony.provisioning.provision", side_effect=RuntimeError("sv down"))
def test_v2_attach_survives_provision_failure(mock_provision, api_client, org, agent):
    resp = api_client.post("/api/v2/platform/telephony/numbers/attach/", {...})
    assert resp.status_code == 200
```

Use the repo's existing V2 attach test fixtures (copy setup from the current `numbers/attach` tests). With Django's `TestCase`, use `captureOnCommitCallbacks(execute=True)` to run `on_commit` hooks.

**Step 2: Run, verify fail.**

**Step 3: Implement.** In `provisioning.py` (append):

```python
def provision_inline(org, vbn) -> None:
    """Immediate best-effort projection after a V2 write commits.

    Same semantics as :func:`provision` (flag-gated, isolated, never raises).
    The outbox row written by the VBN signal remains the retry backstop, and
    the plane syncs are idempotent, so double-fire (inline + cron) is safe.
    """
    try:
        provision(org, vbn)
    except Exception:  # noqa: BLE001 — inline push must never break a request
        logger.warning(
            "provision_inline_failed", extra={"org_id": getattr(org, "id", None)}
        )
```

In each of the five V2 write endpoints (after a successful `attach_number`/`detach_number`, once per affected VBN):

```python
from django.db import transaction
from unpod.telephony import provisioning

transaction.on_commit(lambda o=org, v=vbn: provisioning.provision_inline(o, v))
```

For detach flows call `deprovision` analogously (mirror `handle_vbn_saved`'s deprovision arguments: org, vbn, captured number string).

**Step 4: Run tests, verify pass. Also run the V1 suites** (`pytest unpod/telephony/tests/`) — must pass untouched.

**Step 5: Commit** — `feat(apiV2): inline provision push on commit for telephony writes`.

### Task 2.4: `pipeline` connect type in the attach service

**Files:**
- Modify: `unpod/telephony/services/attach.py` (kinds at lines 55–57, `Termination` at 100, validation at 142, branch at 259–275, `_attach_agent` at 415)
- Modify: `unpod/telephony/models.py` — add `VoiceBridgeNumber.connect_type` + migration
- Modify: `unpod/telephony/utils.py` — worker override (line 32 global, `create_livekit_trunks` :104, `update_dispatch_rule_agent_handle` :566)
- Modify: settings — add `SUPERVOICE_PIPE_AGENT_NAME` next to wherever `SPEECH_SYNC_ENABLED` is declared (grep `config/settings*`), env values `prod-supervoice-pipe-agent-v1` / `qa-supervoice-pipe-agent-v1`
- Test: `unpod/telephony/tests/test_pipeline_attach.py`

**Step 1: Failing tests:**

```python
def test_pipeline_kind_accepted():
    t = Termination(kind="pipeline", agent_id="agent-1")
    result = attach_number(bridge=bridge, number=num, termination=t, product_id=pid)
    vbn = result.vbn
    assert vbn.agent_id == "agent-1"
    assert vbn.connect_type == "pipeline"

@patch("unpod.telephony.providers.livekit.LiveKitClient.set_dispatch_rule")
def test_pipeline_dispatch_rule_targets_pipe_agent(mock_rule, settings, ...):
    settings.SUPERVOICE_PIPE_AGENT_NAME = "qa-supervoice-pipe-agent-v1"
    attach_number(..., termination=Termination(kind="pipeline", agent_id="agent-1"))
    _, worker = mock_rule.call_args.args
    assert worker["name"] == "qa-supervoice-pipe-agent-v1"

def test_agent_kind_unchanged(...):
    # kind=agent still uses get_global_configs("worker-agent") — regression guard
```

**Step 2: Run, verify fail** (`AttachError: unsupported kind`).

**Step 3: Implement:**

1. `attach.py`: `PIPELINE = "pipeline"`; `SUPPORTED_KINDS = {AGENT, SIP, PIPELINE}`.
2. Branch (~line 259): `PIPELINE` routes through `_attach_agent` — same trunk/credential path — with the pipe-agent worker. Give `_attach_agent` a `worker_override: dict | None = None` parameter; the pipeline branch passes `{"name": settings.SUPERVOICE_PIPE_AGENT_NAME}`.
3. Thread `worker_override` through `create_livekit_trunks` / `update_dispatch_rule_agent_handle` → `livekit.set_dispatch_rule(conf, worker_override or worker)`. Default `None` keeps every existing call site byte-identical (V1 safety).
4. Add `"connect_type": "pipeline"` into the `agent_metadata` dict built in `update_dispatch_rule_agent_handle` (~line 688) only when the override is active.
5. `models.py`: `connect_type = models.CharField(max_length=16, default="agent")` on `VoiceBridgeNumber`; `attach_number` sets it from `termination.kind` for AGENT/PIPELINE. `python manage.py makemigrations telephony`.

**Step 4: Run new tests + full `pytest unpod/telephony/tests/` (V1 phases green).**

**Step 5: Commit** — `feat(telephony): pipeline connect type — dispatch rule targets env pipe agent`.

### Task 2.5: Verify-pipe step

**Files:**
- Create: `unpod/speech/pipes.py`
- Modify: `unpod/telephony/services/attach.py` (pipeline branch), `unpod/apiV2Platform/views_telephony.py` (agent-kind warn)
- Test: `unpod/speech/tests/test_pipe_verify.py` + cases in `test_pipeline_attach.py`

**Step 1: Failing tests:**

```python
def test_pipe_exists_for_agent_true(mock_supervoice): ...
def test_pipe_exists_none_on_supervoice_error(mock_supervoice): ...  # returns None, never raises

def test_pipeline_attach_400_when_no_pipe(...):
    with pytest.raises(AttachError, match="no pipe found for agent_id"):
        attach_number(..., termination=Termination(kind="pipeline", agent_id="ghost"))

def test_agent_attach_warns_but_succeeds_when_no_pipe(...):
    resp = api_client.post("/api/v2/platform/telephony/numbers/attach/",
                           {..., "connect_type": "agent"})
    assert resp.status_code == 200
    assert "no pipe" in str(resp.json()["data"].get("warnings", ""))
```

**Step 2: Run, verify fail.**

**Step 3: Implement.** `unpod/speech/pipes.py`:

```python
"""Read-only pipe lookups against supervoice (verification, not lifecycle)."""
from __future__ import annotations
import logging
from .client import SupervoiceClient
from .credentials import get_or_provision_key

logger = logging.getLogger("unpod.speech")

def pipe_exists_for_agent(org, agent_id: str) -> bool | None:
    """True/False if supervoice answered; None when the check itself failed.

    Callers decide policy: pipeline attach hard-fails on False, treats None
    as unverifiable-but-allowed (supervoice outage must not brick attach);
    agent attach only warns.
    """
    if not agent_id:
        return False
    try:
        client = SupervoiceClient()
        key = get_or_provision_key(org, client=client)
        result = client.forward("GET", "/platform/v1/pipes", key=key)
        if result.status_code != 200 or not isinstance(result.data, list):
            return None
        return any(
            isinstance(p, dict) and p.get("agent_id") == agent_id
            for p in result.data
        )
    except Exception:  # noqa: BLE001 — verification must never crash attach
        logger.warning("pipe_verify_failed", extra={"org_id": getattr(org, "id", None)})
        return None
```

In `attach_number`, inside the `kind == PIPELINE` branch **before any write** (V1 never sends pipeline, so V1 is untouched):

```python
from unpod.speech.pipes import pipe_exists_for_agent
exists = pipe_exists_for_agent(org, termination.agent_id)
if exists is False:
    raise AttachError(
        f"no pipe found for agent_id {termination.agent_id!r}; "
        "publish the pipeline before attaching a number"
    )
```

In the V2 `_attach_numbers_flow` (from Task 2.2), when kind is AGENT: call the same helper, and on `False` append a warning string to the response payload (`warnings` key) and stamp `set_sync_state(vbn, plane="speech", status=DRIFT, error="no pipe for agent_id")`. Never block.

**Step 4: Run tests + V1 suites.**

**Step 5: Commit** — `feat(telephony): verify pipe via agent_id — hard-fail pipeline, warn agent`.

### Task 2.6: Widen the sv_numbers sync payload

**Files:**
- Modify: `unpod/speech/mapping.py` (`number_agent_payload`), `unpod/speech/sync.py:129` (`_patch_agent` call site)
- Test: extend the existing speech sync tests (find: `grep -rl "push_number_agent" --include=test_*.py`)

**Step 1: Failing test** — `push_number_agent` on a `connect_type="pipeline"` VBN PATCHes `{"agent_id": ..., "connect_type": "pipeline"}`; an `"agent"` VBN sends `{"agent_id": ...}` only (legacy payload byte-identical).

**Step 2: Run, verify fail.**

**Step 3: Implement:**

```python
# mapping.py
def number_agent_payload(agent_id, connect_type=None):
    payload = {"agent_id": agent_id}
    if connect_type and connect_type != "agent":
        payload["connect_type"] = connect_type
    return payload
```

`sync.py`: `_patch_agent(entry, agent_id, key, client, connect_type=None)`; `push_number_agent` passes `getattr(vbn, "connect_type", None)`. `reconcile_org` also compares/repairs `connect_type` alongside `agent_id` (same diff loop, one extra field).

**Step 4: Run speech tests.**

**Step 5: Commit** — `feat(speech): sync connect_type to sv_numbers alongside agent_id`.

### Task 2.7: Expose `connect_type` on the V2 API

**Files:**
- Modify: `unpod/apiV2Platform/views_telephony.py` — `numbers/attach` and `connect-provider` accept optional `connect_type` (`"agent"` default, `"pipeline"` allowed; anything else → 400) and map it to `Termination.kind`; `overview` includes each number's `connect_type`.
- Test: extend the V2 attach tests: pipeline attach happy path end-to-end (mock supervoice pipes + PATCH), invalid connect_type → 400, overview shows the field.

TDD as above. Commit — `feat(apiV2): connect_type on numbers/attach + overview`.

---

## Phase 3 — unpod-sdk: breaking restructure

Branch: `feat/one-plane-via-django` in this repo. Run tests with `uv run pytest tests/ -v`.

### Task 3.1: Base URLs — kill the supervoice-direct plane

**Files:**
- Modify: `src/unpod/_base_url.py`, `src/unpod/client.py`
- Test: `tests/test_base_url.py`

**Step 1: Failing tests:**

```python
def test_service_base_removed():
    import unpod._base_url as b
    assert not hasattr(b, "service_base")

def test_speech_base(monkeypatch):
    monkeypatch.setenv("UNPOD_BASE_URL", "api.unpod.ai")
    assert speech_base() == "https://api.unpod.ai/api/v2/platform/speech"
```

**Step 2: Run, verify fail.**

**Step 3: Implement.** In `_base_url.py`: delete `service_base()`; add:

```python
def speech_base() -> str | None:
    """Supervoice management plane via the backend-core proxy.

    ``<http_base>/api/v2/platform/speech`` — resource paths stay bare
    ``/v1/<resource>`` (path-transparent to supervoice ``/platform/v1/*``).
    """
    base = platform_base()
    return f"{base}/speech" if base else None
```

Update the module docstring (management REST now routes via the proxy). Fix any `service_base` importers (`grep -rn service_base src/ tests/`).

**Step 4–5: Tests green; commit** — `feat!: route the management plane through the Django proxy only`.

### Task 3.2: `client.speech` namespace + Django-first auth

**Files:**
- Create: `src/unpod/speech/__init__.py`
- Modify: `src/unpod/client.py`, `src/unpod/management/numbers.py` (and the three other rewritten files), delete `src/unpod/management/api_keys.py` and `src/unpod/management/trunks.py`
- Test: rename `tests/test_management.py` → `tests/test_speech.py`

**Step 1: Rewrite the test file first** (it is the contract):

- All resource access via `client.speech.<resource>`.
- Path assertions: bare `/v1/...` (the existing assertions in the old file were right all along — keep them).
- `numbers`: no `attach`/`detach` methods (`assert not hasattr(client.speech.numbers, "attach")`).
- No `client.trunks`, `client.api_keys`, `client.numbers` top-level (`pytest.raises(AttributeError)`).
- Auth: constructing a client with only `api_key` raises `ValueError` (Bearer/direct mode is gone); `UNPOD_PLATFORM_TOKEN` or explicit `TokenAuth`/`JWTAuth` required.
- **Full-URL composition tripwire** (the bug class that caused the half-merge):

```python
def test_speech_full_url_composition(monkeypatch):
    monkeypatch.setenv("UNPOD_BASE_URL", "api.unpod.ai")
    monkeypatch.setenv("UNPOD_PLATFORM_TOKEN", "tok")
    client = AsyncClient()
    http = client.speech.numbers._http
    assert http._base_url == "https://api.unpod.ai/api/v2/platform/speech"
    # httpx appends the request path to the base:
    assert f"{http._base_url}/v1/numbers" == (
        "https://api.unpod.ai/api/v2/platform/speech/v1/numbers"
    )

def test_telephony_full_url_composition(monkeypatch):
    ...  # same for https://api.unpod.ai/api/v2/platform + /telephony/numbers/
```

**Step 2: Run, verify fail.**

**Step 3: Implement:**

1. Revert the four hardcoded files — every `"/api/v2/platform/speech/v1/..."` string in `management/{numbers,calls,pipes,voice_profiles}.py` becomes `"/v1/..."`.
2. Delete `attach`/`detach` from `management/numbers.py` (pipe pinning retired); delete `management/trunks.py` + `management/api_keys.py` and their model imports/exports.
3. New `src/unpod/speech/__init__.py`:

```python
"""client.speech — the supervoice management plane via the Django proxy."""
from unpod.management.calls import CallsResource
from unpod.management.numbers import NumbersResource
from unpod.management.pipes import PipesResource
from unpod.management.recordings import RecordingsResource
from unpod.management.sessions import SessionsResource
from unpod.management.transcripts import TranscriptsResource
from unpod.management.voice_profiles import VoiceProfilesResource


class SpeechNamespace:
    """Proxied supervoice resources; paths are bare ``/v1/...``."""

    def __init__(self, http, orch_http) -> None:
        self.numbers = NumbersResource(http)
        self.pipes = PipesResource(http)
        self.calls = CallsResource(http)
        self.voice_profiles = VoiceProfilesResource(http)
        self.sessions = SessionsResource(http, orch_http=orch_http)
        self.recordings = RecordingsResource(http)
        self.transcripts = TranscriptsResource(http)
```

4. `client.py` — rewrite `AsyncClient.__init__`: drop `api_key`/`base_url` Bearer path (auth = explicit `auth` arg, else `UNPOD_PLATFORM_TOKEN` → `TokenAuth`, else `ValueError`); build `platform_http` (base `platform_base()`, override `UNPOD_PLATFORM_BASE_URL`) and `speech_http` (base `speech_base()`, derived from the same host); keep `orch_http` resolution from `UNPOD_ORCHESTRATOR_BASE_URL`; expose exactly `self.telephony` and `self.speech`; delete the nine top-level resource attributes. Mirror in the blocking `Client` (`_SyncSpeechNamespace` like `_SyncTelephonyNamespace`).

**Step 4: `uv run pytest tests/ -v`** — `test_speech.py`, `test_base_url.py`, `test_telephony.py` all green.

**Step 5: Commit** — `feat!: client.speech namespace; remove direct supervoice access, trunks, api_keys`.

### Task 3.3: `connect_type` on telephony attach

**Files:**
- Modify: `src/unpod/telephony/__init__.py` (`NumbersResource.attach`, ~line 144)
- Test: `tests/test_telephony.py`

**Step 1: Failing test** — `client.telephony.numbers.attach([1], agent_id="a", connect_type="pipeline")` sends `{"connect_type": "pipeline"}` in the POST body; omitting it sends no `connect_type` key (backward-compatible with older backends).

**Step 2–5:** implement (optional keyword `connect_type: str | None = None`, added to the body only when set), tests green, commit — `feat(telephony): connect_type on numbers.attach`.

### Task 3.4: Docs + example sweep

**Files:**
- Modify: `docs/02-management-sdk.md` (rewrite: speech namespace, Django-only routing, no direct mode), `docs/00-overview.md`/`01-architecture.md` (references to `client.numbers` etc.), `examples/test_call.py` (`client.pipes` → `client.speech.pipes`, …)

Grep-driven: `grep -rn "client\.\(numbers\|trunks\|calls\|pipes\|voice_profiles\|recordings\|transcripts\|api_keys\)" docs/ examples/ README*`. Update every hit. Run the example against a dev stack if available; otherwise `uv run python -c "import unpod"` sanity. Commit — `docs: one-plane-via-Django SDK surface`.

### Task 3.5: Version bump + breaking-change notes

Bump the minor/major version in `pyproject.toml`, add a CHANGELOG entry listing every removed symbol and its replacement (table: `client.numbers.attach` → `client.telephony.numbers.attach(connect_type=…)`, `client.trunks` → `client.telephony.trunks`, `client.api_keys` → removed, direct Bearer mode → removed). Commit — `chore: release notes for the one-plane restructure`.

---

## Phase 4 — Rollout (ops checklist, in order)

1. Supervoice deployed with `connect_type` PATCH support (Phase 1) — QA first.
2. Pipe agent worker `qa-supervoice-pipe-agent-v1` registered and answering in QA (Task 1.2 dependency).
3. Backend-core deployed (Phase 2) with `SUPERVOICE_PIPE_AGENT_NAME` set per env; sync flags still off.
4. `python manage.py reconcile_supervoice --dry-run` → verify parity per org.
5. Flip `SPEECH_SYNC_ENABLED=True` in QA; watch `speech_sync_*` / `provision_inline_failed` logs; then prod.
6. Test a pipeline attach end-to-end in QA: attach with `connect_type=pipeline` → dispatch rule's `agent_name` is `qa-supervoice-pipe-agent-v1` (inspect via LiveKit API) → inbound call joins the pipe via PipeCat.
7. Release the SDK (Phase 3) only after 1–6 hold.

## Success criteria (from the design)

- One SDK code path per resource; single attach verb `client.telephony.numbers.attach(agent_id, connect_type)`.
- Default-configured SDK reaches supervoice data only via Django.
- V2 attach visible in supervoice within the request lifecycle.
- Pipeline number's dispatch rule targets `{env}-supervoice-pipe-agent-v1`; pipe agent resolves number → `agent_id` → pipe → PipeCat.
- Zero diffs to Django V1 APIs or behavior (V1 test suites untouched and green).
