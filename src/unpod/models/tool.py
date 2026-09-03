"""Models for the tool catalog and a tenant's own tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BuiltinTool:
    """A tool the platform implements.

    ``availability`` is ``always`` (every agent has it) or ``opt-in`` (attach it
    first). ``config_key`` is the ``tools`` key that configures it, when it has
    one — ``end_call`` has nothing to configure and nothing to attach.
    """

    name: str
    description: str = ""
    availability: str = "always"
    config_key: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)


@dataclass
class CustomToolSupport:
    """What a tool of your own may look like on this deployment."""

    supported: bool = True
    config_key: str = "custom"
    arg_types: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)
    default_timeout_s: float = 10.0
    max_timeout_s: float = 30.0
    notes: list[str] = field(default_factory=list)
    example: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCatalog:
    """Everything an agent can be given."""

    builtin: list[BuiltinTool] = field(default_factory=list)
    custom: CustomToolSupport = field(default_factory=CustomToolSupport)


@dataclass
class CustomTool:
    """One tool this project authored.

    Defined once and attached to agents BY ID, so editing the URL or headers
    reaches every agent using it rather than needing a rewrite on each.
    """

    tool_id: str
    description: str = ""
    method: str = "GET"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    body: dict[str, Any] = field(default_factory=dict)
    args: dict[str, Any] = field(default_factory=dict)
    timeout: float | None = None
    #: Agents this tool is attached to.
    agent_ids: list[str] = field(default_factory=list)


__all__ = ["BuiltinTool", "CustomTool", "CustomToolSupport", "ToolCatalog"]
