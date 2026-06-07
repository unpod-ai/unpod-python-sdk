"""Live-session control plane — routes control actions into a live Session.

Tracks the active SDK ``Session`` objects (keyed by call id) so the web UI's
``POST /playground/sessions/{id}/control`` can reach into the running call and
mutate the dialog machine. M1 supports ``set_llm`` and ``switch_flow``; richer
controls land in later milestones.
"""

from __future__ import annotations

from typing import Any

from unpod import Session


class ControlError(Exception):
    """Raised when a control action cannot be applied."""


class SessionRegistry:
    """Tracks live sessions and applies control actions to them."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def register(self, call_id: str, session: Session) -> None:
        """Track a live session under its call id."""
        self._sessions[call_id] = session

    def unregister(self, call_id: str) -> None:
        """Stop tracking a session that has ended."""
        self._sessions.pop(call_id, None)

    def resolve(self, session_id: str) -> Session:
        """Return the live session for ``session_id``.

        ``"active"`` resolves the single live session (M1 convenience).
        """
        if session_id == "active":
            if len(self._sessions) != 1:
                raise ControlError(
                    f"'active' requires exactly one live session, "
                    f"found {len(self._sessions)}"
                )
            return next(iter(self._sessions.values()))
        session = self._sessions.get(session_id)
        if session is None:
            raise ControlError(f"no live session {session_id!r}")
        return session

    def apply(self, session_id: str, action: str, params: dict[str, Any]) -> None:
        """Apply a control ``action`` to the resolved session."""
        session = self.resolve(session_id)
        adapter = session.dialog_machine
        if adapter is None:
            raise ControlError("session has no dialog machine")

        if action == "set_llm":
            uri = params.get("uri")
            if not isinstance(uri, str) or not uri:
                raise ControlError("set_llm requires a non-empty 'uri'")
            adapter.set_llm(uri)
        elif action == "switch_flow":
            flow = params.get("flow")
            if flow is None:
                raise ControlError("switch_flow requires a 'flow'")
            adapter.switch_flow(
                flow, preserve_memory=bool(params.get("preserve_memory", False))
            )
        else:
            raise ControlError(f"unknown control action {action!r}")
