"""Tests for the playground harness — event bus, control registry, catalog."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest
from playground.agents.catalog import CATALOG, get_agent, resolve_agent
from playground.harness.api import build_app
from playground.harness.control import ControlError, SessionRegistry
from playground.harness.events import EventBus
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from unpod import Session

# --- EventBus -----------------------------------------------------------------


def test_event_bus_publish_reaches_subscriber() -> None:
    bus = EventBus()
    with bus.subscribe() as queue:
        bus.publish("user_turn", text="hi")
        message = queue.get_nowait()
    assert message == {"type": "user_turn", "data": {"text": "hi"}}


def test_event_bus_publish_without_subscribers_is_noop() -> None:
    bus = EventBus()
    bus.publish("ready")  # must not raise


def test_event_bus_unsubscribes_on_context_exit() -> None:
    bus = EventBus()
    with bus.subscribe():
        pass
    bus.publish("agent_turn", text="bye")
    assert bus._subscribers == set()


# --- Catalog ------------------------------------------------------------------


def test_catalog_has_faq_bot() -> None:
    spec = get_agent("faq_bot")
    assert spec is not None
    assert spec.agent_id == "playground-faq-bot"
    assert spec.name in CATALOG


def test_get_unknown_agent_returns_none() -> None:
    assert get_agent("nope") is None


def test_resolve_agent_by_name() -> None:
    spec = resolve_agent("faq_bot")
    assert spec is not None
    assert spec.agent_id == "playground-faq-bot"


def test_resolve_agent_by_agent_id() -> None:
    """The web UI sends the flow ``agent_id``, not the catalog name."""
    spec = resolve_agent("playground-faq-bot")
    assert spec is not None
    assert spec.name == "faq_bot"


def test_resolve_agent_unknown_returns_none() -> None:
    assert resolve_agent("nope") is None


# --- SessionRegistry / control ------------------------------------------------


class _FakeAdapter:
    """Satisfies the DialogAdapter protocol + records control calls."""

    def __init__(self) -> None:
        self.llm: str | None = None
        self.switched: Any = None
        self.preserve: bool | None = None

    async def turn(self, text: str, context: dict | None = None) -> str:
        return ""

    async def stream(
        self, text: str, context: dict | None = None
    ) -> AsyncIterator[str]:
        if False:  # pragma: no cover - empty async generator
            yield ""

    def assist(self, text: str) -> None: ...

    def set_llm(self, uri: str) -> None:
        self.llm = uri

    def switch_flow(self, flow: Any, *, preserve_memory: bool = False) -> None:
        self.switched = flow
        self.preserve = preserve_memory


def _session_with_adapter() -> tuple[Session, _FakeAdapter]:
    session = Session(bridge=object())  # type: ignore[arg-type]
    adapter = _FakeAdapter()
    session.dialog_machine = adapter
    return session, adapter


def test_resolve_active_requires_single_session() -> None:
    registry = SessionRegistry()
    with pytest.raises(ControlError):
        registry.resolve("active")
    session, _ = _session_with_adapter()
    registry.register("c1", session)
    assert registry.resolve("active") is session


def test_resolve_unknown_id_raises() -> None:
    registry = SessionRegistry()
    with pytest.raises(ControlError):
        registry.resolve("missing")


def test_apply_set_llm_updates_adapter() -> None:
    registry = SessionRegistry()
    session, adapter = _session_with_adapter()
    registry.register("c1", session)
    registry.apply("c1", "set_llm", {"uri": "openai/gpt-4o-mini"})
    assert adapter.llm == "openai/gpt-4o-mini"


def test_apply_set_llm_requires_uri() -> None:
    registry = SessionRegistry()
    session, _ = _session_with_adapter()
    registry.register("c1", session)
    with pytest.raises(ControlError):
        registry.apply("c1", "set_llm", {})


def test_apply_switch_flow_passes_preserve_memory() -> None:
    registry = SessionRegistry()
    session, adapter = _session_with_adapter()
    registry.register("c1", session)
    registry.apply("c1", "switch_flow", {"flow": "f", "preserve_memory": True})
    assert adapter.switched == "f"
    assert adapter.preserve is True


def test_apply_unknown_action_raises() -> None:
    registry = SessionRegistry()
    session, _ = _session_with_adapter()
    registry.register("c1", session)
    with pytest.raises(ControlError):
        registry.apply("c1", "bogus", {})


def test_unregister_removes_session() -> None:
    registry = SessionRegistry()
    session, _ = _session_with_adapter()
    registry.register("c1", session)
    registry.unregister("c1")
    with pytest.raises(ControlError):
        registry.resolve("c1")


# --- App routing --------------------------------------------------------------


@pytest.fixture
def harness_app(monkeypatch: pytest.MonkeyPatch):
    """Build the harness app with a dummy LLM key.

    The lifespan builds an ``AgentRunner`` (which resolves an LLM at build time)
    and starts a background task that sleeps before dialing supervoice — it is
    cancelled on context exit, so no network is touched during the test.
    """
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return build_app()


def test_events_websocket_streams_published_events(harness_app) -> None:
    with TestClient(harness_app) as client:
        with client.websocket_connect("/playground/events") as ws:
            harness_app.state.bus.publish("ready", agent="faq_bot")
            message = ws.receive_json()
    assert message == {"type": "ready", "data": {"agent": "faq_bot"}}


def test_unmatched_websocket_closes_cleanly(harness_app) -> None:
    """Regression: a websocket on an unrouted path must close cleanly rather
    than fall through to the SPA static mount and crash with ``AssertionError``
    (StaticFiles asserts an HTTP scope)."""
    with TestClient(harness_app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/audio"):
                pass


def test_health_endpoint_ok(harness_app) -> None:
    with TestClient(harness_app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_create_session_resolves_agent_id_and_injects_ws_url(
    harness_app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the UI sends the flow ``agent_id``; the session must resolve
    it (not 'unknown agent') and return a ``ws_url`` so the browser never dials
    ``/undefined``. The upstream supervoice ``/connect`` is stubbed."""

    class _FakeResp:
        def raise_for_status(self) -> None: ...

        def json(self) -> dict[str, str]:
            return {"session_id": "s1"}

    class _FakeClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None: ...

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *args: Any) -> bool:
            return False

        async def post(self, url: str, params: Any = None) -> _FakeResp:
            return _FakeResp()

    monkeypatch.setattr("playground.harness.api.httpx.AsyncClient", _FakeClient)
    with TestClient(harness_app) as client:
        resp = client.post("/playground/sessions?agent=playground-faq-bot")
    body = resp.json()
    assert "error" not in body
    assert body["ws_url"].endswith("/ws/audio")
    assert body["transport"] == "ws"
    assert body["agent"] == "faq_bot"


