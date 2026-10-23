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
    # IANA zone; None = the assistant's own timezone applies.
    timezone: str | None = None


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
    # Set by ``pending_approvals`` (the assistant-wide list): where it waits.
    thread_id: str | None = None
    end_user_id: str | None = None
    created: str | None = None


class ToolTestResult(BaseModel):
    """One test call of a draft tool: ``ok`` False means the tool answered
    with an error (its text is in ``content``)."""

    model_config = _CONFIG

    ok: bool
    content: str = ""


class McpToolInfo(BaseModel):
    """One tool an MCP server lists (``discover_mcp``). ``description`` is
    the server's own text."""

    model_config = _CONFIG

    name: str
    description: str = ""
    input_schema: dict[str, Any] = {}


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


class RoutineRun(BaseModel):
    """One routine fire (scheduled or manual). ``ts`` is its last change: a run
    that resumed after an approval comes back with its new status."""

    model_config = _CONFIG

    run_id: str
    end_user_id: str = ""
    thread_id: str = ""
    routine_id: str = ""
    trigger: str = ""  # schedule | manual
    status: str  # succeeded | failed | awaiting_approval
    reply: str = ""
    approval: Approval | None = None
    ts: str | None = None

    @property
    def awaiting_approval(self) -> bool:
        return self.status == "awaiting_approval"


class Followup(BaseModel):
    """A follow-up the assistant scheduled for itself: at ``at`` it gets a turn
    in ``thread_id`` with ``note``. ``tainted`` means it was scheduled after the
    assistant read outside content (it still runs, for the same end user)."""

    model_config = _CONFIG

    fu_id: str
    at: str
    note: str = ""
    status: str  # scheduled | fired | cancelled | failed
    created: str | None = None
    tainted: bool = False
    thread_id: str = ""


class TaskItem(BaseModel):
    """One step of the assistant's plan for a thread."""

    model_config = _CONFIG

    id: str
    title: str = ""
    status: str  # todo | doing | done | dropped
