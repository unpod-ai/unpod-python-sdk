"""Domain dictionaries resource: the words an agent hears and says.

A dictionary is authored ONCE per domain and reused by every agent tagged with
that domain. Two steps, and both are needed:

    await client.domain_dictionaries.upsert(
        "gamestop", vocabulary={"PowerUp Rewards": "power up rewords"}
    )
    await client.agents.voice.create("support", brain=Prompt(...), domain="gamestop")

The tag on the agent is what the runtime resolves — nothing looks a dictionary
up by name at call time. A dictionary with no agent tagged changes nothing, and
an agent tagged with a domain that has neither a bundled seed nor tenant rows
gets no keyterms.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence
from urllib.parse import quote

from unpod.management._http import AsyncHTTPClient, unwrap_data
from unpod.models.domain_dictionary import DomainDictionary, DomainListItem, KVItem

_BASE = "/api/v2/platform/speech/v1/domain-dictionaries"

#: Sections accept a mapping, a list of rows, or a list of ``KVItem``.
Rows = Mapping[str, str] | Sequence[Mapping[str, Any]] | Sequence[KVItem] | None


def _seg(domain: str) -> str:
    """Percent-encode a domain as exactly ONE path segment.

    Domain names are free text and are stored verbatim, so a value containing
    ``/`` or ``..`` would otherwise re-point the request at a different route
    while still carrying the caller's credentials.
    """
    return quote(str(domain), safe="")


def _rows(value: Rows) -> list[dict[str, str]]:
    """Normalise one section to the wire shape ``[{"key":…, "value":…}]``.

    A mapping is the ergonomic spelling (``{"IRDAI": "irda i"}``) and the list
    of rows is the wire spelling; both are accepted because the natural way to
    write a dictionary in Python is a dict, and the natural way to round-trip a
    fetched one is the list you got back.
    """
    if value is None:
        return []
    if isinstance(value, Mapping):
        return [{"key": str(k), "value": str(v or "")} for k, v in value.items()]
    out: list[dict[str, str]] = []
    for row in value:
        if isinstance(row, KVItem):
            out.append({"key": row.key, "value": row.value})
        else:
            out.append({"key": str(row["key"]), "value": str(row.get("value") or "")})
    return out


class DomainDictionariesResource:
    """Author and attach the per-domain STT/TTS dictionaries."""

    def __init__(self, http: AsyncHTTPClient) -> None:
        self._http = http

    async def list(self) -> list[DomainListItem]:
        """Every domain: bundled seeds plus any this tenant created."""
        resp = unwrap_data(await self._http.get(_BASE))
        items = resp["domains"] if isinstance(resp, dict) else resp
        return [DomainListItem(**item) for item in items]

    async def get(self, domain: str) -> DomainDictionary:
        """One domain, seed and tenant rows merged — what the runtime will use."""
        return DomainDictionary(
            **unwrap_data(await self._http.get(f"{_BASE}/{_seg(domain)}"))
        )

    async def upsert(
        self,
        domain: str,
        *,
        vocabulary: Rows = None,
        pronunciation: Rows = None,
        fillers: Rows = None,
        settings: dict[str, Any] | None = None,
    ) -> DomainDictionary:
        """REPLACE this tenant's rows for ``domain``.

        Not a merge: whatever you send becomes the whole tenant layer, so read
        with :meth:`get` and send the edited list to add a single term. The
        bundled seed is untouched and keeps applying underneath.

        ``settings`` omitted means "leave the stored filler knobs alone" — a
        client that predates them cannot wipe them by not sending them.
        """
        body: dict[str, Any] = {
            "vocabulary": _rows(vocabulary),
            "pronunciation": _rows(pronunciation),
            "fillers": _rows(fillers),
        }
        if settings is not None:
            body["settings"] = settings
        resp = await self._http.put(f"{_BASE}/{_seg(domain)}", json=body)
        return DomainDictionary(**unwrap_data(resp))

    async def attach(self, domain: str, agent_id: str) -> DomainDictionary:
        """Record ``agent_id`` in this dictionary's reverse index.

        Exclusive: the agent is pulled out of every other domain, because an
        agent has exactly one dictionary. This writes the INDEX only — the tag
        the runtime reads lives on the agent, so pass ``domain=`` to
        ``client.agents.voice.create`` / ``client.agents.update`` to change
        which dictionary an agent actually speaks with.
        """
        resp = await self._http.post(
            f"{_BASE}/{_seg(domain)}/attach", json={"agent_id": agent_id}
        )
        return DomainDictionary(**unwrap_data(resp))

    async def detach(self, domain: str, agent_id: str) -> DomainDictionary:
        """Remove ``agent_id`` from this dictionary's reverse index."""
        resp = await self._http.post(
            f"{_BASE}/{_seg(domain)}/detach", json={"agent_id": agent_id}
        )
        return DomainDictionary(**unwrap_data(resp))

    async def clone(self, source_domain: str, new_domain: str) -> DomainDictionary:
        """Start a custom domain from another domain's seed content.

        409 when ``new_domain`` already holds rows — a clone that silently
        overwrote them would be indistinguishable from one that failed.
        """
        resp = await self._http.post(
            f"{_BASE}/{_seg(source_domain)}/clone", json={"new_domain": new_domain}
        )
        return DomainDictionary(**unwrap_data(resp))

    async def delete(self, domain: str) -> None:
        """Drop this tenant's rows for ``domain``.

        A SEEDED domain is reset, not removed: the bundled seed remains and the
        agents tagged with it keep working. A CUSTOM domain ceases to exist, so
        the agents and voice profiles pointing at it are untagged.
        """
        await self._http.delete(f"{_BASE}/{_seg(domain)}")


__all__ = ["DomainDictionariesResource"]
