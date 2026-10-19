"""``client.assistants``: build, deploy and chat with assistants.

Same transport as ``client.flows``: the platform's journey proxy, org-scoped by
the caller's token. A chat turn runs the model (and maybe several tool steps)
before it answers, so ``send``/``decide`` wait up to ``TURN_TIMEOUT`` and always
carry an ``Idempotency-Key``: retrying the same call returns the first reply
instead of running the turn twice.
"""

from __future__ import annotations

import builtins
import uuid
from collections.abc import Awaitable
from typing import Any, TypeVar

import httpx

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.management.flows import JOURNEY_BASE, _problems
from unpod.models.assistants import (
    Approval,
    Assistant,
    AssistantVersion,
    EndUser,
    MemoryBlock,
    Message,
    Reply,
    Thread,
)

_T = TypeVar("_T")

# How long the platform proxy waits for a turn. The SDK waits a little longer
# so a slow turn surfaces as the proxy's clean 504, not a bare client timeout.
PROXY_TURN_TIMEOUT = 150.0
TURN_TIMEOUT = 165.0
# A turn that timed out or got 503/504 is retried with the SAME
# Idempotency-Key: the service answers the retry with the first turn's reply
# (waiting for it if still running) instead of running a second turn.
_TURN_ATTEMPTS = 3
_RETRY_STATUS = (503, 504)


class AssistantValidationError(Exception):
    """The service refused the request (409 state conflict or 422 invalid)."""

    def __init__(self, status_code: int, problems: builtins.list[str]):
        self.status_code = status_code
        self.problems = problems
        super().__init__(f"{status_code}: {'; '.join(problems) or 'refused'}")


async def _refused(call: Awaitable[_T]) -> _T:
    try:
        return await call
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code not in (409, 422):
            raise
        try:
            body = exc.response.json()
        except ValueError:
            body = exc.response.text
        detail = (
            body.get("detail", body.get("message")) if isinstance(body, dict) else body
        )
        raise AssistantValidationError(
            exc.response.status_code, _problems(detail)
        ) from exc


