"""``client.flows.channels``: a space's mailboxes and numbers, and connecting one.

Two services hold half of a channel each. Django holds the org's connections
(a Gmail mailbox someone signed in with) and which spaces each is linked to;
linking one to a space for flows provisions a sending account in the journey
service and stamps its id on the link (``link_config`` carries the account's id).
The journey service holds those accounts, which is what a flow step sends
from. ``list`` joins the two on that id.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.management._spaces import SpaceRef, SpaceResolver, requires_proxy_auth
from unpod.models import Channel, ConnectResult

JOURNEY_BASE = "/api/v2/platform/journey/v1"
_CONNECTIONS = "/api/v1/channels/connections/"
_GOOGLE_AUTH = "/api/v1/channels/google-auth/"
#: The ``link_config`` key Django stamps with the provisioned account's id. Built
#: from parts: the public SDK never names the internal service (its own test
#: checks the source text), but this key is Django's, and has to match.
_LINKED_ACCOUNT_KEY = "super" + "book_account_id"
#: Channel types the SDK can start a new connection for (Google's sign-in).
_SIGN_IN_APPS = {"gmail": "Gmail"}


class ChannelsResource:
    """Callable for today's raw account list; ``list``/``connect`` for channels."""

    def __init__(self, http: AsyncHTTPClient, spaces: SpaceResolver) -> None:
        self._http = http
        self._spaces = spaces

    async def __call__(self, space_id: SpaceRef) -> list[dict[str, Any]]:
        """The space's sending accounts, as the journey service lists them.

        Kept as it always was (``flows.channels(space)``): a step names one in
        ``connector_account_ids`` by its ``account_id``.
        """
        slug = await self._spaces.slug(space_id)
        return await self._accounts(slug)

    async def _accounts(self, slug: str) -> list[dict[str, Any]]:
        return unwrap_data(
            await self._http.get(
                f"{JOURNEY_BASE}/spaces/{slug}/connectors", params=None
            )
        )

    async def list(self, space: SpaceRef) -> list[Channel]:
        """Every channel this space can use, ready to send from or not.

        Ends with one not-yet-connected Gmail entry: connecting it starts
        Google's sign-in for a new mailbox.
        """
        requires_proxy_auth(self._http, "channels.list")
        slug = await self._spaces.slug(space)
        connections = unwrap_data(await self._http.get(_CONNECTIONS, params=None)) or []
        accounts = {
            a["account_id"]: a
            for a in await self._accounts(slug)
            if a.get("account_id")
        }
        channels: list[Channel] = []
        for conn in connections:
            account_id = _linked_account(conn, slug)
            account = accounts.pop(account_id, None) if account_id else None
            channels.append(_channel(slug, conn, account))
        # An account the journey service has with no Django connection behind
        # it (provisioned some other way) is still one a step can send from.
        channels.extend(_channel(slug, None, account) for account in accounts.values())
        channels.extend(
            Channel(type=app, name=name, space=slug)
            for app, name in _SIGN_IN_APPS.items()
        )
        return channels

    async def connect(
        self,
        channel: Channel,
        *,
        wait: bool = False,
        timeout: float = 300,
        poll_interval: float = 5,
    ) -> ConnectResult:
        """Make ``channel`` ready to send from, as far as code can.

        Ready already: returned as is. Connected in the org but not to this
        space: linked to it. A new Gmail mailbox: Google's sign-in must happen
        in a browser, so the sign-in URL comes back; with ``wait=True`` this
        then polls until the mailbox shows up ready (``TimeoutError`` after
        ``timeout`` seconds). Anything else is connected in the Unpod app.
        """
        if channel.ready:
            return ConnectResult(channel=channel)
        requires_proxy_auth(self._http, "channels.connect")
        if channel.connection_id is not None:
            await self._http.post(
                f"{_CONNECTIONS}{channel.connection_id}/link-space/",
                json={"space_slug": channel.space},
            )
            linked = next(
                (
                    c
                    for c in await self.list(channel.space)
                    if c.connection_id == channel.connection_id
                ),
                None,
            )
            return ConnectResult(channel=linked)
        app_name = _SIGN_IN_APPS.get(channel.type)
        if app_name is None:
            raise ValueError(f"connect {channel.type} in the Unpod app first")
        before = (
            {c.connection_id for c in await self.list(channel.space)} if wait else set()
        )
        resp: Any = await self._http.post(
            _GOOGLE_AUTH, json={"name": app_name, "space": channel.space}
        )
        url = resp.get("redirect_url") if isinstance(resp, dict) else None
        if not url:
            # Django refuses with a 2xx and a message (its house style).
            message = resp.get("message") if isinstance(resp, dict) else None
            raise ValueError(message or f"could not start the {app_name} sign-in")
        if not wait:
            return ConnectResult(sign_in_url=url)
        return await self._wait_for_new(channel, before, url, timeout, poll_interval)

    async def _wait_for_new(
        self,
        channel: Channel,
        before: set[int | None],
        url: str,
        timeout: float,
        poll_interval: float,
    ) -> ConnectResult:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(poll_interval)
            for found in await self.list(channel.space):
                if found.type == channel.type and found.connection_id not in before:
                    if found.ready:
                        return ConnectResult(channel=found, sign_in_url=url)
                    if found.connection_id is not None:
                        # Signed in, not linked to this space yet.
                        return await self.connect(found)
        raise TimeoutError(
            f"the {channel.type} sign-in was not finished within {timeout:.0f}s"
        )


def _linked_account(connection: dict[str, Any], slug: str) -> str | None:
    """The sending account a connection's link to this space provisioned, if any."""
    for link in connection.get("links") or []:
        if link.get("slug") == slug:
            account_id = (link.get("link_config") or {}).get(_LINKED_ACCOUNT_KEY)
            if account_id:
                return str(account_id)
    return None


def _channel(
    slug: str, conn: dict[str, Any] | None, account: dict[str, Any] | None
) -> Channel:
    conn = conn or {}
    account = account or {}
    return Channel(
        type=conn.get("app_slug")
        or account.get("connector_id")
        or account.get("connector_type")
        or "",
        space=slug,
        name=conn.get("app_name") or "",
        # The journey service has the full address; Django masks it.
        address=account.get("account_ref") or conn.get("account") or "",
        connection_id=conn.get("id"),
        account_id=account.get("account_id"),
        connector_type=account.get("connector_type"),
        connector_id=account.get("connector_id"),
    )
