"""``agent.transfer`` carries an ordered fallback list.

The field must exist on the SDK's own verb model, not only on supervoice's copy:
pydantic drops undeclared fields on serialisation, so a fallback list the SDK
does not declare never reaches the wire. The worker would try one number,
apologise, and leave no trace explaining why the configured fallbacks were
ignored.

The worker walks the list because ``Session.transfer`` is fire-and-forget — a
caller-side loop would dial every number at once.
"""

from unittest.mock import AsyncMock

import pytest

from unpod._protocol import AgentTransferVerb


def test_the_verb_declares_a_fallback_list():
    verb = AgentTransferVerb(target="+91900", fallback_targets=["+91901", "+91902"])

    assert verb.fallback_targets == ["+91901", "+91902"]


def test_the_fallback_list_defaults_to_empty():
    assert AgentTransferVerb(target="+91900").fallback_targets == []


def test_the_fallback_list_survives_serialisation():
    """The actual bug being prevented."""
    dumped = AgentTransferVerb(
        target="+91900", fallback_targets=["+91901"]
    ).model_dump()

    assert dumped["fallback_targets"] == ["+91901"]


class _Bridge:
    def __init__(self) -> None:
        self.sent: list[object] = []
        self.send_verb = AsyncMock(side_effect=lambda v: self.sent.append(v))


def _session() -> tuple[object, _Bridge]:
    from unpod.connectivity.session import Session

    bridge = _Bridge()
    session = Session.__new__(Session)
    session._bridge = bridge  # type: ignore[attr-defined]
    return session, bridge


@pytest.mark.anyio
async def test_transfer_passes_the_fallback_list_to_the_verb():
    session, bridge = _session()

    await session.transfer("+91900", mode="warm", fallback_targets=["+91901", "+91902"])

    assert bridge.sent[0].fallback_targets == ["+91901", "+91902"]
    assert bridge.sent[0].mode == "warm"


@pytest.mark.anyio
async def test_transfer_without_fallbacks_is_unchanged():
    session, bridge = _session()

    await session.transfer("+91900")

    assert bridge.sent[0].fallback_targets == []
    assert bridge.sent[0].target == "+91900"
    assert bridge.sent[0].mode == "cold"


@pytest.mark.anyio
async def test_transfer_to_human_still_works_without_the_new_argument():
    session, bridge = _session()

    await session.transfer_to_human("support-queue")

    assert bridge.sent[0].transfer_type == "human"
    assert bridge.sent[0].fallback_targets == []
