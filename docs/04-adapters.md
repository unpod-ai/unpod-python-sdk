# Adapters — DialogAdapter Protocol & Bundled Adapters

Adapters bridge the `Session.dialog_machine` slot to any conversation engine. The SDK ships with four adapters and supports custom implementations via the `DialogAdapter` protocol.

## DialogAdapter Protocol

The contract every adapter must satisfy:

```python
from typing import AsyncIterator, Protocol, runtime_checkable

@runtime_checkable
class DialogAdapter(Protocol):
    async def turn(self, text: str, context: dict | None = None) -> str:
        """Process user text and return agent response.

        Args:
            text: User's spoken text (from STT).
            context: Optional per-turn context (session.data, call metadata).

        Returns:
            Agent's response text (sent to TTS).
        """
        ...

    async def stream(self, text: str, context: dict | None = None) -> AsyncIterator[str]:
        """Process user text and stream agent response tokens.

        Args:
            text: User's spoken text.
            context: Optional per-turn context.

        Yields:
            Text chunks as they become available.
        """
        ...

    def assist(self, text: str) -> None:
        """Inject a system instruction for the next turn.

        Used by session controls like:
            session.dialog_machine.assist("User is upset")
        """
        ...
```

### Optional Methods

Adapters may also implement these methods. Session detects and calls them when available:

```python
def set_llm(self, uri: str) -> None:
    """Hot-swap LLM model. Only applicable to LLM-backed adapters."""
    ...

def switch_flow(self, flow, preserve_memory: bool = False) -> None:
    """Switch conversation flow. Only applicable to superdialog adapter."""
    ...

@property
def is_complete(self) -> bool:
    """Whether the conversation has reached a terminal state."""
    ...

@property
def state(self) -> dict:
    """Current state snapshot (node_id, slots, etc.)."""
    ...
```

## Auto-Wrapping

When you assign a known type to `session.dialog_machine`, the SDK auto-wraps it:

```python
from superdialog import DialogMachine

dm = DialogMachine(flow=flow, llm="anthropic/claude-haiku-4-5")

# Auto-detected and wrapped in SuperDialogAdapter
session.dialog_machine = dm

# Equivalent to:
from unpod.adapters import SuperDialogAdapter
session.dialog_machine = SuperDialogAdapter(dm)
```

**Auto-wrap rules:**

| Assigned Type | Adapter Used |
|---------------|-------------|
| `superdialog.DialogMachine` | `SuperDialogAdapter` |
| `superdialog.LLMAgent` | `SuperDialogAdapter` |
| Any `DialogAdapter` instance | Used directly |
| Anything else | `TypeError` |

---

## Bundled Adapters

### SuperDialogAdapter

Wraps `superdialog.DialogMachine` or `superdialog.LLMAgent`.

**Install:** `pip install unpod[dialog]`

```python
from superdialog import DialogMachine, create_dialog_flow, PythonTool
from unpod.adapters import SuperDialogAdapter

flow = create_dialog_flow(
    prompt="Verify customer KYC. Ask for Aadhaar last 4 digits.",
    llm="openai/gpt-4.1-mini",
)

def lookup_customer(aadhaar_last_4: str) -> dict:
    return crm.lookup(aadhaar_last_4)

dm = DialogMachine(
    flow=flow,
    llm="anthropic/claude-haiku-4-5",
    tools=[PythonTool(fn=lookup_customer)],
)

# Explicit wrapping
adapter = SuperDialogAdapter(dm)
session.dialog_machine = adapter

# Or auto-wrap
session.dialog_machine = dm
```

**Supported methods:**

| Method | Maps to |
|--------|---------|
| `turn(text)` | `dm.turn(text)` |
| `stream(text)` | `dm.turn(text, stream="text")` |
| `assist(text)` | `dm.assist(text)` |
| `set_llm(uri)` | `dm.set_llm(uri)` |
| `switch_flow(flow)` | `dm.switch_flow(flow)` |
| `is_complete` | `dm.is_complete` |
| `state` | `dm.state` |

---

### LangChainAdapter

Wraps any LangChain `Runnable` (chain, agent, graph).

**Install:** `pip install unpod[langchain]`

```python
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from unpod.adapters import LangChainAdapter

prompt = ChatPromptTemplate.from_messages([
    ("system", "You are a KYC verification assistant."),
    ("placeholder", "{messages}"),
])
llm = ChatOpenAI(model="gpt-4.1-mini")
chain = prompt | llm

adapter = LangChainAdapter(chain)
session.dialog_machine = adapter
```

**How it works:**

- Maintains chat history internally (list of `HumanMessage` / `AIMessage`)
- `turn(text)` appends `HumanMessage`, invokes chain, returns `AIMessage.content`
- `stream(text)` uses `chain.astream()` for token-level streaming
- `assist(text)` appends `SystemMessage` to history

---

### HTTPAdapter

Wraps an external HTTP endpoint as a brain.

**Install:** included in base `unpod` package

```python
from unpod.adapters import HTTPAdapter

adapter = HTTPAdapter(
    url="https://my-brain.io/turn",
    headers={"Authorization": "Bearer sk_..."},
    timeout_s=10,
)
session.dialog_machine = adapter
```

**HTTP contract:**

Request (POST):
```json
{
    "text": "I need to verify my KYC",
    "context": {"customer_id": "C123"},
    "session_id": "sess_abc"
}
```

Response:
```json
{
    "text": "Sure, what are the last 4 digits of your Aadhaar?",
    "metadata": {}
}
```

**Features:**
- Automatic retries with exponential backoff
- Configurable timeout
- `assist(text)` sends as `{"system_message": text}` on next request

---

### MCPAdapter

Wraps a Model Context Protocol server as a brain.

**Install:** `pip install unpod[mcp]`

```python
from unpod.adapters import MCPAdapter

adapter = MCPAdapter(
    server_url="https://my-internal.io/mcp",
    tools=["lookup_customer", "verify_kyc", "schedule_followup"],
    llm="anthropic/claude-haiku-4-5",  # LLM for orchestrating tool calls
)
session.dialog_machine = adapter
```

**How it works:**

- Connects to MCP server, discovers available tools
- Uses specified LLM to orchestrate conversation + tool calls
- Tool calls flow through the MCP server transparently
- `assist(text)` injects system instruction for next LLM call

---

## Custom Adapters

Implement the `DialogAdapter` protocol for custom brains:

```python
from unpod.adapters import DialogAdapter

class MyCustomAdapter:
    """Wraps my proprietary conversation engine."""

    def __init__(self, engine):
        self._engine = engine
        self._pending_instructions: list[str] = []

    async def turn(self, text: str, context: dict | None = None) -> str:
        # Inject any pending system instructions
        for instruction in self._pending_instructions:
            self._engine.inject(instruction)
        self._pending_instructions.clear()

        # Process turn
        response = await self._engine.process(text, metadata=context)
        return response.text

    async def stream(self, text: str, context: dict | None = None):
        # Fall back to non-streaming if engine doesn't support it
        result = await self.turn(text, context)
        yield result

    def assist(self, text: str) -> None:
        self._pending_instructions.append(text)

# Usage
session.dialog_machine = MyCustomAdapter(my_engine)
```

The protocol is `@runtime_checkable`, so `isinstance(obj, DialogAdapter)` works for type checking.
