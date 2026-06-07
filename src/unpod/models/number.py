"""Phone number models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Number(BaseModel):
    """A provisioned phone number (matches sv_numbers response shape)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    number_id: str = Field(alias="id")
    project_id: str | None = None
    number: str
    trunk_id: str | None = None
    provider_trunk_id: str | None = None
    trunk_type: str = "livekit"
    country: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    status: str = "available"
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


class NumberPurchase(BaseModel):
    """Request to purchase/provision a phone number."""

    country: str
    capabilities: list[str] = Field(default_factory=lambda: ["voice"])
