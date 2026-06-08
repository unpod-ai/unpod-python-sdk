"""Trunk models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ByoConfigCreate(BaseModel):
    """BYO carrier SIP configuration for trunk creation."""

    provider: str
    sip_domain: str
    auth_username: str
    auth_password: str
    transport: str = "tls"


class Trunk(BaseModel):
    """A SIP trunk (matches sv_trunks response shape)."""

    model_config = ConfigDict(extra="allow")

    trunk_id: str
    project_id: str
    name: str
    type: str  # "livekit" | "byo"
    status: str = "active"
    provider_trunk_id: str | None = None
    byo_config_provider: str | None = None
    byo_config_sip_domain: str | None = None
    byo_config_auth_username: str | None = None
    byo_config_transport: str | None = None
    created: datetime
    modified: datetime


class TrunkCreate(BaseModel):
    """Request to create a trunk."""

    name: str
    type: str  # "livekit" | "byo"
    provider_trunk_id: str | None = None
    byo_config: ByoConfigCreate | None = None
