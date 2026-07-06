"""Pluggable auth strategies for the management transport.

Two modes reach the same management API:

- **Direct** → supervoice, ``Authorization: Bearer <api_key>``
  (supervoice resolves the tenant from the key).
- **Proxy** → the unpod backend-core proxy, ``Authorization: JWT <token>`` plus
  an ``Org-Handle`` header (DRF auth; the proxy injects the org's supervoice key).

An ``Auth`` produces the per-request auth headers; the transport merges them
with ``Content-Type`` and ``X-Request-Id``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class Auth(Protocol):
    """Produces the auth headers attached to every management request."""

    def headers(self) -> dict[str, str]:
        """Return the auth headers (e.g. ``Authorization``, ``Org-Handle``)."""
        ...


@dataclass(frozen=True)
class BearerAuth:
    """Direct-mode auth: ``Authorization: Bearer <api_key>`` for supervoice."""

    api_key: str

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"}


@dataclass(frozen=True)
class TokenAuth:
    """DRF-token auth: ``Authorization: Token <key>`` (+ optional Org-Handle).

    Used to reach the backend-core native ``/api/v2/platform`` surface with a
    per-user DRF token (``rest_framework.authtoken``) instead of a platform JWT.
    Org-scoped endpoints (telephony) additionally require the ``Org-Handle``
    header.
    """

    token: str
    org_handle: str | None = None

    def headers(self) -> dict[str, str]:
        headers = {"Authorization": f"Token {self.token}"}
        if self.org_handle:
            headers["Org-Handle"] = self.org_handle
        return headers


@dataclass(frozen=True)
class JWTAuth:
    """Proxy-mode auth: ``Authorization: JWT <token>`` (+ optional Org-Handle).

    Used when reaching the management API through the unpod backend-core proxy,
    which authenticates with the platform's user JWT and scopes the request to
    an organization via the ``Org-Handle`` header.
    """

    token: str
    org_handle: str | None = None

    def headers(self) -> dict[str, str]:
        headers = {"Authorization": f"JWT {self.token}"}
        if self.org_handle:
            headers["Org-Handle"] = self.org_handle
        return headers
