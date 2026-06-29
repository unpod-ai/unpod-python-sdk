"""OpenAIAdapter — wraps an OpenAI AsyncOpenAI client as a dialog adapter."""

from __future__ import annotations

from typing import Any, AsyncIterator


class OpenAIAdapter:
    """Dialog adapter backed by the OpenAI Chat Completions API.

    Args:
        client: An ``openai.AsyncOpenAI`` instance.
        model: Model name, e.g. ``"gpt-4o"`` or ``"gpt-4o-mini"``.
        system_prompt: Optional system prompt prepended to every conversation.
    """

    def __init__(
        self,
        client: Any,
        model: str = "gpt-4o-mini",
        system_prompt: str | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._history: list[dict[str, str]] = []
        if system_prompt:
            self._history.append({"role": "system", "content": system_prompt})

    async def turn(self, text: str, context: dict | None = None) -> str:
        """Send user text and return the assistant reply."""
        self._history.append({"role": "user", "content": text})
        response = await self._client.chat.completions.create(
            model=self._model,
            messages=self._history,
        )
        content: str = response.choices[0].message.content or ""
        self._history.append({"role": "assistant", "content": content})
        return content

    async def stream(
        self,
        text: str,
        context: dict | None = None,
        language: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield response tokens from the OpenAI streaming API.

        HOT PATH — called by ``session.run()`` on every user turn. Tokens are
        streamed directly to the voice bridge for low-latency TTS synthesis.
        """
        self._history.append({"role": "user", "content": text})
        full: list[str] = []
        async with await self._client.chat.completions.create(
            model=self._model,
            messages=self._history,
            stream=True,
        ) as stream:
            async for chunk in stream:
                delta = chunk.choices[0].delta.content
                if delta:
                    full.append(delta)
                    yield delta
        self._history.append({"role": "assistant", "content": "".join(full)})

    def assist(self, text: str) -> None:
        """Inject a system instruction before the next turn."""
        self._history.append({"role": "system", "content": text})
