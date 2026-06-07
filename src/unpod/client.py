"""Sync and async management SDK entry points."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from dotenv import load_dotenv
from unpod._base_url import service_base
from unpod.management._http import AsyncHTTPClient
from unpod.management.api_keys import ApiKeysResource
from unpod.management.calls import CallsResource
from unpod.management.numbers import NumbersResource
from unpod.management.pipes import PipesResource
from unpod.management.recordings import RecordingsResource
from unpod.management.sessions import SessionsResource
from unpod.management.transcripts import TranscriptsResource
from unpod.management.trunks import TrunksResource
from unpod.management.voice_profiles import VoiceProfilesResource

load_dotenv()


class AsyncClient:
    """Async management SDK entry point."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        orchestrator_base_url: str | None = None,
    ) -> None:
        """Create an async management client.

        The REST base URL is resolved in order: ``base_url`` arg,
        ``UNPOD_SERVICE_BASE_URL`` env, ``https://<UNPOD_BASE_URL>/platform``
        when ``UNPOD_BASE_URL`` is set, else the hosted default.

        Session lifecycle ops (``end``/``transfer``/``merge``) target a
        separate orchestrator service. Its base URL is resolved in order:
        ``orchestrator_base_url`` arg, ``UNPOD_ORCHESTRATOR_BASE_URL`` env,
        else derived by swapping a trailing ``/platform`` in ``base_url`` for
        ``/orchestrator``. If ``base_url`` has no ``/platform`` suffix and no
        override is given, the orchestrator falls back to ``base_url`` — set
        ``orchestrator_base_url`` explicitly for non-standard deployments to
        avoid lifecycle ops hitting the platform service.
        """
        self._api_key = api_key or os.environ.get("UNPOD_API_KEY")
        if not self._api_key:
            raise ValueError("api_key required (pass directly or set UNPOD_API_KEY)")
        self._base_url = (
            base_url
            or os.environ.get("UNPOD_SERVICE_BASE_URL")
            or service_base()
            or "https://api.unpod.ai/platform"
        )
        self._http = AsyncHTTPClient(
            api_key=self._api_key,
            base_url=self._base_url,
        )
        orch_base = (
            orchestrator_base_url
            or os.environ.get("UNPOD_ORCHESTRATOR_BASE_URL")
            or (
                self._base_url[: -len("/platform")] + "/orchestrator"
                if self._base_url.endswith("/platform")
                else self._base_url
            )
        )
        self._orch_http = AsyncHTTPClient(
            api_key=self._api_key,
            base_url=orch_base,
        )
        self.voice_profiles = VoiceProfilesResource(self._http)
        self.trunks = TrunksResource(self._http)
        self.numbers = NumbersResource(self._http)
        self.pipes = PipesResource(self._http)
        self.calls = CallsResource(self._http)
        self.sessions = SessionsResource(self._http, orch_http=self._orch_http)
        self.recordings = RecordingsResource(self._http)
        self.transcripts = TranscriptsResource(self._http)
        self.api_keys = ApiKeysResource(self._http)

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.close()
        await self._orch_http.close()


class Client:
    """Blocking management SDK entry point."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        orchestrator_base_url: str | None = None,
    ) -> None:
        self._async_client = AsyncClient(
            api_key=api_key,
            base_url=base_url,
            orchestrator_base_url=orchestrator_base_url,
        )
        self._api_key = self._async_client._api_key
        self._base_url = self._async_client._base_url
        self.voice_profiles = _SyncResource(self._async_client.voice_profiles)
        self.trunks = _SyncResource(self._async_client.trunks)
        self.numbers = _SyncResource(self._async_client.numbers)
        self.pipes = _SyncResource(self._async_client.pipes)
        self.calls = _SyncResource(self._async_client.calls)
        self.sessions = _SyncResource(self._async_client.sessions)
        self.recordings = _SyncResource(self._async_client.recordings)
        self.transcripts = _SyncResource(self._async_client.transcripts)
        self.api_keys = _SyncResource(self._async_client.api_keys)

    def close(self) -> None:
        _run_blocking(self._async_client.close())

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()


class _SyncResource:
    """Thin blocking facade over an async resource."""

    def __init__(self, async_resource: Any) -> None:
        self._async = async_resource

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._async, name)
        if not callable(attr):
            return attr

        def _call(*args: Any, **kwargs: Any) -> Any:
            result = attr(*args, **kwargs)
            if asyncio.iscoroutine(result):
                return _run_blocking(result)
            return result

        return _call


def _run_blocking(coro: Any) -> Any:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise RuntimeError(
        "Client cannot be used from a running event loop; use AsyncClient"
    )
