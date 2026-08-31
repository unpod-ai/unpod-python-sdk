"""client.agents.voice — one creation call, four brain sources.

The point of the typed union is that invalid combinations are unrepresentable.
There is no `prompt=` AND `playbook=` to pass together, so there is no
precedence rule to document and no validation error to write — which is what
the Aug 5 meeting asked for when it replaced `client.pipes.create`.
"""

from __future__ import annotations

from typing import Any

import pytest

from unpod import Endpoint, Playbook, Prompt, Runner
from unpod.agents import AgentsNamespace


class _FakeHTTP:
    """Records calls; replays canned responses."""

    def __init__(self, response: Any = None) -> None:
        self.calls: list[tuple[str, str, Any]] = []
        self._response = response if response is not None else {}

    async def post(self, path: str, json: dict | None = None) -> Any:
        self.calls.append(("POST", path, json))
        return self._response

    async def get(self, path: str, params: dict | None = None) -> Any:
        self.calls.append(("GET", path, params))
        return self._response

    async def put(self, path: str, json: dict | None = None) -> Any:
        self.calls.append(("PUT", path, json))
        return self._response

    async def delete(self, path: str) -> None:
        self.calls.append(("DELETE", path, None))

    async def delete_with_response(self, path: str):
        self.calls.append(("DELETE", path, None))
        return self._response


def _ns(response: Any = None) -> tuple[AgentsNamespace, _FakeHTTP]:
    http = _FakeHTTP(response)
    return AgentsNamespace(http), http  # type: ignore[arg-type]


_ROW = {
    "agent_id": "gamestop-support",
    "voice_profile_id": "VP_hi",
    "name": "Gamestop Support",
    "brain": {"type": "playbook", "ref": "PB_abc"},
    "brain_execution": "embedded",
    "is_default": True,
}


# --- the brain union --------------------------------------------------------


@pytest.mark.parametrize(
    "brain,expected",
    [
        (Playbook("PB_abc"), {"type": "playbook", "ref": "PB_abc"}),
        (Prompt("You are a bot."), {"type": "prompt", "ref": "You are a bot."}),
        (Runner(), {"type": "runner"}),
        (Runner("my-brain-v2"), {"type": "runner", "ref": "my-brain-v2"}),
    ],
)
def test_each_brain_source_serialises_to_one_object(brain, expected) -> None:
    assert brain.as_body() == expected


def test_endpoint_carries_its_http_config() -> None:
    body = Endpoint(
        "https://acme.co/v1/chat/completions",
        model="acme-agent",
        api_key="sk-acme",
        headers={"X-Tenant": "acme"},
        timeout_s=8.0,
    ).as_body()

    assert body["type"] == "endpoint"
    assert body["ref"] == "https://acme.co/v1/chat/completions"
    assert body["cfg"] == {
        "model": "acme-agent",
        "api_key": "sk-acme",
        "headers": {"X-Tenant": "acme"},
        "timeout_s": 8.0,
    }


def test_endpoint_omits_config_it_was_not_given() -> None:
    """No null-stuffing: an unset knob must not become an explicit null."""
    assert Endpoint("https://acme.co/v1").as_body() == {
        "type": "endpoint",
        "ref": "https://acme.co/v1",
    }


def test_two_brain_sources_are_unrepresentable() -> None:
    """The whole design: you cannot pass a prompt AND a playbook."""
    import inspect

    from unpod.agents import AgentVoiceResource

    params = inspect.signature(AgentVoiceResource.create).parameters
    assert "brain" in params
    assert {"prompt", "playbook", "agent_endpoint"}.isdisjoint(params)


# --- create -----------------------------------------------------------------


async def test_create_posts_the_brain_as_one_object() -> None:
    ns, http = _ns(_ROW)

    await ns.voice.create(
        "gamestop-support",
        brain=Playbook("PB_abc"),
        name="Gamestop Support",
        voice_profile="hindi-female-warm-hd",
    )

    method, path, body = http.calls[0]
    assert (method, path) == ("POST", "/api/v2/platform/speech/v1/agents")
    assert body["brain"] == {"type": "playbook", "ref": "PB_abc"}
    assert body["agent_id"] == "gamestop-support"
    assert body["voice_profile"] == "hindi-female-warm-hd"


async def test_root_create_alias_serialises_typed_runner() -> None:
    ns, http = _ns(_ROW)

    await ns.create("gamestop", brain=Runner(agent_id="gamestop"))

    assert http.calls[0][2]["brain"] == {
        "type": "runner",
        "ref": "gamestop",
    }


async def test_create_needs_no_playbook_for_a_prompt_agent() -> None:
    """PRD success criterion 1: a prompt agent, with nothing deployed."""
    ns, http = _ns(_ROW)

    await ns.voice.create("receptionist", brain=Prompt("You are a receptionist."))

    assert http.calls[0][2]["brain"] == {
        "type": "prompt",
        "ref": "You are a receptionist.",
    }


async def test_create_defaults_name_to_the_agent_id() -> None:
    ns, http = _ns(_ROW)
    await ns.voice.create("gamestop-support", brain=Runner())
    assert http.calls[0][2]["name"] == "gamestop-support"


async def test_create_omits_unset_optionals() -> None:
    ns, http = _ns(_ROW)
    await ns.voice.create("bot", brain=Runner())
    body = http.calls[0][2]
    assert "voice_profile" not in body
    assert "greeting" not in body
    assert "brain_execution" not in body


