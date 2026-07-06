"""Call models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class Call(BaseModel):
    """A voice call (matches sv_calls response shape)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    call_id: str = Field(validation_alias=AliasChoices("id", "call_id"))
    project_id: str = ""
    pipe_id: str | None = None
    agent_id: str | None = None
    direction: str = "outbound"
    from_number: str | None = None
    to_number: str | None = Field(
        default=None, validation_alias=AliasChoices("to_number", "user_number")
    )
    number_id: str | None = None
    trunk_id: str | None = None
    session_id: str | None = None
    room_name: str | None = None
    instructions: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_s: float | None = None
    status: str = "pending"
    end_reason: str | None = None
    created: datetime | None = None
    modified: datetime | None = None

    @property
    def id(self) -> str:
        return self.call_id

    @property
    def user_number(self) -> str | None:
        return self.to_number


class CallCreate(BaseModel):
    """Request to initiate an outbound call."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pipe_id: str = Field(default="", alias="agent")
    to_number: str = Field(default="", alias="user_number")
    from_number: str | None = None
    instructions: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)

    @property
    def agent(self) -> str:
        return self.pipe_id

    @property
    def user_number(self) -> str:
        return self.to_number
