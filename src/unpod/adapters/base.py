"""DialogAdapter — runtime-checkable protocol for dialog engines."""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class DialogAdapter(Protocol):
    """Minimal interface every dialog backend must satisfy.

    Entry points:
      - ``turn()`` - returns a complete response string (not called during live calls)
      - ``stream()`` - yields tokens; THIS is the hot path called by ``session.run()``
      - ``assist()`` - injects a system instruction before the next turn

    Optional, duck-typed (``session.run()`` calls it ``hasattr``-guarded, so it is
    NOT a required member):
      - ``mark_interrupted(heard_text: str | None = None)`` - truncate the last
        assistant turn to what the caller actually heard on a barge-in, keeping
        the next turn's context honest.
    """

    async def turn(self, text: str, context: dict | None = None) -> str: ...

    async def stream(
        self,
        text: str,
        context: dict | None = None,
        language: str | None = None,
    ) -> AsyncIterator[str]:
        """Yield response tokens.

        HOT PATH - ``session.run()`` calls this on every user turn and streams
        tokens directly to the voice bridge. Implement real token streaming here.
        A single-chunk fallback causes choppy audio.

        ``turn()`` is NOT called by the framework during live calls.
        """
        ...

    def assist(self, text: str) -> None: ...