class AssistantsResource:
    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    def _a(self, space_id: str) -> str:
        return f"{JOURNEY_BASE}/spaces/{space_id}/assistants"

    # ------------------------------------------------------------- build

    async def validate(self, space_id: str, definition: dict[str, Any]) -> bool:
        """Dry-run: True if the definition is valid; raises with the problems."""
        await _refused(
            self._http.post(f"{self._a(space_id)}/validate", json=definition)
        )
        return True

    async def create(self, space_id: str, definition: dict[str, Any]) -> Assistant:
        """Save a new assistant as a draft (not live until ``deploy``)."""
        resp = await _refused(self._http.post(self._a(space_id), json=definition))
        return Assistant(**unwrap_data(resp))

    async def list(self, space_id: str) -> builtins.list[Assistant]:
        resp = unwrap_data(await self._http.get(self._a(space_id)))
        return [Assistant(**a) for a in resp]

    async def get(self, space_id: str, assistant_id: str) -> Assistant:
        return Assistant(
            **unwrap_data(await self._http.get(f"{self._a(space_id)}/{assistant_id}"))
        )

    async def update(
        self, space_id: str, assistant_id: str, definition: dict[str, Any]
    ) -> Assistant:
        """Replace the draft. Running threads keep the version they started on."""
        resp = await _refused(
            self._http.put(f"{self._a(space_id)}/{assistant_id}", json=definition)
        )
        return Assistant(**unwrap_data(resp))

    async def deploy(self, space_id: str, assistant_id: str) -> AssistantVersion:
        """Freeze the draft as the next version and turn the assistant on."""
        resp = await _refused(
            self._http.post(f"{self._a(space_id)}/{assistant_id}/deploy")
        )
        return AssistantVersion(**unwrap_data(resp))

    async def off(self, space_id: str, assistant_id: str) -> Assistant:
        resp = await _refused(
            self._http.post(f"{self._a(space_id)}/{assistant_id}/off")
        )
        return Assistant(**unwrap_data(resp))

    async def versions(
        self, space_id: str, assistant_id: str
    ) -> builtins.list[AssistantVersion]:
        resp = unwrap_data(
            await self._http.get(f"{self._a(space_id)}/{assistant_id}/versions")
        )
        return [AssistantVersion(**v) for v in resp]

    # -------------------------------------------------------------- chat

    async def end_user(
        self, space_id: str, assistant_id: str, external_id: str, name: str = ""
    ) -> EndUser:
        """Get or create the end user for your own user id ``external_id``."""
        resp = await self._http.post(
            f"{self._a(space_id)}/{assistant_id}/end_users",
            json={"external_id": external_id, "name": name},
        )
        return EndUser(**unwrap_data(resp))

    async def open_thread(
        self, space_id: str, assistant_id: str, end_user_id: str
    ) -> Thread:
        """A new conversation, pinned to the live version (409 if not on)."""
        resp = await _refused(
            self._http.post(
                f"{self._a(space_id)}/{assistant_id}/end_users/{end_user_id}/threads"
            )
        )
        return Thread(**unwrap_data(resp))

    async def _turn(self, path: str, body: dict[str, Any], key: str) -> Reply:
        for attempt in range(_TURN_ATTEMPTS):
            try:
                resp = await _refused(
                    self._http.post(
                        path,
                        json=body,
                        headers={"Idempotency-Key": key},
                        timeout=TURN_TIMEOUT,
                    )
                )
                return Reply(**unwrap_data(resp))
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                retryable = isinstance(exc, httpx.TimeoutException) or (
                    exc.response.status_code in _RETRY_STATUS
                )
                if not retryable or attempt == _TURN_ATTEMPTS - 1:
                    raise
        raise AssertionError("unreachable")

    async def send(
        self,
        space_id: str,
        assistant_id: str,
        thread_id: str,
        text: str,
        idempotency_key: str | None = None,
    ) -> Reply:
        """Send a message; the reply (or a pending approval) comes back.

        Pass your own ``idempotency_key`` if you may call ``send`` again for the
        same message later (e.g. after a crash); within this call, timeouts and
        503/504 are retried automatically with the same key.
        """
        return await self._turn(
            f"{self._a(space_id)}/{assistant_id}/threads/{thread_id}/messages",
            {"text": text},
            idempotency_key or uuid.uuid4().hex,
        )

    async def decide(
        self,
        space_id: str,
        assistant_id: str,
        thread_id: str,
        approval_id: str,
        approve: bool,
    ) -> Reply:
        """Approve or deny the tool call a turn paused on; returns the resumed reply."""
        decision = "approve" if approve else "deny"
        return await self._turn(
            f"{self._a(space_id)}/{assistant_id}/threads/{thread_id}/approvals/{approval_id}",
            {"decision": decision},
            f"{approval_id}:{decision}",
        )

    async def messages(
        self, space_id: str, assistant_id: str, thread_id: str
    ) -> builtins.list[Message]:
        resp = unwrap_data(
            await self._http.get(
                f"{self._a(space_id)}/{assistant_id}/threads/{thread_id}/messages"
            )
        )
        return [Message(**m) for m in resp]

    async def approvals(
        self, space_id: str, assistant_id: str, thread_id: str
    ) -> builtins.list[Approval]:
        """Pending approvals of a thread (at most one in practice), newest first."""
        resp = unwrap_data(
            await self._http.get(
                f"{self._a(space_id)}/{assistant_id}/threads/{thread_id}/approvals",
                params={"status": "pending"},
            )
        )
        return [Approval(**a) for a in resp]

    async def memory(
        self, space_id: str, assistant_id: str, end_user_id: str
    ) -> builtins.list[MemoryBlock]:
        """What the assistant remembers about this end user."""
        resp = unwrap_data(
            await self._http.get(
                f"{self._a(space_id)}/{assistant_id}/end_users/{end_user_id}/memory"
            )
        )
        return [MemoryBlock(**b) for b in resp]
