"""Recording models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Recording(BaseModel):
    """A stored call recording."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    recording_id: str = Field(alias="id")
    call_id: str
    duration_s: float | None = None
    format: str | None = None
    size_bytes: int | None = None
    url: str | None = None

    @property
    def id(self) -> str:
        return self.recording_id
