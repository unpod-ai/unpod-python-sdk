"""Speech Pipe models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Pipe(BaseModel):
    """A Speech Pipe (matches sv_pipes response shape)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    pipe_id: str
    project_id: str = ""
    name: str
    voice_profile_id: str | None = Field(default=None, alias="voice_profile")
    agent_id: str | None = None  # was runner_agent_id (the dev brain)
    agent_endpoint: str | None = None
    recording: bool | dict[str, Any] = False
    max_call_duration_s: int = 3600
    number_id: str | None = None
    number: str | None = None
    status: str = "active"
    first_speaker: str | None = None
    fillers: dict[str, Any] = Field(default_factory=dict)
    created: datetime | None = None
    modified: datetime | None = None


class PipeCreate(BaseModel):
    """Request to create a new Speech Pipe."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    name: str
    voice_profile: str | None = None
    number: str | None = None
    agent_id: str | None = None  # was runner_agent_id
    agent_endpoint: str | None = None
    first_speaker: str | None = None
    recording: bool | dict[str, Any] = False
    max_call_duration_s: int = 3600


class PipeUpdate(BaseModel):
    """Request to update an existing Speech Pipe."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    voice_profile: str | None = None
    agent_id: str | None = None
    agent_endpoint: str | None = None
    recording: bool | None = None
    max_call_duration_s: int | None = None
