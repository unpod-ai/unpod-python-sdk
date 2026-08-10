"""Analytics resource: author blocks, attach them to agents, read results.

A block runs automatically on every session its attached agents finish — there
is no "run" call. Attach it, place calls, read the results.

    block = await client.analytics.create(
        name="Lead Qualification",
        prompt="Assess whether the caller is a qualified lead and why.",
        fields=[
            {"name": "budget", "type": "int"},
            {"name": "sentiment", "type": "enum",
             "choices": ["Positive", "Neutral", "Negative"]},
        ],
    )
    await client.analytics.attach(block.block_id, agent_id="support-agent")
    results = await client.analytics.results(block.block_id)
"""

from __future__ import annotations

from typing import Any

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models.analytics import AnalyticsBlock, AnalyticsResult


class AnalyticsResource:
    """Manage analytics blocks and read their results."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def create(
        self,
        name: str,
        prompt: str,
        fields: list[dict[str, Any]] | None = None,
        *,
        summary_description: str | None = None,
        model: str | None = None,
        success_enabled: bool | None = None,
        success_description: str | None = None,
        success_type: str | None = None,
        success_choices: list[str] | None = None,
        condition: dict[str, Any] | None = None,
        from_template: str | None = None,
    ) -> AnalyticsBlock:
        """Create a block.

        ``fields`` entries are ``{name, type, description?, choices?}`` with
        type one of ``str | int | float | bool | list[str] | enum``. A summary
        is extracted for every block whether or not you declare one — pass
        ``summary_description`` to steer what it emphasises. ``model``
        overrides the service's default LLM for this block alone.
        """
        body: dict[str, Any] = {
            "name": name,
            "prompt": prompt,
            "fields": fields or [],
        }
        if summary_description is not None:
            body["summary_description"] = summary_description
        if model is not None:
            body["model"] = model
        for key, value in (
            ("success_enabled", success_enabled),
            ("success_description", success_description),
            ("success_type", success_type),
            ("success_choices", success_choices),
            ("condition", condition),
        ):
            if value is not None:
                body[key] = value
        path = "/speech/v1/analytics-blocks"
        if from_template:
            path += f"?from_template={from_template}"
        return AnalyticsBlock(**unwrap_data(await self._http.post(path, body)))

    async def templates(self) -> list[dict[str, Any]]:
        """Starter blocks you can fork: fetch one, edit it, save it as yours.

        Pass the chosen ``template_id`` to :meth:`create` as ``from_template``;
        anything you also pass overrides the template.
        """
        return list(
            unwrap_data(await self._http.get("/speech/v1/analytics-blocks/templates"))
        )

    async def list(self, *, include_archived: bool = False) -> list[AnalyticsBlock]:
        """List blocks in this project."""
        path = "/speech/v1/analytics-blocks"
        if include_archived:
            path += "?include_archived=true"
        resp = unwrap_data(await self._http.get(path))
        return [AnalyticsBlock(**item) for item in resp]

    async def get(self, block_id: str) -> AnalyticsBlock:
        """Get one block, including the agents it is attached to."""
        resp = unwrap_data(await self._http.get(f"/speech/v1/analytics-blocks/{block_id}"))
        return AnalyticsBlock(**resp)

    async def update(
        self,
        block_id: str,
        *,
        name: str | None = None,
        prompt: str | None = None,
        fields: list[dict[str, Any]] | None = None,
        summary_description: str | None = None,
        model: str | None = None,
        success_enabled: bool | None = None,
        success_description: str | None = None,
        success_type: str | None = None,
        success_choices: list[str] | None = None,
        condition: dict[str, Any] | None = None,
    ) -> AnalyticsBlock:
        """Update a block. Bumps its version; past results keep the old one."""
        body: dict[str, Any] = {}
        for key, value in (
            ("name", name),
            ("prompt", prompt),
            ("fields", fields),
            ("summary_description", summary_description),
            ("model", model),
            ("success_enabled", success_enabled),
            ("success_description", success_description),
            ("success_type", success_type),
            ("success_choices", success_choices),
            ("condition", condition),
        ):
            if value is not None:
                body[key] = value
        resp = unwrap_data(
            await self._http.patch(f"/speech/v1/analytics-blocks/{block_id}", body)
        )
        return AnalyticsBlock(**resp)

    async def delete(self, block_id: str) -> None:
        """Archive a block: it stops running, its results stay readable."""
        await self._http.delete(f"/speech/v1/analytics-blocks/{block_id}")

    async def attach(self, block_id: str, agent_id: str) -> None:
        """Attach a block to an agent. Idempotent; one block serves many agents."""
        await self._http.post(
            f"/speech/v1/analytics-blocks/{block_id}/attach", {"agent_id": agent_id}
        )

    async def detach(self, block_id: str, agent_id: str) -> None:
        """Stop a block running for one agent."""
        await self._http.delete(
            f"/speech/v1/analytics-blocks/{block_id}/attach/{agent_id}"
        )

    async def results(
        self, block_id: str, *, limit: int = 100, skip: int = 0
    ) -> list[AnalyticsResult]:
        """Every run of one block, newest first — the report for this analytics id."""
        resp = unwrap_data(
            await self._http.get(
                f"/speech/v1/analytics-blocks/{block_id}/results?limit={limit}&skip={skip}"
            )
        )
        return [AnalyticsResult(**item) for item in resp]

    async def list_results(
        self,
        *,
        agent_id: str | None = None,
        block_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
        skip: int = 0,
    ) -> list[AnalyticsResult]:
        """Results across blocks, optionally filtered by agent, block, status."""
        params = [f"limit={limit}", f"skip={skip}"]
        for key, value in (
            ("agent_id", agent_id),
            ("block_id", block_id),
            ("status", status),
        ):
            if value:
                params.append(f"{key}={value}")
        resp = unwrap_data(
            await self._http.get("/speech/v1/analytics-results?" + "&".join(params))
        )
        return [AnalyticsResult(**item) for item in resp]

    async def results_table(
        self, block_id: str, *, limit: int = 100, skip: int = 0
    ) -> dict[str, Any]:
        """One block's results as ``{columns, rows}`` for a table UI.

        Columns come from the block's field spec, so a field every row left
        null still gets a column and types are never inferred from values.
        """
        return dict(
            unwrap_data(
                await self._http.get(
                    "/speech/v1/analytics-results"
                    f"?block_id={block_id}&format=flat&limit={limit}&skip={skip}"
                )
            )
        )

    async def for_session(self, session_id: str) -> list[AnalyticsResult]:
        """Every block's result for one session."""
        resp = unwrap_data(
            await self._http.get(f"/speech/v1/sessions/{session_id}/analytics")
        )
        return [AnalyticsResult(**item) for item in resp]

    async def for_call(self, call_id: str) -> list[AnalyticsResult]:
        """Every block's result for one call."""
        resp = unwrap_data(
            await self._http.get(f"/speech/v1/calls/{call_id}/analytics")
        )
        return [AnalyticsResult(**item) for item in resp]
