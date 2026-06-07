"""Tests for LangChainAdapter."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from unpod.adapters.langchain import LangChainAdapter


@pytest.mark.anyio
async def test_langchain_adapter_turn():
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=MagicMock(content="chain response"))
    adapter = LangChainAdapter(mock_chain)
    result = await adapter.turn("hello")
    assert result == "chain response"


@pytest.mark.anyio
async def test_langchain_adapter_turn_maintains_history():
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=MagicMock(content="response 1"))
    adapter = LangChainAdapter(mock_chain)
    await adapter.turn("hello")

    # History should have user + assistant messages
    assert len(adapter._history) == 2
    assert adapter._history[0]["role"] == "user"
    assert adapter._history[1]["role"] == "assistant"


def test_langchain_adapter_assist():
    mock_chain = MagicMock()
    adapter = LangChainAdapter(mock_chain)
    adapter.assist("be empathetic")
    assert len(adapter._history) == 1
    assert adapter._history[0]["role"] == "system"
    assert adapter._history[0]["content"] == "be empathetic"


@pytest.mark.anyio
async def test_langchain_adapter_stream():
    mock_chain = MagicMock()

    async def fake_astream(input_dict: dict) -> None:
        yield MagicMock(content="chunk1")
        yield MagicMock(content="chunk2")

    mock_chain.astream = fake_astream
    adapter = LangChainAdapter(mock_chain)

    chunks: list[str] = []
    async for chunk in adapter.stream("hello"):
        chunks.append(chunk)
    assert chunks == ["chunk1", "chunk2"]


@pytest.mark.anyio
async def test_langchain_adapter_custom_input_key_turn():
    """Adapter with custom input_key passes that key to the chain."""
    received: list[dict] = []

    async def capture_invoke(input_dict):
        received.append(input_dict)
        return MagicMock(content="ok")

    mock_chain = MagicMock()
    mock_chain.ainvoke = capture_invoke
    adapter = LangChainAdapter(mock_chain, input_key="input")
    await adapter.turn("hello")
    assert "input" in received[0]
    assert "messages" not in received[0]


@pytest.mark.anyio
async def test_langchain_adapter_custom_input_key_stream():
    """Adapter with custom input_key passes that key to astream."""
    received: list[dict] = []

    async def capture_stream(input_dict):
        received.append(input_dict)
        yield MagicMock(content="chunk")

    mock_chain = MagicMock()
    mock_chain.astream = capture_stream
    adapter = LangChainAdapter(mock_chain, input_key="input")
    chunks = [c async for c in adapter.stream("hello")]
    assert chunks == ["chunk"]
    assert "input" in received[0]
    assert "messages" not in received[0]


@pytest.mark.anyio
async def test_langchain_adapter_default_input_key_is_messages():
    """Default input_key='messages' preserves existing behaviour."""
    received: list[dict] = []

    async def capture(input_dict):
        received.append(input_dict)
        return MagicMock(content="ok")

    mock_chain = MagicMock()
    mock_chain.ainvoke = capture
    adapter = LangChainAdapter(mock_chain)
    await adapter.turn("hello")
    assert "messages" in received[0]
    assert "input" not in received[0]
