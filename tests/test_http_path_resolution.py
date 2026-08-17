"""Host-root paths must not inherit the base URL's path.

Every management resource spells its route as an ABSOLUTE backend-core path
(``/api/v2/platform/speech/v1/agents``). httpx treats a leading slash as
relative to ``base_url``, so a base carrying its own path silently produced
``/platform/api/v2/platform/speech/v1/agents`` -- a route no service mounts,
answered with an HTML 404 that names neither cause nor fix.

``UNPOD_SERVICE_BASE_URL`` is exactly the knob that grows a path: unset, the
client derives ``<host>/platform`` (:func:`unpod._base_url.service_base`), so
the failure appears from a MISSING variable rather than a wrong one.
"""

from __future__ import annotations

import pytest
from unpod.management._auth import BearerAuth
from unpod.management._http import AsyncHTTPClient


def _client(base: str) -> AsyncHTTPClient:
    return AsyncHTTPClient(auth=BearerAuth("unpod_sk_test"), base_url=base)


@pytest.mark.parametrize(
    "base,path,expected",
    [
        # The reported failure: base derived as <host>/platform.
        (
            "http://localhost:8000/platform",
            "/api/v2/platform/speech/v1/agents",
            "http://localhost:8000/api/v2/platform/speech/v1/agents",
        ),
        # Already correct: a bare host is left exactly as it was.
        (
            "http://localhost:8000",
            "/api/v2/platform/speech/v1/agents",
            "http://localhost:8000/api/v2/platform/speech/v1/agents",
        ),
        # The telephony plane's base already IS the prefix its paths repeat.
        (
            "https://api.unpod.ai/api/v2/platform",
            "/api/v2/platform/telephony/numbers/",
            "https://api.unpod.ai/api/v2/platform/telephony/numbers/",
        ),
    ],
)
def test_host_root_paths_ignore_the_base_path(
    base: str, path: str, expected: str
) -> None:
    assert _client(base)._url(path) == expected


@pytest.mark.parametrize(
    "base,path",
    [
        # The orchestrator plane is addressed RELATIVE to its base: sessions
        # live under whatever prefix the base names, so rewriting them would
        # break the very calls the /api/ rule is meant to leave alone.
        ("http://localhost:8000/orchestrator", "/v1/sessions"),
        ("http://localhost:8000/platform", "/v1/agents"),
    ],
)
def test_relative_paths_are_untouched(base: str, path: str) -> None:
    assert _client(base)._url(path) == path


def test_port_and_scheme_survive_the_rewrite() -> None:
    url = _client("https://qa.unpod.tv:8443/platform")._url("/api/v2/platform/speech/v1/calls")
    assert url == "https://qa.unpod.tv:8443/api/v2/platform/speech/v1/calls"
