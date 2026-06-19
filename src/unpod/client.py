"""Sync and async management SDK entry points."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from dotenv import load_dotenv
from unpod._base_url import platform_base, service_base
from unpod.management._auth import Auth, BearerAuth
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
from unpod.telephony import TelephonyNamespace

load_dotenv()


class AsyncClient:
    """Async management SDK entry point."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        orchestrator_base_url: str | None = None,
        auth: Auth | None = None,
    ) -> None:
        """Create an async management client.

        **Auth / mode.** Pass an ``auth`` strategy to choose how requests are
        authenticated:

        - ``BearerAuth(api_key)`` — *direct* mode against supervoice (the
          default when ``api_key`` / ``UNPOD_API_KEY`` is supplied).
        - ``JWTAuth(token, org_handle)`` — *proxy* mode against the unpod
          backend-core proxy. Point ``base_url`` at the proxy
          (``https://<host>/api/v2/platform/speech``); the resource paths are
          identical to direct mode.

        The REST base URL is resolved in order: ``base_url`` arg,
        ``UNPOD_SERVICE_BASE_URL`` env, ``https://<UNPOD_BASE_URL>/platform``
        when ``UNPOD_BASE_URL`` is set, else the hosted default.

        Session lifecycle ops (``end``/``transfer``/``merge``) target a
        separate orchestrator service and are **not available in proxy mode**
        (the proxy fronts the management plane only). Its base URL is resolved
        in order: ``orchestrator_base_url`` arg, ``UNPOD_ORCHESTRATOR_BASE_URL``
        env, else derived by swapping a trailing ``/platform`` in ``base_url``
        for ``/orchestrator``, else ``base_url``.
        """
        if auth is None:
            api_key = api_key or os.environ.get("UNPOD_API_KEY")
            if not api_key:
                raise ValueError(
                    "provide auth=... (BearerAuth/JWTAuth) or api_key "
                    "(directly or via UNPOD_API_KEY)"
                )
            auth = BearerAuth(api_key)
        self._auth = auth
        # Retained for backward compatibility; None in JWT/proxy mode.
        self._api_key = api_key or os.environ.get("UNPOD_API_KEY")
        self._base_url = (
            base_url
            or os.environ.get("UNPOD_SERVICE_BASE_URL")
            or service_base()
            or "https://api.unpod.ai/platform"
        )
        self._http = AsyncHTTPClient(auth=auth, base_url=self._base_url)
        orch_base = (
            orchestrator_base_url
            or os.environ.get("UNPOD_ORCHESTRATOR_BASE_URL")
            or (
                self._base_url[: -len("/platform")] + "/orchestrator"
                if self._base_url.endswith("/platform")
                else self._base_url
            )
        )
        self._orch_http = AsyncHTTPClient(auth=auth, base_url=orch_base)
        # backend-core platform plane (telephony): a different service from the
        # supervoice management plane. Requires JWT/proxy auth (Org-Handle-scoped).
        platform_url = (
            os.environ.get("UNPOD_PLATFORM_BASE_URL")
            or platform_base()
            or (
                self._base_url[: -len("/platform")] + "/api/v2/platform"
                if self._base_url.endswith("/platform")
                else self._base_url
            )
        )
        self._platform_http = AsyncHTTPClient(auth=auth, base_url=platform_url)
        self.telephony = TelephonyNamespace(self._platform_http)
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
        await self._platform_http.close()


class Client:
    """Blocking management SDK entry point."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        orchestrator_base_url: str | None = None,
        auth: Auth | None = None,
    ) -> None:
        self._async_client = AsyncClient(
            api_key=api_key,
            base_url=base_url,
            orchestrator_base_url=orchestrator_base_url,
            auth=auth,
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
        self.telephony = _SyncTelephonyNamespace(self._async_client.telephony)

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


class _SyncTelephonyNamespace:
    """Blocking facade over ``client.telephony`` (nested resources + overview)."""

    def __init__(self, async_ns: Any) -> None:
        self._async = async_ns
        self.numbers = _SyncResource(async_ns.numbers)
        self.trunks = _SyncResource(async_ns.trunks)

    def overview(self) -> Any:
        return _run_blocking(self._async.overview())
