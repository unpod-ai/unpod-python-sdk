"""Voice profile models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class VoiceProfile(BaseModel):
    """A voice profile (STT + TTS bundle, matches sv_voice_profiles response shape)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    profile_id: str = Field(alias="id")
    project_id: str | None = None  # None = global/seeded profile
    name: str = Field(default="", alias="persona")
    description: str | None = None
    gender: str | None = None
    quality: str | None = Field(default=None, alias="quality_tier")
    languages: list[str] = Field(default_factory=list)
    language: str | None = None
    stt_provider: str | None = None
    stt_model: str | None = None
    stt_languages: list[str] = Field(default_factory=list)
    tts_provider: str | None = None
    tts_model: str | None = None
    tts_voice: str | None = None
    tts_language: str | None = None
    voice_temperature: float | None = None
    voice_speed: float | None = None
    voice_prompt: str | None = None
    greeting_message: str | None = None
    estimated_cost_per_min_usd: float | None = Field(
        default=None, alias="price_per_minute"
    )
    latency_ms: int | None = Field(default=None, alias="latency_p95_ms")
    created: datetime | None = None
    modified: datetime | None = None

    @property
    def id(self) -> str:
        return self.profile_id

    @property
    def persona(self) -> str:
        return self.name

    @property
    def quality_tier(self) -> str | None:
        return self.quality

    @property
    def price_per_minute(self) -> float | None:
        return self.estimated_cost_per_min_usd

    @property
    def latency_p95_ms(self) -> int | None:
        return self.latency_ms
