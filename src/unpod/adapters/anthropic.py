"""AnthropicAdapter — wraps an Anthropic AsyncAnthropic client as a dialog adapter."""

from __future__ import annotations

from typing import Any, AsyncIterator


class AnthropicAdapter:
    """Dialog adapter backed by the Anthropic Messages API.

    Args:
        client: An ``anthropic.AsyncAnthropic`` instance.
        model: Model name, e.g. ``"claude-haiku-4-5"`` or ``"claude-sonnet-4-6"``.
        system_prompt: Optional system prompt for every conversation.
        max_tokens: Max tokens per response. Defaults to 1024.
    """

    def __init__(
        self,
        client: Any,
        model: str = "claude-haiku-4-5-20251001",
        system_prompt: str | None = None,
        max_tokens: int = 1024,
    ) -> None:
        self._client = client
        self._model = model
        self._system = system_prompt
        self._max_tokens = max_tokens
        self._history: list[dict[str, str]] = []

    async def turn(self, text: str, context: dict | None = None) -> str:
        """Send user text and return the assistant reply."""
        self._history.append({"role": "user", "content": text})
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": self._history,
        }
        if self._system:
            kwargs["system"] = self._system
        response = await self._client.messages.create(**kwargs)
        content: str = response.content[0].text
        self._history.append({"role": "assistant", "content": content})
        return content

    async def stream(
        self,
        text: str,
        context: dict | None = None,
        language: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield response tokens from the Anthropic streaming API.

        HOT PATH — called by ``session.run()`` on every user turn. Tokens are
        streamed directly to the voice bridge for low-latency TTS synthesis.
        """
        self._history.append({"role": "user", "content": text})
        full: list[str] = []
        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": self._history,
        }
        if self._system:
            kwargs["system"] = self._system
        async with self._client.messages.stream(**kwargs) as stream:
            async for text_chunk in stream.text_stream:
                full.append(text_chunk)
                yield text_chunk
        self._history.append({"role": "assistant", "content": "".join(full)})

    def assist(self, text: str) -> None:
        """Inject a system instruction before the next turn.

        Note: Anthropic does not support mid-conversation system messages.
        This prepends as a user/assistant pair to approximate the effect.
        """
        self._history.append({"role": "user", "content": f"[system] {text}"})
        self._history.append({"role": "assistant", "content": "Understood."})
