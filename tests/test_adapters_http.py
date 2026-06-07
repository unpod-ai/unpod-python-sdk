"""Tests for HTTPAdapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from unpod.adapters.http import HTTPAdapter


@pytest.mark.anyio
async def test_http_adapter_turn():
    adapter = HTTPAdapter(url="https://brain.example.com/turn")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = lambda: {"text": "I can help with that"}
    mock_response.raise_for_status = MagicMock()

    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=mock_response,
    ):
        result = await adapter.turn("hello")
        assert result == "I can help with that"


@pytest.mark.anyio
async def test_http_adapter_turn_with_context():
    adapter = HTTPAdapter(url="https://brain.example.com/turn")
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = lambda: {"text": "ok"}
    mock_response.raise_for_status = MagicMock()

    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_post:
        await adapter.turn("hello", context={"customer_id": "C1"})
        call_args = mock_post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body["context"] == {"customer_id": "C1"}


def test_http_adapter_assist():
    adapter = HTTPAdapter(url="https://brain.example.com/turn")
    adapter.assist("be concise")
    assert adapter._pending_instructions == ["be concise"]


@pytest.mark.anyio
async def test_http_adapter_assist_sent_in_turn():
    adapter = HTTPAdapter(url="https://brain.example.com/turn")
    adapter.assist("be concise")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = lambda: {"text": "ok"}
    mock_response.raise_for_status = MagicMock()

    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        return_value=mock_response,
    ) as mock_post:
        await adapter.turn("hello")
        call_args = mock_post.call_args
        body = call_args.kwargs.get("json") or call_args[1].get("json")
        assert body.get("system_instructions") == ["be concise"]
    # After turn, pending instructions should be cleared
    assert adapter._pending_instructions == []


def test_http_adapter_custom_headers():
    adapter = HTTPAdapter(
        url="https://brain.example.com/turn",
        headers={"Authorization": "Bearer sk_test"},
    )
    assert adapter._headers["Authorization"] == "Bearer sk_test"
