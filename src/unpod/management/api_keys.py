"""API keys resource."""

from __future__ import annotations

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models import ApiKey


class ApiKeysResource:
    """Create and manage platform API keys.

    API keys are scoped to an organisation and optionally to a project
    within that organisation. When ``project_id`` is omitted, the platform
    defaults it to ``org_id``.

    The ``raw_key`` field in the returned :class:`~unpod.models.ApiKey` is
    the plaintext secret and is shown **once only** at creation time.
    """

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def create(
        self,
        name: str,
        org_id: str,
        project_id: str | None = None,
    ) -> ApiKey:
        """Create a new API key.

        Args:
            name: Human-readable label for the key.
            org_id: Organisation the key is scoped to.
            project_id: Optional project within the organisation; defaults to
                ``org_id`` server-side when omitted.

        Returns:
            The created :class:`~unpod.models.ApiKey` including ``raw_key``.
        """
        body: dict[str, str] = {"name": name, "org_id": org_id}
        if project_id is not None:
            body["project_id"] = project_id
        resp = await self._http.post("/v1/api-keys", json=body)
        return ApiKey(**unwrap_data(resp))
