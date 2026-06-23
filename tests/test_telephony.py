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
    TelephonyNamespace,
    Trunk,
)


class _FakeHTTP:
    """Records calls; returns canned responses keyed by (method, path)."""

    def __init__(self, responses: dict) -> None:
        self.responses = responses
        self.calls: list[tuple] = []

    async def get(self, path, params=None):
        self.calls.append(("GET", path, None))
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
    http = _FakeHTTP({
        ("GET", "/telephony/numbers/"): {
            "status_code": 200, "message": "ok",
            "data": [{"id": 1, "number": "+1555"}],
        }
    })
    ns = TelephonyNamespace(http)
    nums = await ns.numbers.list()
    assert [n.id for n in nums] == [1]
    assert nums[0].number == "+1555"


@pytest.mark.anyio
async def test_trunk_create_maps_username_password():
    http = _FakeHTTP({
        ("POST", "/telephony/trunks/"): {
            "status_code": 201, "message": "ok", "data": {"id": 7, "name": "C"},
        }
    })
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
    res = await ns.numbers.attach([1], agent_id="asst_sales", bridge_slug="b")
    assert isinstance(res, AgentAttachResult)
    assert res.agent_id == "asst_sales"
    assert res.numbers[0].ok is True
    assert res.numbers[0].agent_id == "asst_sales"
    # primary Leg-B flow returns no carrier origin endpoint
    assert not hasattr(res, "origin_endpoint") or res.origin_endpoint is None
    _, _, body = http.calls[0]
    assert body == {"number_ids": [1], "agent_id": "asst_sales", "bridge_slug": "b"}


@pytest.mark.anyio
async def test_numbers_attach_without_agent_id_omits_it():
    resp = {
        "status_code": 201, "message": "ok",
        "data": {"agent_id": None, "numbers": [{"number_id": 1, "ok": True}]},
    }
    http = _FakeHTTP({("POST", "/telephony/numbers/attach/"): resp})
    ns = TelephonyNamespace(http)
    res = await ns.numbers.attach([1])
    assert isinstance(res, AgentAttachResult)
    assert res.agent_id is None
    _, _, body = http.calls[0]
    assert body == {"number_ids": [1]}  # agent_id omitted, not sent as null


@pytest.mark.anyio
async def test_overview():
    http = _FakeHTTP({
        ("GET", "/telephony/overview/"): {
            "status_code": 200, "message": "ok",
            "data": [{"number_id": 1, "in_sync": True}],
        }
    })
    ns = TelephonyNamespace(http)
    rows = await ns.overview()
    assert rows[0].in_sync is True
