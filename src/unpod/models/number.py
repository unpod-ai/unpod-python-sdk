"""Phone number models."""

from __future__ import annotations

from datetime import datetime

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class Number(BaseModel):
    """A provisioned phone number (matches sv_numbers response shape)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow", coerce_numbers_to_str=True)

    number_id: str = Field(alias="id")
    project_id: str | None = None
    number: str
    trunk_id: str | None = None
    provider_trunk_id: str | None = None
    trunk_type: str = "livekit"
    country: str | None = Field(default=None)
    capabilities: list[str] = Field(default_factory=list)
    status: str = Field(default="available", validation_alias=AliasChoices("status", "state"))
    pipe_id: str | None = None
    active_call_id: str | None = None
    created: datetime | None = Field(default=None, alias="created_at")
    modified: datetime | None = None

    @property
    def id(self) -> str:
        return self.number_id

    @property
    def created_at(self) -> datetime | None:
        return self.created

