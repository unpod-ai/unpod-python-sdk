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
