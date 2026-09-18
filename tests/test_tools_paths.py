"""Every ``client.tools`` call names a path supervoice actually serves.

The resource rides ``_http`` (the supervoice management base), so its paths
are spelled RELATIVE to that base and must carry the ``/v1`` prefix supervoice
mounts its platform routes under. Spelled without it, ``/tools`` resolved to
``<base>/tools`` — which nothing serves — and every tool verb 404'd while the
same call through a ``/v1`` path returned 200.

The expected paths below are supervoice's own route table
(``GET /v1/tools``, ``GET,PUT,DELETE /v1/custom-tools[/{id}]``,
``POST /v1/tools/{id}/(at|de)tach``), so a drift on either side fails here.
"""

from __future__ import annotations

from typing import Any

import pytest

from unpod.management.tools import ToolsResource


class _FakeHTTP:
    """Records the path of the last request, whatever the verb."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self.path: str | None = None
        self.json: dict | None = None

    async def get(self, path: str) -> Any:
        self.path = path
        return self._response

    async def put(self, path: str, json: dict | None = None) -> Any:
        self.path = path
        self.json = json
        return self._response

    async def delete(self, path: str) -> Any:
        self.path = path
        return self._response

    async def post(self, path: str, json: dict | None = None) -> Any:
        self.path = path
        self.json = json
        return self._response


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_list_reads_the_v1_catalog() -> None:
    fake = _FakeHTTP({"data": {"builtin": [], "custom": {}}})
    await ToolsResource(http=fake).list()  # type: ignore[arg-type]
    assert fake.path == "/v1/tools"


@pytest.mark.anyio
async def test_list_custom_reads_the_v1_collection() -> None:
    fake = _FakeHTTP({"data": []})
    await ToolsResource(http=fake).list_custom()  # type: ignore[arg-type]
    assert fake.path == "/v1/custom-tools"


@pytest.mark.anyio
async def test_create_writes_to_the_v1_item() -> None:
    fake = _FakeHTTP({"data": {"tool_id": "t1", "url": "https://x"}})
    await ToolsResource(http=fake).create("t1", url="https://x")  # type: ignore[arg-type]
    assert fake.path == "/v1/custom-tools/t1"


@pytest.mark.anyio
async def test_delete_targets_the_v1_item() -> None:
    fake = _FakeHTTP(None)
    await ToolsResource(http=fake).delete("t1")  # type: ignore[arg-type]
    assert fake.path == "/v1/custom-tools/t1"


@pytest.mark.anyio
async def test_attach_and_detach_target_the_v1_verbs() -> None:
    fake = _FakeHTTP({"data": {"attached": True}})
    res = ToolsResource(http=fake)  # type: ignore[arg-type]

    await res.attach("t1", "agent-1")
    assert fake.path == "/v1/tools/t1/attach"
    assert fake.json == {"agent_id": "agent-1"}

    await res.detach("t1", "agent-1")
    assert fake.path == "/v1/tools/t1/detach"


@pytest.mark.anyio
async def test_the_tool_id_is_escaped_into_one_segment() -> None:
    """A slash in the id must not invent a path segment."""
    fake = _FakeHTTP(None)
    await ToolsResource(http=fake).delete("a/b")  # type: ignore[arg-type]
    assert fake.path == "/v1/custom-tools/a%2Fb"
