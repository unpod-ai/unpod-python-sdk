"""client.domain_dictionaries — authoring the words, and tagging who uses them.

Two objects that are easy to conflate and must not be: the DICTIONARY (rows of
vocabulary/pronunciation/fillers, keyed by domain) and the agent's ``domain``
TAG. The runtime resolves the tag; the dictionary is what the tag names. A
dictionary with nothing tagged changes no call, and a tag naming a domain with
no seed and no rows applies nothing — so both halves are tested here.
"""

from __future__ import annotations

from typing import Any

import pytest

from unpod.management.domain_dictionaries import DomainDictionariesResource
from unpod.models import KVItem

_BASE = "/api/v2/platform/speech/v1/domain-dictionaries"


class _FakeHTTP:
    """Records calls; replays canned responses."""

    def __init__(self, response: Any = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self._response = response if response is not None else {}

    async def get(self, path: str, params: dict | None = None) -> Any:
        self.calls.append(("GET", path, params))
        return self._response

    async def post(self, path: str, json: dict | None = None) -> Any:
        self.calls.append(("POST", path, json))
        return self._response

    async def put(self, path: str, json: dict | None = None) -> Any:
        self.calls.append(("PUT", path, json))
        return self._response

    async def delete(self, path: str) -> None:
        self.calls.append(("DELETE", path, None))


_DOC = {
    "domain": "gamestop",
    "resolved_key": None,
    "seed_vocabulary": [],
    "seed_pronunciation": [],
    "vocabulary": [{"key": "PowerUp Rewards", "value": "power up rewords"}],
    "pronunciation": [{"key": "GameStop", "value": "GAME-stop"}],
    "fillers": [{"key": "en", "value": "One moment…"}],
    "settings": {"enabled": True},
    "agent_ids": ["support"],
}


def _res(response: Any = None) -> tuple[DomainDictionariesResource, _FakeHTTP]:
    http = _FakeHTTP(response)
    return DomainDictionariesResource(http), http  # type: ignore[arg-type]


# --- reads ------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_unwraps_the_domains_envelope() -> None:
    res, http = _res(
        {
            "domains": [
                {"domain": "banking", "seeded": True, "agent_ids": []},
                {"domain": "gamestop", "seeded": False, "agent_ids": ["support"]},
            ]
        }
    )
    items = await res.list()
    assert http.calls == [("GET", _BASE, None)]
    assert [(i.domain, i.seeded) for i in items] == [
        ("banking", True),
        ("gamestop", False),
    ]


@pytest.mark.anyio
async def test_get_returns_the_merged_dictionary() -> None:
    res, http = _res(_DOC)
    doc = await res.get("gamestop")
    assert http.calls == [("GET", f"{_BASE}/gamestop", None)]
    assert doc.vocabulary[0].key == "PowerUp Rewards"
    assert doc.agent_ids == ["support"]


def test_keyterms_include_variants_and_dedupe() -> None:
    from unpod.models import DomainDictionary

    doc = DomainDictionary(
        domain="d",
        vocabulary=[
            {"key": "IRDAI", "value": "irda i"},
            {"key": "rider", "value": ""},
            {"key": "IRDAI", "value": "IRDA I"},  # repeat, any case
        ],
    )
    assert doc.keyterms == ["IRDAI", "irda i", "rider"]


# --- writes -----------------------------------------------------------------


@pytest.mark.anyio
async def test_upsert_accepts_a_plain_mapping() -> None:
    """The natural way to write a dictionary in Python is a dict."""
    res, http = _res(_DOC)
    await res.upsert("gamestop", vocabulary={"PowerUp Rewards": "power up rewords"})
    method, path, body = http.calls[0]
    assert (method, path) == ("PUT", f"{_BASE}/gamestop")
    assert body["vocabulary"] == [
        {"key": "PowerUp Rewards", "value": "power up rewords"}
    ]
    # Absent sections are sent EMPTY (the route replaces), and settings is
    # omitted entirely so stored filler knobs survive.
    assert body["pronunciation"] == [] and body["fillers"] == []
    assert "settings" not in body


@pytest.mark.anyio
async def test_upsert_round_trips_rows_and_kvitems() -> None:
    res, http = _res(_DOC)
    await res.upsert(
        "gamestop",
        vocabulary=[KVItem(key="GameStop", value="game stop")],
        pronunciation=[{"key": "GameStop", "value": "GAME-stop"}],
        fillers=[{"key": "en", "value": "One moment…"}],
        settings={"enabled": False},
    )
    body = http.calls[0][2]
    assert body["vocabulary"] == [{"key": "GameStop", "value": "game stop"}]
    assert body["pronunciation"] == [{"key": "GameStop", "value": "GAME-stop"}]
    assert body["fillers"] == [{"key": "en", "value": "One moment…"}]
    assert body["settings"] == {"enabled": False}


@pytest.mark.anyio
async def test_attach_and_detach_write_the_reverse_index() -> None:
    res, http = _res(_DOC)
    await res.attach("gamestop", "support")
    await res.detach("gamestop", "support")
    assert http.calls == [
        ("POST", f"{_BASE}/gamestop/attach", {"agent_id": "support"}),
        ("POST", f"{_BASE}/gamestop/detach", {"agent_id": "support"}),
    ]


@pytest.mark.anyio
async def test_clone_names_the_new_domain_in_the_body() -> None:
    res, http = _res(_DOC)
    await res.clone("banking", "acme-banking")
    assert http.calls == [
        ("POST", f"{_BASE}/banking/clone", {"new_domain": "acme-banking"}),
    ]


@pytest.mark.anyio
async def test_delete_targets_the_domain() -> None:
    res, http = _res()
    await res.delete("gamestop")
    assert http.calls == [("DELETE", f"{_BASE}/gamestop", None)]


# --- path safety ------------------------------------------------------------


@pytest.mark.anyio
async def test_a_domain_is_exactly_one_path_segment() -> None:
    """Domain names are free text: a slash must not re-point the request."""
    res, http = _res(_DOC)
    await res.get("real estate/../internal")
    assert http.calls[0][1] == f"{_BASE}/real%20estate%2F..%2Finternal"


# --- the tag reaches every creation surface ---------------------------------
#
# An agent can be created three ways and its brain can be one of four kinds.
# The dictionary is orthogonal to both, so EVERY surface has to be able to
# carry the tag — a surface that cannot is a shape of agent that can never use
# a dictionary at all.


class _CaptureHTTP(_FakeHTTP):
    pass


@pytest.mark.anyio
@pytest.mark.parametrize(
    "brain_name",
    ["prompt", "playbook", "endpoint", "runner"],
)
async def test_agents_voice_create_tags_every_brain_mode(brain_name: str) -> None:
    from unpod.agents import AgentsNamespace, Endpoint, Playbook, Prompt, Runner

    brains = {
        "prompt": Prompt("You are a bot."),
        "playbook": Playbook("PB_1"),
        "endpoint": Endpoint("https://brain.test/v1"),
        "runner": Runner("my-brain"),
    }
    http = _CaptureHTTP({"agent_id": "a", "domain": "gamestop"})
    ns = AgentsNamespace(http)  # type: ignore[arg-type]
    row = await ns.voice.create("a", brain=brains[brain_name], domain="gamestop")
    assert http.calls[0][2]["domain"] == "gamestop"
    assert http.calls[0][2]["brain"]["type"] == brain_name
    assert row.domain == "gamestop"


@pytest.mark.anyio
async def test_the_deprecated_pipes_route_tags_too() -> None:
    """It writes a complete agent row, so it must be able to tag one."""
    import warnings

    from unpod.management.pipes import PipesResource

    http = _CaptureHTTP(
        {
            "pipe_id": "PIPE_1",
            "project_id": "p",
            "name": "n",
            "domain": "gamestop",
        }
    )
    res = PipesResource(http)  # type: ignore[arg-type]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        pipe = await res.create(name="n", agent_id="brain", domain="gamestop")
    assert http.calls[0][2]["domain"] == "gamestop"
    assert pipe.domain == "gamestop"
