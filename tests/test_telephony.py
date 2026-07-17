"""Tests for the backend-core telephony plane (``client.telephony.*``).

Fixtures use the REAL backend wire shape: every ``/api/v2/platform`` response is
wrapped by ``UnpodJSONRenderer`` as ``{"status_code", "message", "data"}``. The
SDK unwraps that envelope (``unwrap_data``) before binding models — these tests
feed the enveloped shape so they exercise the true contract.
"""

from __future__ import annotations

import pytest
from unpod._base_url import platform_base
from unpod.telephony import (
    AgentAttachResult,
    AttachResult,
    Number,
    TelephonyNamespace,
    Trunk,
    _as_number_list,
)


class _FakeHTTP:
    """Records calls; returns canned responses keyed by (method, path)."""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[tuple] = []

    async def get(self, path, params=None):
        self.calls.append(("GET", path, params))
        return self.responses[("GET", path)]

    async def post(self, path, json=None):
        self.calls.append(("POST", path, json))
        return self.responses[("POST", path)]

    async def delete(self, path):
        self.calls.append(("DELETE", path, None))


def test_platform_base(monkeypatch):
    monkeypatch.setenv("UNPOD_BASE_URL", "api.unpod.ai")
    assert platform_base() == "https://api.unpod.ai/api/v2/platform"


@pytest.mark.anyio
async def test_numbers_list():
    http = _FakeHTTP(
        {
            ("GET", "/telephony/numbers/"): {
                "status_code": 200,
                "message": "ok",
                "data": [{"id": 1, "number": "+1555"}],
            }
        }
    )
    ns = TelephonyNamespace(http)
    nums = await ns.numbers.list()
    assert [n.number for n in nums] == ["+1555"]
    assert not hasattr(nums[0], "id")


@pytest.mark.anyio
async def test_trunk_create_maps_username_password():
    http = _FakeHTTP(
        {
            ("POST", "/telephony/trunks/"): {
                "status_code": 201,
                "message": "ok",
                "data": {"id": 7, "name": "C"},
            }
        }
    )
    ns = TelephonyNamespace(http)
    trunk = await ns.trunks.create(
        "C", "sip:c.net", username="u", password="p", source_ips=["1.2.3.4"]
    )
    assert isinstance(trunk, Trunk) and trunk.id == 7
    _, _, body = http.calls[0]
    assert body == {
        "name": "C",
        "sip_url": "sip:c.net",
        "auth_username": "u",
        "auth_password": "p",
        "source_ips": ["1.2.3.4"],
    }


@pytest.mark.anyio
async def test_attach_numbers_returns_origin_endpoint():
    resp = {
        "status_code": 201,
        "message": "ok",
        "data": {
            "trunk_id": 7,
            "origin_endpoint": {
                "ingress": "sip:sbc.unpod",
                "dids": ["+1555"],
                "accepted_source_ips": ["1.2.3.4"],
                "region": "IN",
            },
            "numbers": [{"number_id": 1, "number": "+1555", "ok": True}],
        },
    }
    http = _FakeHTTP({("POST", "/telephony/trunks/7/attach-numbers/"): resp})
    ns = TelephonyNamespace(http)
    res = await ns.trunks.attach_numbers(7, [1], bridge_slug="b")
    assert isinstance(res, AttachResult)
    assert res.origin_endpoint.ingress == "sip:sbc.unpod"
    assert res.origin_endpoint.dids == ["+1555"]
    assert res.numbers[0].ok is True
    _, _, body = http.calls[0]
    assert body == {"number_ids": [1], "bridge_slug": "b"}


@pytest.mark.anyio
async def test_numbers_attach_to_agent():
    resp = {
        "status_code": 201,
        "message": "Numbers attached to agent.",
        "data": {
            "agent_id": "asst_sales",
            "numbers": [
                {
                    "number_id": 1,
                    "number": "+1555",
                    "connection_state": "NOT_LINKED",
                    "agent_id": "asst_sales",
                    "ok": True,
                }
            ],
        },
    }
    http = _FakeHTTP({("POST", "/telephony/numbers/attach/"): resp})
    ns = TelephonyNamespace(http)
    res = await ns.numbers.attach(
        ["+1555"], agent_id="asst_sales", bridge_slug="b"
    )
    assert isinstance(res, AgentAttachResult)
    assert res.agent_id == "asst_sales"
    assert res.numbers[0].ok is True
    assert res.numbers[0].agent_id == "asst_sales"
    # primary Leg-B flow returns no carrier origin endpoint
    assert not hasattr(res, "origin_endpoint") or res.origin_endpoint is None
    _, _, body = http.calls[0]
    assert body == {
        "numbers": ["+1555"],
        "agent_id": "asst_sales",
        "bridge_slug": "b",
    }


@pytest.mark.anyio
async def test_numbers_attach_without_agent_id_omits_it():
    resp = {
        "status_code": 201,
        "message": "ok",
        "data": {"agent_id": None, "numbers": [{"number_id": 1, "ok": True}]},
    }
    http = _FakeHTTP({("POST", "/telephony/numbers/attach/"): resp})
    ns = TelephonyNamespace(http)
    res = await ns.numbers.attach(["+1555"])
    assert isinstance(res, AgentAttachResult)
    assert res.agent_id is None
    _, _, body = http.calls[0]
    assert body == {"numbers": ["+1555"]}  # agent_id omitted, not sent as null


