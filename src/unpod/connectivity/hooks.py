"""Hook registry for lifecycle events."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable, Coroutine

from unpod._logging import get_logger

logger = get_logger("hooks")

VALID_EVENTS: frozenset[str] = frozenset(
    {
        "call_start",
        "user_turn",
        "user_partial",
        "agent_turn",
        "tool_call",
        "tool_result",
        "silence",
        "interruption",
        "metric",
        "state",
        "call_end",
        "error",
        "llm_call",
        "turn_complete",
    }
)


class HookRegistry:
    """Registry for async event handlers."""

    def __init__(self) -> None:
        self._handlers: dict[str, list[Callable[..., Coroutine[Any, Any, None]]]] = (
            defaultdict(list)
        )

    def on(
        self, event: str
    ) -> Callable[
        [Callable[..., Coroutine[Any, Any, None]]],
        Callable[..., Coroutine[Any, Any, None]],
    ]:
        """Decorator to register an async handler for an event."""
        if event not in VALID_EVENTS:
            raise ValueError(f"Unknown event '{event}'. Valid: {sorted(VALID_EVENTS)}")

        def decorator(
            fn: Callable[..., Coroutine[Any, Any, None]],
        ) -> Callable[..., Coroutine[Any, Any, None]]:
            self._handlers[event].append(fn)
            return fn

        return decorator

    async def fire(self, event: str, *args: Any, **kwargs: Any) -> None:
        """Fire all registered handlers for an event.

        A raising handler still propagates (unchanged behavior — the SDK does
        not decide that your hook's failure is survivable), but it is logged
        with the handler's name first: an exception surfacing from deep in the
        session loop is otherwise very hard to attribute to a hook.
        """
        for handler in self._handlers.get(event, []):
            try:
                await handler(*args, **kwargs)
            except Exception:
                logger.exception(
                    "hook %r registered for %r raised",
                    getattr(handler, "__qualname__", repr(handler)),
                    event,
                )
                raise
