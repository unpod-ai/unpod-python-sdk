"""Single-URL configuration.

Set ``UNPOD_BASE_URL`` once (bare host or any URL form) and every endpoint
is derived from it in code:

- Management REST   -> ``https://<host>/platform``
- Orchestrator WSS  -> ``wss://<host>``
- Speech service    -> ``wss://<host>``
- Auth plane        -> ``https://<host>``

Per-component overrides (``UNPOD_SERVICE_BASE_URL``,
``UNPOD_ORCHESTRATOR_URL``, ``SUPERVOICE_URL``) still win when set.
"""

from __future__ import annotations

import os

_SCHEMES = ("https://", "http://", "wss://", "ws://")


def _raw() -> str | None:
    value = os.environ.get("UNPOD_BASE_URL", "").strip().rstrip("/")
    return value or None


def host() -> str | None:
    """Bare ``host[:port]`` from ``UNPOD_BASE_URL``, scheme stripped."""
    raw = _raw()
    if raw is None:
        return None
    for scheme in _SCHEMES:
        if raw.startswith(scheme):
            return raw[len(scheme) :]
    return raw


def _secure() -> bool:
    raw = _raw() or ""
    return not raw.startswith(("http://", "ws://"))


def http_base() -> str | None:
    """``https://<host>`` (http for insecure bases); ``None`` when unset."""
    h = host()
    if h is None:
        return None
    return f"{'https' if _secure() else 'http'}://{h}"


def ws_base() -> str | None:
    """``wss://<host>`` (ws for insecure bases); ``None`` when unset."""
    h = host()
    if h is None:
        return None
    return f"{'wss' if _secure() else 'ws'}://{h}"


def service_base() -> str | None:
    """Management REST base ``<http_base>/platform``; ``None`` when unset."""
    base = http_base()
    return f"{base}/platform" if base else None


def platform_base() -> str | None:
    """backend-core platform REST base ``<http_base>/api/v2/platform``.

    The home of the telephony plane (``/telephony/*``) — a different service from
    the supervoice management plane at ``/platform``. ``None`` when unset.
    """
    base = http_base()
    return f"{base}/api/v2/platform" if base else None


def auth_base() -> str | None:
    """Auth/identity plane base ``https://<host>`` (bare host, no path).

    Callers append the auth path themselves, e.g. ``f"{auth_base()}/auth/me/"``
    or ``f"{auth_base()}/api/v1/auth/login/"``. ``None`` when unset.
    """
    return http_base()
