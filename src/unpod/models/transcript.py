"""Transcript models."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TurnTiming(BaseModel):
    """Per-turn latency timing in milliseconds."""

    audio_ingress_ms: int | None = None
    stt_ms: int | None = None
    bridge_to_dev_ms: int | None = None
    dev_brain_ms: int | None = None
    tts_ms: int | None = None


class TranscriptTurn(BaseModel):
    """One transcript turn."""

    speaker: str
    text: str
    timestamp_ms: int | None = None
    timing: TurnTiming = Field(default_factory=TurnTiming)


class Transcript(BaseModel):
    """Transcript for a call or session."""

    model_config = ConfigDict(extra="allow")

    call_id: str | None = None
    session_id: str | None = None
    turns: list[TranscriptTurn] = Field(default_factory=list)


__all__ = ["Transcript", "TranscriptTurn", "TurnTiming"]