# --- voices and the group ---------------------------------------------------


async def test_add_voice_targets_the_agents_voices_collection() -> None:
    ns, http = _ns(_ROW)

    await ns.voice.add("gamestop-support", voice_profile="en-female-warm-hd")

    method, path, body = http.calls[0]
    assert method == "POST"
    assert path == "/api/v2/platform/speech/v1/agents/gamestop-support/voices"
    assert body == {"voice_profile": "en-female-warm-hd"}


async def test_remove_voice_deletes_only_that_voice() -> None:
    ns, http = _ns()
    await ns.voice.remove("gamestop-support", "VP_en")
    assert http.calls[0] == (
        "DELETE",
        "/api/v2/platform/speech/v1/agents/gamestop-support/voices/VP_en",
        None,
    )


async def test_get_returns_the_group_with_its_voices() -> None:
    ns, _ = _ns(
        {
            "agent_id": "gamestop-support",
            "name": "Gamestop Support",
            "brain": {"type": "playbook", "ref": "PB_abc"},
            "brain_execution": "embedded",
            "voices": [_ROW, {**_ROW, "voice_profile_id": "VP_en", "is_default": False}],
        }
    )

    agent = await ns.get("gamestop-support")

    assert {v.voice_profile_id for v in agent.voices} == {"VP_hi", "VP_en"}
    assert agent.brain["ref"] == "PB_abc"


async def test_update_sends_the_brain_as_one_object() -> None:
    ns, http = _ns({"agent_id": "gamestop-support", "voices": []})

    await ns.update("gamestop-support", brain=Prompt("You are a bot."))

    method, path, body = http.calls[0]
    assert (method, path) == ("PUT", "/api/v2/platform/speech/v1/agents/gamestop-support")
    assert body["brain"] == {"type": "prompt", "ref": "You are a bot."}


async def test_numbers_attach_binds_by_agent_id() -> None:
    ns, http = _ns({})

    await ns.numbers.attach("gamestop-support", "NUM_1")

    method, path, body = http.calls[0]
    assert method == "POST"
    assert path.endswith("/numbers/NUM_1/attach")
    # The E.164 rides the BODY: supervoice matches an existing sv_numbers row
    # by number before falling back to the path id.
    assert body == {"number": "NUM_1", "agent_id": "gamestop-support"}


# --- backward compatibility -------------------------------------------------


def test_pipes_remains_available_on_both_clients() -> None:
    """7 in-repo call sites plus unknown user code — one release of overlap."""
    from unpod.client import AsyncClient, Client

    assert hasattr(AsyncClient, "__init__")
    for cls in (AsyncClient, Client):
        src = cls.__init__.__code__.co_names
        assert "PipesResource" in src or "pipes" in str(src)


def test_brain_types_are_exported_at_package_root() -> None:
    import unpod

    for name in ("Playbook", "Prompt", "Endpoint", "Runner"):
        assert name in unpod.__all__
        assert hasattr(unpod, name)


async def test_numbers_detach_targets_the_route_that_exists() -> None:
    """DELETE /numbers/{id}/attach — there is no POST /detach on the platform.

    numbers.py exposes POST /numbers/{id}/attach and DELETE
    /numbers/{id}/attach; detaching is modelled as removing the attachment.
    The first version of this method POSTed to a /detach path that 404s.
    """
    ns, http = _ns({})

    await ns.numbers.detach("NUM_1")

    method, path, _ = http.calls[0]
    assert method == "DELETE"
    assert path == "/api/v2/platform/speech/v1/numbers/NUM_1/attach"


# --- domain dictionary tag --------------------------------------------------
#
# The dictionary itself lives in client.domain_dictionaries; THIS is the tag
# that decides whether an agent uses one. Nothing resolves a dictionary by name
# at call time, so an agent created without the tag applies none.


@pytest.mark.anyio
async def test_create_sends_the_domain_tag() -> None:
    ns, http = _ns(_ROW)
    await ns.voice.create("support", brain=Runner(), domain="gamestop")
    assert http.calls[0][2]["domain"] == "gamestop"


@pytest.mark.anyio
async def test_create_without_a_domain_sends_no_key() -> None:
    ns, http = _ns(_ROW)
    await ns.voice.create("support", brain=Runner())
    assert "domain" not in http.calls[0][2]


@pytest.mark.anyio
async def test_the_response_exposes_which_dictionary_an_agent_uses() -> None:
    ns, _ = _ns({**_ROW, "domain": "gamestop"})
    row = await ns.voice.create("support", brain=Runner())
    assert row.domain == "gamestop"


@pytest.mark.anyio
async def test_update_retags_the_dictionary() -> None:
    ns, http = _ns({"agent_id": "support", "voices": []})
    await ns.update("support", domain="banking")
    assert http.calls[0][:2] == (
        "PUT",
        "/api/v2/platform/speech/v1/agents/support",
    )
    assert http.calls[0][2] == {"domain": "banking"}


@pytest.mark.anyio
async def test_update_can_detach_with_an_empty_domain() -> None:
    """`""` is the explicit detach; omitting the kwarg leaves the tag alone."""
    ns, http = _ns({"agent_id": "support", "voices": []})
    await ns.update("support", domain="")
    assert http.calls[0][2] == {"domain": ""}
    await ns.update("support", name="Renamed")
    assert "domain" not in http.calls[1][2]
