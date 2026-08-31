"""Unified agent management namespace."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import Pipe


def _read_playbook(value: str | os.PathLike[str]) -> str:
    """Resolve a path or accept raw YAML text."""
    if isinstance(value, os.PathLike):
        return Path(value).read_text(encoding="utf-8")
    try:
        path = Path(value)
        if path.is_file():
            return path.read_text(encoding="utf-8")
    except OSError:
        # Long/raw YAML strings are not valid filesystem paths.
        pass
    return value


class VoiceAgentResource:
    """Create voice agents from runner, endpoint, prompt, or playbook."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def create(
        self,
        name: str,
        voice_profile: str | None = None,
        *,
        agent_id: str | None = None,
        agent_endpoint: str | None = None,
        prompt: str | None = None,
        playbook: str | os.PathLike[str] | None = None,
        recording: bool = False,
        max_call_duration_s: int = 3600,
        domain: str | None = None,
    ) -> Pipe:
        """Create a voice agent from exactly one brain source.

        ``domain`` names the dictionary the agent speaks with (see
        ``client.domain_dictionaries``); it is stamped on the agent row, and on
        the playbook doc too when the brain is a playbook.
        """
        sources: dict[str, Any] = {
            "agent_id": agent_id,
            "agent_endpoint": agent_endpoint,
            "prompt": prompt,
            "playbook": playbook,
        }
        present = [
            key for key, value in sources.items()
            if value is not None and str(value).strip()
        ]
        if not present:
            raise ValueError("exactly one brain source is required; received none")
        if len(present) > 1:
            raise ValueError(
                "exactly one brain source is required; received conflicts: "
                + ", ".join(present)
            )
        body: dict[str, Any] = {
            "name": name,
            "recording": recording,
            "max_call_duration_s": max_call_duration_s,
        }
        if voice_profile is not None:
            body["voice_profile"] = voice_profile
        if domain is not None:
            body["domain"] = domain
        if agent_id is not None:
            body["agent_id"] = agent_id
        elif agent_endpoint is not None:
            body["agent_endpoint"] = agent_endpoint
        elif prompt is not None:
            body["prompt"] = prompt
        else:
            body["playbook"] = _read_playbook(playbook)  # type: ignore[arg-type]
        resp = unwrap_data(
            await self._http.post("/api/v2/platform/speech/v1/agent/voice", json=body)
        )
        return Pipe(**resp)


class AgentNamespace:
    """Agent resource namespace."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self.voice = VoiceAgentResource(http)


__all__ = ["AgentNamespace", "VoiceAgentResource"]
