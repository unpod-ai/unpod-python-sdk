"""MCP (Model Context Protocol) adapter for the Unpod SDK."""

from __future__ import annotations

from typing import Any, AsyncIterator


class MCPAdapter:
    """Wraps a Model Context Protocol server as a brain.

    Connects to an MCP server, discovers available tools,
    and uses a specified LLM to orchestrate conversation + tool calls.
    """

    def __init__(
        self,
        server_url: str,
        tools: list[str] | None = None,
        llm: str = "anthropic/claude-haiku-4-5",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._server_url = server_url
        self._tools = tools or []
        self._llm = llm
        self._headers = headers or {}
        self._pending_instructions: list[str] = []
        self._history: list[dict[str, str]] = []

    async def turn(self, text: str, context: dict[str, Any] | None = None) -> str:
        """Process user text via MCP server tools + LLM orchestration.

        Full implementation requires mcp package -- this is the interface
        that will be wired when mcp is installed.
        """
        try:
            from mcp import ClientSession  # noqa: F401
        except ImportError:
            raise ImportError(
                "MCPAdapter requires the mcp package. "
                "Install with: pip install unpod[mcp]"
            )

        # Placeholder: actual MCP tool call orchestration
        # 1. Connect to MCP server
        # 2. Discover/filter tools
        # 3. Build LLM messages with tool definitions
        # 4. Call LLM, handle tool calls
        # 5. Return final response
        raise NotImplementedError("MCPAdapter.turn() requires full MCP integration")

    async def stream(
        self,
        text: str,
        context: dict[str, Any] | None = None,
        language: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream response tokens."""
        result = await self.turn(text, context)
        yield result

    def assist(self, text: str) -> None:
        """Inject system instruction for next turn."""
        self._pending_instructions.append(text)
