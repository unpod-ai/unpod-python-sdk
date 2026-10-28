"""Autonomous-agent models (flows and journeys).

Developer-facing fields only; ``extra="allow"`` keeps everything else, so a
newer engine never breaks an older SDK.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

_CONFIG = ConfigDict(populate_by_name=True, extra="allow")


class Space(BaseModel):
    """A space as a caller may hold it: its handle (``slug``) is what routes use."""

    model_config = _CONFIG

    slug: str
    token: str | None = None
    name: str = ""


class Channel(BaseModel):
    """One way a flow can reach people in a space: a mailbox, a number.

    ``connection_id`` is the org's connection in the Unpod app; ``account_id``
    is the sending account flows use, set once the connection is linked to
    this space. A channel with an ``account_id`` is ``ready`` to send from.
    """

    model_config = _CONFIG

    type: str
    space: str
    name: str = ""
    address: str = ""
    connection_id: int | None = None
    account_id: str | None = None
    #: The channel's kind (``email``): a step's ``action``.
    connector_type: str | None = None
    #: The connector sending on it (``gmail``): what an agent's tools name.
    connector_id: str | None = None

    @property
    def ready(self) -> bool:
        return bool(self.account_id)


class ConnectResult(BaseModel):
    """What ``channels.connect`` did: a ready channel, or a sign-in to finish."""

    model_config = _CONFIG

    channel: Channel | None = None
    #: Open this in a browser to connect a new Gmail mailbox.
    sign_in_url: str | None = None

    @property
    def ready(self) -> bool:
        return bool(self.channel and self.channel.ready)


class Flow(BaseModel):
    """One flow version: its steps plus the agent settings."""

    model_config = _CONFIG

    #: Left out on create, the service picks one; always set on a stored flow.
    flow_id: str | None = None
    space_id: str = ""
    name: str = ""
    version: int = 1
    status: str = ""
    steps: list[dict[str, Any]] = Field(default_factory=list)
    triggers: list[Any] = Field(default_factory=list)
    agentic_profile: str | None = None
    agentic_tools: list[str] = Field(default_factory=list)
    agentic_plan: str | None = None
    agentic_follow_ups: bool = True


class JourneyStartResult(BaseModel):
    """One contact's outcome from ``journeys.start``."""

    model_config = _CONFIG

    participant_id: str | None = None
    journey_id: str | None = None
    result: str = ""


class Journey(BaseModel):
    """One contact going through one flow."""

    model_config = _CONFIG

    journey_id: str
    space_id: str = ""
    flow_id: str = ""
    participant_id: str = ""
    status: str = ""
    current_step_id: str | None = None
    inbox_thread_id: str | None = None
    thread_id: str | None = None
    created: datetime | None = None
    modified: datetime | None = None


class ConversationMessage(BaseModel):
    """One message on a journey's inbox thread."""

    model_config = _CONFIG

    block_id: str = ""
    direction: str = ""
    text: str = ""
    subject: str = ""
    created: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _from_block(cls, value: Any) -> Any:
        if isinstance(value, dict) and isinstance(value.get("data"), dict):
            data = value["data"]
            value = {
                **value,
                "text": value.get("text") or data.get("content") or data.get("body") or "",
                "subject": value.get("subject") or data.get("subject") or "",
            }
        return value
