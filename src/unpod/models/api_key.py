"""API key model."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ApiKey(BaseModel):
    """An API key returned by the platform.

    ``raw_key`` contains the plaintext secret and is returned **once only**
    at creation time; subsequent fetches omit it.
    """

    model_config = ConfigDict(extra="allow")

    key_id: str
    name: str
    org_id: str
    project_id: str
    status: str = "active"
    created: datetime | None = None
    raw_key: str | None = None
