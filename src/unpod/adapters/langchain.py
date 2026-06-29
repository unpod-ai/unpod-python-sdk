"""LangChainAdapter - wraps a LangChain Runnable as a dialog adapter."""

from __future__ import annotations

from typing import Any, AsyncIterator


class LangChainAdapter:
    """Dialog adapter backed by a LangChain chain/runnable.

    Args:
        chain: Any LangChain Runnable with ``ainvoke`` and ``astream`` methods.
        input_key: The key used to pass the message history to the chain.
            Defaults to ``"messages"`` which works with ``ChatPromptTemplate | ChatModel``
            chains. Set to ``"input"`` or another key if your chain uses a different schema.
    """

    def __init__(self, chain: Any, input_key: str = "messages") -> None:
        self._chain = chain
        self._input_key = input_key
        self._history: list[dict[str, str]] = []

    async def turn(self, text: str, context: dict | None = None) -> str:
        """Invoke the chain and return the response content."""
        self._history.append({"role": "user", "content": text})
        result = await self._chain.ainvoke({self._input_key: self._history})
        content: str = result.content if hasattr(result, "content") else str(result)
        self._history.append({"role": "assistant", "content": content})
        return content

    async def stream(
        self,
        text: str,
        context: dict | None = None,
        language: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield response tokens from the chain.

        This is the hot path called by ``session.run()`` on every user turn.
        Tokens are streamed directly to the voice bridge - implement real
        token streaming here. A single-chunk fallback causes choppy audio.

        Note: ``turn()`` is NOT called by the framework during live calls.
        """
        self._history.append({"role": "user", "content": text})
        full: list[str] = []
        async for chunk in self._chain.astream({self._input_key: self._history}):
            content: str = chunk.content if hasattr(chunk, "content") else str(chunk)
            full.append(content)
            yield content
        self._history.append({"role": "assistant", "content": "".join(full)})

    def assist(self, text: str) -> None:
        """Inject a system instruction before the next turn."""
        self._history.append({"role": "system", "content": text})
