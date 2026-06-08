"""Playground voice profile catalog — lightweight STT+TTS combos for the UI selector.

Each entry maps a human-readable label to the STT and TTS providers it uses.
Distinct from ``unpod.models.VoiceProfile`` (the full server-managed model) —
this is a simple display catalog for the playground frontend only.
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceProfile:
    id: str
    name: str
    stt: str
    tts: str


VOICE_PROFILES: list[VoiceProfile] = [
    VoiceProfile(id="default", name="Default", stt="Deepgram Nova-2", tts="ElevenLabs"),
    VoiceProfile(id="fast", name="Fast", stt="Whisper", tts="OpenAI TTS"),
    VoiceProfile(id="quality", name="Quality", stt="Deepgram Nova-2", tts="Cartesia"),
]
