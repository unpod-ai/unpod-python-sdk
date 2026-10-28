"""Turn whatever a caller holds for a space into the handle the routes need.

The journey routes are keyed by a space's handle (its slug: superbook's keys
are minted per slug). A caller may hold the handle, the space's token, or a
``Space`` object; only the token needs a lookup, and Django's space route
(``GET /api/v2/platform/spaces/<token>/``) is the one that resolves it.
"""

from __future__ import annotations

from typing import Any

import httpx

from unpod.management._auth import JWTAuth
from unpod.management._http import AsyncHTTPClient
from unpod.models import Space

SpaceRef = str | Space

_SPACES_PATH = "/api/v2/platform/spaces"


def is_proxy_auth(http: AsyncHTTPClient) -> bool:
    """Whether ``http`` talks to backend-core as a signed-in user (JWT + Org-Handle)."""
    return isinstance(getattr(http, "_auth", None), JWTAuth)


def requires_proxy_auth(http: AsyncHTTPClient, what: str) -> None:
    """Refuse up front rather than let Django answer 401.

    Every Django route these calls reach (channels, Gmail sign-in, space
    lookup) authenticates a user's JWT only; a DRF token or an API key is
    rejected there, with a message that does not say why.
    """
    if not is_proxy_auth(http):
        raise ValueError(f"{what} needs proxy-mode auth: JWTAuth with an Org-Handle")


class SpaceResolver:
    """Resolves and remembers each space reference for one client."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http
        self._cache: dict[str, str] = {}

    async def slug(self, space: SpaceRef) -> str:
        if isinstance(space, Space):
            return space.slug
        value = str(space)
        if value in self._cache:
            return self._cache[value]
        # Without a user JWT the lookup route would 401, so a value is taken as
        # the handle it most often is; a token then fails at the server, which
        # is the documented requirement rather than a silent guess.
        slug = await self._lookup(value) if is_proxy_auth(self._http) else None
        self._cache[value] = slug or value
        return self._cache[value]

    async def _lookup(self, value: str) -> str | None:
        try:
            resp: Any = await self._http.get(f"{_SPACES_PATH}/{value}/", params=None)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None  # not a token: it is the handle itself
            raise
        data = resp.get("data", resp) if isinstance(resp, dict) else None
        # Django answers some misses with a 2xx and only a message: no slug
        # there means the value was not a token.
        slug = data.get("slug") if isinstance(data, dict) else None
        return str(slug) if slug else None
