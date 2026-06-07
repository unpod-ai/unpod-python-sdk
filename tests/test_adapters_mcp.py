# tests/test_adapters_mcp.py


def test_mcp_adapter_init():
    from unpod.adapters.mcp import MCPAdapter

    adapter = MCPAdapter(
        server_url="https://internal.io/mcp",
        tools=["lookup_customer"],
        llm="anthropic/claude-haiku-4-5",
    )
    assert adapter._server_url == "https://internal.io/mcp"
    assert adapter._tools == ["lookup_customer"]
    assert adapter._llm == "anthropic/claude-haiku-4-5"


def test_mcp_adapter_assist():
    from unpod.adapters.mcp import MCPAdapter

    adapter = MCPAdapter(
        server_url="https://internal.io/mcp",
        tools=["lookup_customer"],
        llm="anthropic/claude-haiku-4-5",
    )
    adapter.assist("be concise")
    assert adapter._pending_instructions == ["be concise"]


def test_mcp_adapter_multiple_assist():
    from unpod.adapters.mcp import MCPAdapter

    adapter = MCPAdapter(
        server_url="https://internal.io/mcp",
        tools=[],
        llm="anthropic/claude-haiku-4-5",
    )
    adapter.assist("be concise")
    adapter.assist("speak Hindi")
    assert len(adapter._pending_instructions) == 2


def test_mcp_adapter_defaults():
    from unpod.adapters.mcp import MCPAdapter

    adapter = MCPAdapter(server_url="https://example.com/mcp")
    assert adapter._tools == []
    assert adapter._llm == "anthropic/claude-haiku-4-5"
    assert adapter._headers == {}
    assert adapter._pending_instructions == []
    assert adapter._history == []


def test_mcp_adapter_exported_from_package():
    from unpod.adapters import MCPAdapter

    assert MCPAdapter is not None
