"""``client.tools`` — discover tools, author your own, connect them to agents.

Two kinds, one id-based verb for connecting either:

* BUILT-INS the platform implements (``handover_tool`` and friends).
* CUSTOM HTTP tools this project authors. Stored once and attached BY ID, so an
  edit to a tool's URL or headers reaches every agent using it — the same
  author-once/attach-many shape as domain dictionaries. Embedding the definition
  on each agent instead means a changed endpoint has to be rewritten on all of
  them, and the ones that get missed keep calling the old URL in silence.
"""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import quote

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models.tool import BuiltinTool, CustomTool, CustomToolSupport, ToolCatalog


def _seg(value: str) -> str:
    return quote(str(value), safe="")


class ToolsResource:
    """List, author, attach."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(self) -> ToolCatalog:
        """Every built-in, plus what a tool of your own may look like."""
        data = unwrap_data(await self._http.get("/tools"))
        return ToolCatalog(
            builtin=[BuiltinTool(**t) for t in data.get("builtin", [])],
            custom=CustomToolSupport(**data.get("custom", {})),
        )

    async def list_custom(self) -> list[CustomTool]:
        """The tools this project has authored."""
        data = unwrap_data(await self._http.get("/custom-tools"))
        return [CustomTool(**t) for t in data]

    async def create(
        self,
        tool_id: str,
        *,
        url: str,
        description: str = "",
        method: str = "GET",
        headers: Mapping[str, str] | None = None,
        body: Mapping[str, Any] | None = None,
        args: Mapping[str, Any] | None = None,
        timeout: float | None = None,
    ) -> CustomTool:
        """Create or replace one tool. ``tool_id`` is the name the MODEL calls.

        Templating inside ``url``, ``headers`` and ``body``:

        * ``{{ args.NAME }}`` — an argument the model supplied
        * ``{{ env.NAME }}`` — an environment variable ON THE WORKER

        ``env`` deliberately does not read stored config: a credential held in
        the tool row would sit in the database in plaintext and travel into a
        worker process shared with other tenants. An operator sets the variable.

        ``args`` is ``{name: {"type": ..., "description": ..., "required": ...}}``
        and becomes the schema the model is offered — the description is the
        only thing it has to decide when to call the tool, so write it for a
        reader who cannot see your API.

        Rejected on write, not ignored at call time: an unknown arg type, a
        private/reserved URL, or an id that shadows a built-in raises here.
        """
        payload: dict[str, Any] = {
            "url": url,
            "description": description,
            "method": method,
            "headers": dict(headers or {}),
            "body": dict(body or {}),
            "args": dict(args or {}),
        }
        if timeout is not None:
            payload["timeout"] = timeout
        return CustomTool(
            **unwrap_data(await self._http.put(f"/custom-tools/{_seg(tool_id)}", json=payload))
        )

    async def delete(self, tool_id: str) -> None:
        """Delete a tool and detach it from every agent.

        Both, because an id left attached to a tool that no longer exists makes
        those agents resolve nothing — which looks exactly like the model
        choosing not to call it.
        """
        await self._http.delete(f"/custom-tools/{_seg(tool_id)}")

    async def attach(self, tool_id: str, agent_id: str) -> dict[str, Any]:
        """Connect a tool to an agent, by id — built-in or your own.

        One verb for both kinds, because "give this agent that tool" is one
        intention from the caller's side. A built-in is enabled through its
        config key; a tool of your own is referenced by id, so editing the
        definition later reaches this agent without re-attaching.

        A built-in that is always on (``end_call``) reports itself attached and
        changes nothing — there is nothing to enable.
        """
        return unwrap_data(
            await self._http.post(
                f"/tools/{_seg(tool_id)}/attach", json={"agent_id": agent_id}
            )
        )

    async def detach(self, tool_id: str, agent_id: str) -> dict[str, Any]:
        """Disconnect a tool from an agent. Always-on built-ins refuse (409)."""
        return unwrap_data(
            await self._http.post(
                f"/tools/{_seg(tool_id)}/detach", json={"agent_id": agent_id}
            )
        )


__all__ = ["ToolsResource"]
