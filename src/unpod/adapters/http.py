"""HTTPAdapter — POST-based dialog adapter for remote brain endpoints."""

from __future__ import annotations

from typing import AsyncIterator

import httpx


class HTTPAdapter:
    """Dialog adapter that sends turns to an HTTP endpoint."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout_s: float = 10.0,
    ) -> None:
        self._url = url
        self._headers: dict[str, str] = headers or {}
        self._timeout = timeout_s
        self._pending_instructions: list[str] = []
        self._session_id: str | None = None

    async def turn(self, text: str, context: dict | None = None) -> str:
        """Send a user turn and return the assistant reply."""
        body: dict = {"text": text, "context": context or {}}
        if self._pending_instructions:
            body["system_instructions"] = self._pending_instructions
            self._pending_instructions = []
        if self._session_id:
            body["session_id"] = self._session_id
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._url, json=body, headers=self._headers)
            resp.raise_for_status()
            data: dict = resp.json()
            return data["text"]

    async def stream(
        self, text: str, context: dict | None = None
    ) -> AsyncIterator[str]:
        """HTTP adapter falls back to non-streaming."""
        result = await self.turn(text, context)
        yield result

    def assist(self, text: str) -> None:
        """Queue a system instruction for the next turn."""
        self._pending_instructions.append(text)
