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
            if not self._sessions:
                raise ControlError("'active' requires a live session, found none")
            # Single-user dev convenience: the most recently registered call is
            # the active one. Tolerates stale sessions left by prior calls that
            # did not unregister cleanly (failed handshakes, abrupt disconnects).
            return next(reversed(self._sessions.values()))
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
            try:
                adapter.set_llm(uri)
            except AttributeError as exc:  # non-flow agent (e.g. plain LLMAgent)
                raise ControlError("this agent does not support set_llm") from exc
        elif action == "switch_flow":
            flow = params.get("flow")
            if flow is None:
                raise ControlError("switch_flow requires a 'flow'")
            try:
                adapter.switch_flow(
                    flow, preserve_memory=bool(params.get("preserve_memory", False))
                )
            except KeyError as exc:
                raise ControlError(f"unknown flow {flow!r}") from exc
            except AttributeError as exc:  # agent has no FlowSet to switch within
                raise ControlError(
                    "this agent does not support flow switching"
                ) from exc
        else:
            raise ControlError(f"unknown control action {action!r}")
