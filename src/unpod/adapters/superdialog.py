"""SuperDialogAdapter — wraps superdialog.DialogMachine / LLMAgent."""

from __future__ import annotations

import inspect
from typing import Any, AsyncIterator


class SuperDialogAdapter:
    """Thin adapter around a superdialog DialogMachine or LLMAgent."""

    def __init__(self, dm: Any) -> None:
        self._dm = dm
        turn = getattr(dm, "turn", None)
        try:
            self._dm_accepts_language = (
                turn is not None and "language" in inspect.signature(turn).parameters
            )
        except (TypeError, ValueError):
            self._dm_accepts_language = False

    # --- core dialog interface ---

    async def turn(self, text: str, context: dict | None = None) -> str:
        """Send user text and return the assistant reply."""
        result = await self._dm.turn(text)
        return result.text  # type: ignore[no-any-return]

    def assist(self, text: str) -> None:
        """Inject a system-level assist directive."""
        self._dm.assist(text)

    def mark_interrupted(self, heard_text: str | None = None) -> None:
        """Truncate the brain's last assistant turn to what the caller heard.

        Best-effort passthrough: no-op when the underlying dialog object doesn't
        expose ``mark_interrupted`` (older superdialog, or a wrapper without it),
        in which case the Session keeps its legacy interruption nudge.
        """
        fn = getattr(self._dm, "mark_interrupted", None)
        if callable(fn):
            fn(heard_text)

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
        """Register a per-LLM-call usage callback on the underlying DialogMachine.

        Prefers ``DialogMachine.register_llm_callback`` which wires both the
        graph (ToolCallAdapter) and playbook (provider adapters) engines. Falls
        back to setting the toolcall adapter's ``_on_llm_complete`` directly for
        older superdialog builds without that method."""
        self._llm_callback = fn
        dm_register = getattr(self._dm, "register_llm_callback", None)
        if callable(dm_register):
            dm_register(fn)
            return
        # Fallback (graph-only): older superdialog without register_llm_callback.
        _adapter = getattr(self._dm, "_adapter", None)
        if _adapter is not None and hasattr(_adapter, "_on_llm_complete"):
            _adapter._on_llm_complete = fn

    async def stream(
        self,
        text: str,
        context: dict | None = None,
        language: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream assistant reply chunks."""
        cb = getattr(self, "_llm_callback", None)
        if cb is not None:
            # Ensure machine is initialized first — _ensure_machine may create a new adapter
            # instance, so we must call it before reading _adapter to get the final reference.
            if hasattr(self._dm, "_ensure_machine"):
                try:
                    await self._dm._ensure_machine()
                except Exception:
                    pass
            _adapter = getattr(self._dm, "_adapter", None)
            if _adapter is not None and hasattr(_adapter, "_on_llm_complete"):
                _adapter._on_llm_complete = cb
        kwargs: dict[str, Any] = {"stream": True}
        if language is not None and self._dm_accepts_language:
            kwargs["language"] = language
        async for chunk in await self._dm.turn(text, **kwargs):
            yield chunk.text

    @property
    def state(self) -> dict:
        """Current dialog state snapshot."""
        return self._dm.state  # type: ignore[no-any-return]
