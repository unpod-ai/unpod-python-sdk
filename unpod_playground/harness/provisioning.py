"""Provisioning — ensure a Pipe + voice profile exist for a session.

M1 uses the supervoice **WS dev path** (``POST /connect`` + ``/ws/audio``), which
serves a single globally-configured voice profile and dispatches to the
registered worker. No per-session Pipe/profile provisioning is required, so this
is a no-op placeholder. Pipe/profile provisioning arrives with the LiveKit
transport (M4) and voice-profile picker (M5).
"""

from __future__ import annotations

from typing import Any


async def ensure_session_resources(agent_id: str) -> dict[str, Any]:
    """Return the resource descriptor for a session (M1: empty / WS dev path)."""
    return {"transport": "ws", "agent_id": agent_id}
