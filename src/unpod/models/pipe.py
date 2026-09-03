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
    playbook_id: str | None = None
    recording: bool | dict[str, Any] = False
    max_call_duration_s: int = 3600
    number_id: str | None = None
    number: str | None = None
    status: str = "active"
    first_speaker: str | None = None
    #: Domain dictionary this agent speaks with (``client.domain_dictionaries``).
    #: The runtime reads the tag from HERE, so an agent showing None applies no
    #: dictionary however many exist in the project.
    domain: str | None = None
    #: Ambience under the agent's speech. ``background_sound_volume`` of None
    #: means the platform's default level, not silence; ambience is off when
    #: ``background_sound_enabled`` is False or no room is named.
    background_sound: str | None = None
    background_sound_enabled: bool = True
    background_sound_volume: float | None = None
    #: Inbound noise canceller for this agent. ``None`` means the deployment's
    #: own default, not "no cancellation".
    noise_cancellation: str | None = None
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
    #: Domain dictionary tag; see ``client.domain_dictionaries``.
    domain: str | None = None
    #: Ambience; see :class:`Pipe`.
    background_sound: str | None = None
    background_sound_enabled: bool | None = None
    background_sound_volume: float | None = None
    noise_cancellation: str | None = None


class PipeUpdate(BaseModel):
    """Request to update an existing Speech Pipe."""

    model_config = ConfigDict(extra="allow")

    name: str | None = None
    voice_profile: str | None = None
    agent_id: str | None = None
    agent_endpoint: str | None = None
    recording: bool | None = None
    max_call_duration_s: int | None = None
    #: None leaves the tag alone; an EMPTY STRING detaches the dictionary.
    domain: str | None = None
