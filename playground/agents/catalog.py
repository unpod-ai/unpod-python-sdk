"""Agent catalog — registry mapping a human name to an agent builder.

Each :class:`AgentSpec` binds a display name to an ``agent_id`` (the SDK worker
identity), a one-line description, and a ``build`` factory that returns the
per-call dialog machine. The harness reads this catalog for
``GET /playground/agents`` and to construct the live ``Session`` dialog machine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from playground.agents import faq_bot


@dataclass(frozen=True)
class AgentSpec:
    """One catalog entry: a named, buildable playground agent."""

    name: str
    agent_id: str
    description: str
    build: Callable[[str], Any]


CATALOG: dict[str, AgentSpec] = {
    "faq_bot": AgentSpec(
        name="faq_bot",
        agent_id="playground-faq-bot",
        description="A concise FAQ voice assistant (plain LLM, no flow).",
        build=faq_bot.build,
    ),
}


def get_agent(name: str) -> AgentSpec | None:
    """Return the catalog entry for ``name`` or ``None`` if unknown."""
    return CATALOG.get(name)
