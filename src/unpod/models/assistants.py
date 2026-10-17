"""Developer-facing shapes of the assistant API (``client.assistants``).

Only the fields a developer uses are declared; anything else the service adds
is kept (``extra="allow"``), so a new server field never breaks an older SDK.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

_CONFIG = ConfigDict(populate_by_name=True, extra="allow")


class Assistant(BaseModel):
    model_config = _CONFIG

    assistant_id: str
    space_id: str | None = None
    status: str = "draft"  # draft | on | off
    deployed_version: int | None = None
    draft: dict[str, Any] = {}


class AssistantVersion(BaseModel):
    model_config = _CONFIG

    version: int
    deployed_at: str | None = None
    definition: dict[str, Any] | None = None


class EndUser(BaseModel):
    model_config = _CONFIG

    end_user_id: str
    external_id: str = ""
    name: str = ""


class Thread(BaseModel):
    model_config = _CONFIG

    thread_id: str
    end_user_id: str = ""
    version: int | None = None


class Message(BaseModel):
    model_config = _CONFIG

    message_id: str
    role: str
    content: str
    ts: str | None = None


class Approval(BaseModel):
    """A tool call the turn paused on; approve runs exactly these ``args``."""

    model_config = _CONFIG

    approval_id: str
    tool: str
    args: dict[str, Any] = {}
    reason: str = ""


class Reply(BaseModel):
    model_config = _CONFIG

    message_id: str
    reply: str = ""
    status: str  # succeeded | failed | awaiting_approval
    approval: Approval | None = None

    @property
    def awaiting_approval(self) -> bool:
        return self.status == "awaiting_approval"


class MemoryBlock(BaseModel):
    model_config = _CONFIG

    label: str
    value: str = ""
    limit: int | None = None
    editable_by_agent: bool = True
