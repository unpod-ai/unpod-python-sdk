"""Tests for DialogAdapter protocol and SuperDialogAdapter."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from unpod.adapters.base import DialogAdapter


def test_dialog_adapter_is_protocol():
    """DialogAdapter is a runtime-checkable Protocol."""

    class FakeAdapter:
        async def turn(self, text, context=None):
            return "ok"

        async def stream(self, text, context=None):
            yield "ok"

        def assist(self, text):
            pass

    assert isinstance(FakeAdapter(), DialogAdapter)


def test_non_conforming_not_adapter():
    class NotAdapter:
        pass

    assert not isinstance(NotAdapter(), DialogAdapter)


@pytest.mark.anyio
async def test_superdialog_adapter_turn():
    from unpod.adapters.superdialog import SuperDialogAdapter

    mock_dm = MagicMock()
    mock_dm.turn = AsyncMock(return_value=MagicMock(text="response"))
    adapter = SuperDialogAdapter(mock_dm)
    result = await adapter.turn("hello")
    assert result == "response"
    mock_dm.turn.assert_called_once_with("hello")


@pytest.mark.anyio
async def test_superdialog_adapter_assist():
    from unpod.adapters.superdialog import SuperDialogAdapter

    mock_dm = MagicMock()
    adapter = SuperDialogAdapter(mock_dm)
    adapter.assist("be empathetic")
    mock_dm.assist.assert_called_once_with("be empathetic")


def test_superdialog_adapter_set_llm():
    from unpod.adapters.superdialog import SuperDialogAdapter

    mock_dm = MagicMock()
    adapter = SuperDialogAdapter(mock_dm)
    adapter.set_llm("anthropic/claude-haiku-4-5")
    mock_dm.set_llm.assert_called_once_with("anthropic/claude-haiku-4-5")


def test_superdialog_adapter_switch_flow():
    from unpod.adapters.superdialog import SuperDialogAdapter

    mock_dm = MagicMock()
    adapter = SuperDialogAdapter(mock_dm)
    mock_flow = MagicMock()
    adapter.switch_flow(mock_flow, preserve_memory=True)
    mock_dm.switch_flow.assert_called_once_with(mock_flow, preserve_memory=True)


def test_superdialog_adapter_is_complete():
    from unpod.adapters.superdialog import SuperDialogAdapter

    mock_dm = MagicMock()
    mock_dm.is_complete = True
    adapter = SuperDialogAdapter(mock_dm)
    assert adapter.is_complete is True


def test_superdialog_adapter_state():
    from unpod.adapters.superdialog import SuperDialogAdapter

    mock_dm = MagicMock()
    mock_dm.state = {"node_id": "greeting", "slots": {}}
    adapter = SuperDialogAdapter(mock_dm)
    assert adapter.state == {"node_id": "greeting", "slots": {}}


def test_register_llm_callback_delegates_to_dialog_machine():
    """Prefers DialogMachine.register_llm_callback (wires both engines)."""
    from unpod.adapters.superdialog import SuperDialogAdapter

    mock_dm = MagicMock()  # auto-provides register_llm_callback
    adapter = SuperDialogAdapter(mock_dm)

    async def _cb(data):
        pass

    adapter.register_llm_callback(_cb)
    mock_dm.register_llm_callback.assert_called_once_with(_cb)


def test_register_llm_callback_fallback_without_dm_method():
    """Older superdialog without register_llm_callback -> set _adapter directly."""
    from unpod.adapters.superdialog import SuperDialogAdapter

    class _DM:  # no register_llm_callback method
        def __init__(self):
            self._adapter = MagicMock()
            self._adapter._on_llm_complete = None

    dm = _DM()
    adapter = SuperDialogAdapter(dm)

    async def _cb(data):
        pass

    adapter.register_llm_callback(_cb)
    assert dm._adapter._on_llm_complete is _cb


@pytest.mark.anyio
async def test_stream_forwards_language_when_brain_accepts_it():
    """Brain whose turn() accepts language -> stream forwards it."""
    from types import SimpleNamespace

    from unpod.adapters.superdialog import SuperDialogAdapter

    class _LangBrain:
        def __init__(self):
            self.captured: dict = {}

        async def turn(self, text, stream=False, language=None):
            self.captured = {"stream": stream, "language": language}

            async def _gen():
                yield SimpleNamespace(text="hi")

            return _gen()

    brain = _LangBrain()
    adapter = SuperDialogAdapter(brain)
    chunks = [c async for c in adapter.stream("hello", language="hi")]
    assert chunks == ["hi"]
    assert brain.captured == {"stream": True, "language": "hi"}


@pytest.mark.anyio
async def test_stream_omits_language_for_legacy_brain():
    """Legacy brain without language param -> no raise and language not passed."""
    from types import SimpleNamespace

    from unpod.adapters.superdialog import SuperDialogAdapter

    class _LegacyBrain:
        def __init__(self):
            self.captured: dict = {}

        async def turn(self, text, stream=False):
            self.captured = {"stream": stream}

            async def _gen():
                yield SimpleNamespace(text="ok")

            return _gen()

    brain = _LegacyBrain()
    adapter = SuperDialogAdapter(brain)
    chunks = [c async for c in adapter.stream("hello", language="hi")]
    assert chunks == ["ok"]
    assert brain.captured == {"stream": True}


@pytest.mark.anyio
async def test_session_llm_cb_forwards_cached_to_usage():
    """Session._llm_cb passes data.cached → UsageReporter (billable cache tokens)."""
    from types import SimpleNamespace

    from unpod.connectivity.session import Session

    captured: dict = {}

    class _FakeAdapter:
        async def turn(self, text, context=None):
            return "ok"

        async def stream(self, text, context=None):
            yield "ok"

        def assist(self, text):
            pass

        def register_llm_callback(self, fn):
            captured["cb"] = fn

    session = Session(ctx=SimpleNamespace(session_id="s1"))
    session.dialog_machine = _FakeAdapter()
    assert "cb" in captured  # _llm_cb was wired

    await captured["cb"](
        SimpleNamespace(
            tokens_in=6000,
            tokens_out=20,
            cached=5800,
            model="anthropic/claude-haiku-4-5",
        )
    )

    # The reporter accumulated the cached read tokens (not just prompt/completion).
    assert session._usage._cached == 5800
    assert session._usage._prompt == 6000
    assert session._usage._completion == 20
    assert session._usage._counters().get("llm_cached_tokens") == 5800


@pytest.mark.parametrize(
    "name",
    [
        "AnthropicAdapter",
        "HTTPAdapter",
        "LangChainAdapter",
        "MCPAdapter",
        "OpenAIAdapter",
        "SuperDialogAdapter",
    ],
)
def test_adapter_stream_accepts_language(name):
    """Every exported adapter's stream() must accept ``language``.

    session.run() calls ``stream(text, language=...)`` on the hot path, so an
    adapter missing the kwarg raises TypeError on the first user turn.
    """
    import inspect

    import unpod.adapters as adapters

    adapter_cls = getattr(adapters, name)
    params = inspect.signature(adapter_cls.stream).parameters
    assert "language" in params