# --- Flows ---------------------------------------------------------------------


def test_flow_registry_discovers_flows() -> None:
    from playground.agents.flows import flow_registry

    infos = flow_registry()
    assert len(infos) >= 3
    assert "health_wellness_3node" in {f.id for f in infos}
    for f in infos:
        assert f.label and f.nodes > 0 and f.initial_node


def test_flows_endpoint_lists_all(harness_app) -> None:
    with TestClient(harness_app) as client:
        body = client.get("/playground/flows").json()
    ids = {f["id"] for f in body["flows"]}
    assert len(body["flows"]) >= 3
    assert body["active"] in ids
    assert {"id", "label", "nodes", "initial_node", "description"} <= set(
        body["flows"][0]
    )


def test_switch_flow_control_changes_active_flow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A FlowSet DialogMachine can be switched live via the control plane."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from playground.agents.flows import build, flow_registry

    session = Session(bridge=object())  # type: ignore[arg-type]
    session.dialog_machine = build("openai/gpt-4o-mini")
    registry = SessionRegistry()
    registry.register("c1", session)

    target = flow_registry()[1].id
    registry.apply("c1", "switch_flow", {"flow": target, "preserve_memory": True})
    assert isinstance(session.dialog_machine.state, dict)


def test_switch_flow_unknown_id_raises_control_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from playground.agents.flows import build

    session = Session(bridge=object())  # type: ignore[arg-type]
    session.dialog_machine = build("openai/gpt-4o-mini")
    registry = SessionRegistry()
    registry.register("c1", session)

    with pytest.raises(ControlError):
        registry.apply("c1", "switch_flow", {"flow": "does-not-exist"})


def test_switch_flow_on_non_flow_agent_raises_control_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """switch_flow on a plain LLMAgent (no FlowSet) is a clean ControlError,
    not an AttributeError that would escape as an HTTP 500."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from playground.agents import faq_bot

    session = Session(bridge=object())  # type: ignore[arg-type]
    session.dialog_machine = faq_bot.build("openai/gpt-4o-mini")
    registry = SessionRegistry()
    registry.register("c1", session)

    with pytest.raises(ControlError):
        registry.apply("c1", "switch_flow", {"flow": "anything"})
