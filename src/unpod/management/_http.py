"""Shared async HTTP client with auth, request IDs, and retries."""

from __future__ import annotations

import uuid

import httpx

from unpod.management._auth import Auth


def unwrap_data(resp: dict | list) -> dict | list:
    """Return the common API payload body."""
    if (
        isinstance(resp, dict)
        and len(resp) == 1
        and isinstance(resp.get("data"), (dict, list))
    ):
        return resp["data"]
    return resp


class AsyncHTTPClient:
    """Shared httpx async client with auth, request IDs, and retries."""

    DEFAULT_BASE_URL = "https://api.unpod.ai"
    DEFAULT_TIMEOUT = 30.0

    def __init__(
        self,
        auth: Auth,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._auth = auth
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _headers(self) -> dict[str, str]:
        """Auth + request ID headers.

        The auth headers come from the configured ``Auth`` strategy (Bearer for
        direct supervoice, JWT + Org-Handle for the backend-core proxy).
        """
        return {
            **self._auth.headers(),
            "Content-Type": "application/json",
            "X-Request-Id": str(uuid.uuid4()),
        }

    async def _ensure_client(self) -> httpx.AsyncClient:
        """Lazily create the underlying httpx client."""
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._client

    async def get(self, path: str, params: dict[str, str] | None = None) -> dict | list:
        """Send a GET request and return parsed JSON response."""
        client = await self._ensure_client()
        resp = await client.get(path, headers=self._headers(), params=params)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def post(self, path: str, json: dict | None = None) -> dict | list:
        """Send a POST request and return parsed JSON response."""
        client = await self._ensure_client()
        resp = await client.post(path, headers=self._headers(), json=json)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def put(self, path: str, json: dict | None = None) -> dict:
        """Send a PUT request and return parsed JSON response."""
        client = await self._ensure_client()
        resp = await client.put(path, headers=self._headers(), json=json)
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def delete(self, path: str) -> None:
        """Send a DELETE request (no response body)."""
        client = await self._ensure_client()
        resp = await client.delete(path, headers=self._headers())
        resp.raise_for_status()

    async def delete_with_response(self, path: str) -> dict:
        """Send a DELETE request and return parsed JSON response."""
        client = await self._ensure_client()
        resp = await client.delete(path, headers=self._headers())
        resp.raise_for_status()
        return resp.json()  # type: ignore[no-any-return]

    async def close(self) -> None:
        """Close the underlying httpx client."""
        if self._client:
            await self._client.aclose()
            self._client = None
