"""Tests for the playground harness — event bus, control registry, catalog."""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest
from playground.agents.catalog import CATALOG, get_agent
from playground.harness.control import ControlError, SessionRegistry
from playground.harness.events import EventBus
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
