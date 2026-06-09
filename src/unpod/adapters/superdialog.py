"""SuperDialogAdapter — wraps superdialog.DialogMachine / LLMAgent."""

from __future__ import annotations

from typing import Any, AsyncIterator


class SuperDialogAdapter:
    """Thin adapter around a superdialog DialogMachine or LLMAgent."""

    def __init__(self, dm: Any) -> None:
        self._dm = dm

    # --- core dialog interface ---

    async def turn(self, text: str, context: dict | None = None) -> str:
        """Send user text and return the assistant reply."""
        result = await self._dm.turn(text)
        return result.text  # type: ignore[no-any-return]

    async def stream(
        self, text: str, context: dict | None = None
    ) -> AsyncIterator[str]:
        """Stream assistant reply chunks."""
        async for chunk in await self._dm.turn(text, stream=True):
            yield chunk.text

    def assist(self, text: str) -> None:
        """Inject a system-level assist directive."""
        self._dm.assist(text)

    # --- superdialog-specific helpers ---

    def set_llm(self, uri: str) -> None:
        """Switch the underlying LLM model."""
        self._dm.set_llm(uri)

    def switch_flow(self, flow: Any, *, preserve_memory: bool = False) -> None:
        """Replace the active dialog flow."""
        self._dm.switch_flow(flow, preserve_memory=preserve_memory)

    @property
    def is_complete(self) -> bool:
        """Whether the dialog has reached a terminal state."""
        return self._dm.is_complete  # type: ignore[no-any-return]

    def register_llm_callback(self, fn: Any) -> None:
        """Register _on_llm_complete on the underlying ToolCallAdapter. No-op if not available."""
        _adapter = getattr(self._dm, "_adapter", None)
        if _adapter is not None and hasattr(_adapter, "_on_llm_complete"):
            _adapter._on_llm_complete = fn

    @property
    def state(self) -> dict:
        """Current dialog state snapshot."""
        return self._dm.state  # type: ignore[no-any-return]
