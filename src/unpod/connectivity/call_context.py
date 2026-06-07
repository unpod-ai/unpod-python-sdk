"""CallContext — per-call metadata and session reference."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from unpod.connectivity.session import Session


@dataclass
class CallContext:
    """Immutable context for a single call."""

    call_id: str
    session_id: str
    agent_id: str
    direction: str  # "inbound" | "outbound"
    user_number: str
    instructions: str | None
    data: dict[str, Any]
    session: Session
    room: dict[str, Any] = field(default_factory=dict)
    """LiveKit room metadata ({url, token, name}) from dispatch. Informational
    only — the supervoice worker owns media; the SDK brain is text-only."""