@pytest.mark.anyio
async def test_numbers_attach_sends_attach_type():
    resp = {
        "status_code": 201,
        "message": "ok",
        "data": {"agent_id": "asst_sales", "numbers": [{"number_id": 1, "ok": True}]},
    }
    http = _FakeHTTP({("POST", "/telephony/numbers/attach/"): resp})
    ns = TelephonyNamespace(http)
    await ns.numbers.attach(
        ["+1555"], agent_id="asst_sales", attach_type="pipeline", pipe_id="PIPE_y"
    )
    _, _, body = http.calls[0]
    assert body["attach_type"] == "pipeline"
    assert body["pipe_id"] == "PIPE_y"


@pytest.mark.anyio
async def test_numbers_attach_omits_attach_type_when_unset():
    resp = {
        "status_code": 201,
        "message": "ok",
        "data": {"agent_id": None, "numbers": [{"number_id": 1, "ok": True}]},
    }
    http = _FakeHTTP({("POST", "/telephony/numbers/attach/"): resp})
    ns = TelephonyNamespace(http)
    await ns.numbers.attach(["+1555"])
    _, _, body = http.calls[0]
    assert "attach_type" not in body  # server default ("agent") applies
    assert "pipe_id" not in body  # agent mode carries no pipe


@pytest.mark.anyio
async def test_overview():
    http = _FakeHTTP(
        {
            ("GET", "/telephony/overview/"): {
                "status_code": 200,
                "message": "ok",
                "data": [{"number_id": 1, "in_sync": True}],
            }
        }
    )
    ns = TelephonyNamespace(http)
    rows = await ns.overview()
    assert rows[0].in_sync is True


def test_number_status_assigned():
    n = Number(number="+919876543210", state="ASSIGNED", active=True)
    assert n.status == "assigned"


def test_number_status_not_assigned_means_attachable():
    n = Number(number="+919876543210", state="NOT_ASSIGNED", active=True)
    assert n.status == "not_assigned"


def test_number_inactive_is_closed_not_not_assigned():
    """A listing must never promise an attach that would fail."""
    n = Number(number="+919876543210", state="NOT_ASSIGNED", active=False)
    assert n.status == "closed"


def test_number_exposes_no_id():
    n = Number(number="+919876543210", state="NOT_ASSIGNED", active=True)
    assert not hasattr(n, "id")


@pytest.mark.anyio
async def test_list_sends_include_assigned():
    """Without the flag the SDK could not show a user their own attached numbers."""
    http = _FakeHTTP(
        {
            ("GET", "/telephony/numbers/"): {
                "status_code": 200,
                "message": "ok",
                "data": [
                    {"id": 1, "number": "+919876543210", "state": "ASSIGNED", "active": True},
                    {"id": 2, "number": "+14155551234", "state": "NOT_ASSIGNED", "active": True},
                ],
            }
        }
    )
    ns = TelephonyNamespace(http)

    nums = await ns.numbers.list()

    assert http.calls == [("GET", "/telephony/numbers/", {"include_assigned": "true"})]
    assert [n.status for n in nums] == ["assigned", "not_assigned"]
    assert not hasattr(nums[0], "id")


def test_as_number_list_wraps_a_bare_string():
    """A bare str is ONE number, never iterated char-by-char."""
    assert _as_number_list("+919876543210") == ["+919876543210"]


def test_as_number_list_passes_a_sequence():
    assert _as_number_list(["+919876543210", "+14155551234"]) == [
        "+919876543210",
        "+14155551234",
    ]


@pytest.mark.anyio
async def test_attach_sends_numbers_never_ids():
    http = _FakeHTTP(
        {
            ("POST", "/telephony/numbers/attach/"): {
                "status_code": 201,
                "message": "ok",
                "data": {"agent_id": "a1", "numbers": [], "message": "ok"},
            }
        }
    )
    ns = TelephonyNamespace(http)

    await ns.numbers.attach(
        "+919876543210", agent_id="a1", attach_type="pipeline", pipe_id="PIPE_x"
    )

    _, path, body = http.calls[0]
    assert path == "/telephony/numbers/attach/"
    assert body["numbers"] == ["+919876543210"]
    assert "number_ids" not in body
    assert body["agent_id"] == "a1"
    assert body["attach_type"] == "pipeline"
    assert body["pipe_id"] == "PIPE_x"


@pytest.mark.anyio
async def test_detach_sends_numbers():
    http = _FakeHTTP(
        {
            ("POST", "/telephony/numbers/detach/"): {
                "status_code": 200,
                "message": "ok",
                "data": {"numbers": [], "message": "ok"},
            }
        }
    )
    ns = TelephonyNamespace(http)

    await ns.numbers.detach("+919876543210")

    _, path, body = http.calls[0]
    assert path == "/telephony/numbers/detach/"
    assert body == {"numbers": ["+919876543210"]}
