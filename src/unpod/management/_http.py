"""Shared async HTTP client with auth, request IDs, and retries."""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from unpod._logging import get_logger
from unpod.management._auth import Auth

logger = get_logger("http")


def unwrap_data(resp: dict | list) -> dict | list:
    """Return the common API payload body, unwrapping the response envelope.

    Two envelope shapes occur across the planes:
    - the supervoice ``/v1`` plane returns a bare ``{"data": ...}``; and
    - the backend-core ``/api/v2/platform`` plane wraps every response through
      ``UnpodJSONRenderer`` as ``{"status_code", "message", "data"}``.

    Unwrap when ``data`` is present AND the payload is one of those envelopes
    (sole key, or accompanied by the renderer's ``status_code``/``message``).
    A genuine body that merely happens to carry a ``data`` field is left intact.
    """
    if (
        isinstance(resp, dict)
        and isinstance(resp.get("data"), (dict, list))
        and (len(resp) == 1 or "status_code" in resp or "message" in resp)
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
            logger.debug(
                "http client created base_url=%s timeout=%s",
                self._base_url,
                self._timeout,
            )
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
            )
        return self._client

    @staticmethod
    def _log_response(resp: Any) -> None:
        """Log one HTTP exchange before ``raise_for_status`` fires.

        Errors log the response body (truncated): the API puts the actual
        reason there, and ``httpx.HTTPStatusError`` alone only says "404 Not
        Found for url …", which is rarely enough to act on. Never raises —
        a logging failure must not mask the real HTTP error.
        """
        try:
            status = getattr(resp, "status_code", 0)
            request = getattr(resp, "request", None)
            method = getattr(request, "method", "?")
            url = getattr(request, "url", "?")
            if not isinstance(status, int):
                return  # a test double, not a real response
            if status < 400:
                logger.debug("http %s %s -> %s", method, url, status)
                return
            body = ""
            try:
                body = (resp.text or "")[:300]
            except Exception:  # noqa: BLE001
                body = "<unreadable body>"
            log = logger.warning if status < 500 else logger.error
            log("http %s %s -> %s %s", method, url, status, body)
        except Exception:  # noqa: BLE001 — never mask the HTTP error
            pass

    @staticmethod
    def _json_or_empty(resp: Any) -> Any:
        """Parsed body, or ``{}`` when there is none.

        A 204 (or any empty 2xx) is a legitimate success — ``attach`` and
        friends answer that way — but ``resp.json()`` raises JSONDecodeError on
        an empty body, which surfaced as a parse error instead of the success
        it actually was.
        """
        if getattr(resp, "status_code", 200) == 204 or not (resp.content or b""):
            return {}
        return resp.json()

    async def get(self, path: str, params: dict[str, str] | None = None) -> dict | list:
        """Send a GET request and return parsed JSON response."""
        client = await self._ensure_client()
        resp = await client.get(path, headers=self._headers(), params=params)
        self._log_response(resp)
        resp.raise_for_status()
        return self._json_or_empty(resp)  # type: ignore[no-any-return]

    async def post(self, path: str, json: dict | None = None) -> dict | list:
        """Send a POST request and return parsed JSON response."""
        client = await self._ensure_client()
        resp = await client.post(path, headers=self._headers(), json=json)
        self._log_response(resp)
        resp.raise_for_status()
        return self._json_or_empty(resp)  # type: ignore[no-any-return]

    async def put(self, path: str, json: dict | None = None) -> dict:
        """Send a PUT request and return parsed JSON response."""
        client = await self._ensure_client()
        resp = await client.put(path, headers=self._headers(), json=json)
        self._log_response(resp)
        resp.raise_for_status()
        return self._json_or_empty(resp)  # type: ignore[no-any-return]

    async def patch(self, path: str, json: dict | None = None) -> dict:
        """Send a PATCH request and return parsed JSON response."""
        client = await self._ensure_client()
        resp = await client.patch(path, headers=self._headers(), json=json)
        self._log_response(resp)
        resp.raise_for_status()
        return self._json_or_empty(resp)  # type: ignore[no-any-return]

    async def delete(self, path: str) -> None:
        """Send a DELETE request (no response body)."""
        client = await self._ensure_client()
        resp = await client.delete(path, headers=self._headers())
        self._log_response(resp)
        resp.raise_for_status()

    async def delete_with_response(self, path: str) -> dict:
        """Send a DELETE request and return parsed JSON response."""
        client = await self._ensure_client()
        resp = await client.delete(path, headers=self._headers())
        self._log_response(resp)
        resp.raise_for_status()
        return self._json_or_empty(resp)  # type: ignore[no-any-return]

    async def close(self) -> None:
        """Close the underlying httpx client."""
        if self._client:
            await self._client.aclose()
            self._client = None
