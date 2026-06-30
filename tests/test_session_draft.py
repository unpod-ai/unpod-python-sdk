"""A2a/A2b speculative-draft relay in Session.run.

A non-final ``user.text`` partial drafts a reply the worker holds. The committed
turn either RELEASES the draft via ``agent.draft.commit`` (text matched what the
draft assumed) or RETRACTS it via ``agent.draft.retract`` and speaks the committed
stream fresh (text diverged — A2b revise-on-divergence). A newer partial also
retracts the stale draft before redrafting. See
``supervoice/docs/2026-06-30-speculative-turn-design.md`` §6.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from unpod._protocol import (
    AgentDraftCommitVerb,
    AgentDraftRetractVerb,
    AgentTextDeltaEvent,
    UserTextEvent,
)
from unpod.connectivity.session import Session


class _DraftAdapter:
    """Adapter exposing both the committed ``stream`` and A2a ``draft_stream``."""

    is_complete = False

    def __init__(self) -> None:
        self.draft_calls: list[str] = []
        self.stream_calls: list[str] = []

    async def turn(self, text: str, context: dict | None = None) -> str:
        return "ok"

    async def stream(  # type: ignore[override]
        self, text: str, context: dict | None = None, language: str | None = None
    ):
        self.stream_calls.append(text)
        yield "committed-reply"

    async def draft_stream(  # type: ignore[override]
        self, text: str, context: dict | None = None, *, language: str | None = None
    ):
        self.draft_calls.append(text)
        # Await between chunks so the background draft task interleaves with the
        # run loop (lets a newer partial/final supersede it mid-stream).
        await asyncio.sleep(0)
        yield "draft-"
        await asyncio.sleep(0)
        yield "reply"

    def assist(self, text: str) -> None:
        pass


def _bridge(events: list) -> AsyncMock:
    """A mock bridge whose recv_event yields control each call (so background
    draft tasks run) then returns the next scripted event, ending on exhaustion."""
    seq = iter(events)

    async def _recv():
        await asyncio.sleep(0)
        try:
            return next(seq)
        except StopIteration:
            raise Exception("done")

    b = AsyncMock()
    b.recv_event = _recv
    b.send_verb = AsyncMock()
    return b


def _verbs(b: AsyncMock, cls) -> list:
    return [c.args[0] for c in b.send_verb.call_args_list if isinstance(c.args[0], cls)]


def _deltas(send_verb: AsyncMock) -> list[AgentTextDeltaEvent]:
    return [
        c.args[0]
        for c in send_verb.call_args_list
        if isinstance(c.args[0], AgentTextDeltaEvent)
    ]


@pytest.mark.anyio
async def test_matching_final_commits_draft() -> None:
    """Final text == the draft's grounding → release the held draft (A2a path).

    The worker speaks the held draft, so the committed turn runs (state + tools)
    but its text is suppressed; a single commit fires before the end.
    """
    adapter = _DraftAdapter()
    b = _bridge(
        [
            UserTextEvent(text="book a table", is_final=False),
            UserTextEvent(text="book a table", is_final=True),
        ]
    )
    session = Session(bridge=b)
    session.dialog_machine = adapter
    await session.run()

    assert adapter.draft_calls == ["book a table"]
    assert adapter.stream_calls == ["book a table"]

    deltas = _deltas(b.send_verb)
    assert "".join(d.text for d in deltas if d.draft) == "draft-reply"
    # Committed text suppressed — the held draft is the spoken reply.
    assert [d for d in deltas if not d.draft] == []

    events = [c.args[0] for c in b.send_verb.call_args_list]
    commit_idx = [i for i, e in enumerate(events) if isinstance(e, AgentDraftCommitVerb)]
    end_idx = [i for i, e in enumerate(events) if type(e).__name__ == "AgentTextEndEvent"]
    assert len(commit_idx) == 1
    assert events[commit_idx[0]].turn_id == 1
    assert commit_idx[0] < end_idx[0]
    assert _verbs(b, AgentDraftRetractVerb) == []


@pytest.mark.anyio
async def test_divergent_final_retracts_and_speaks_committed() -> None:
    """Final text differs from the draft's grounding → retract + speak committed.

    A2b revise-on-divergence: the stale draft is dropped (retract) and the
    committed stream's text flows to TTS instead of being suppressed.
    """
    adapter = _DraftAdapter()
    b = _bridge(
        [
            UserTextEvent(text="from delhi to", is_final=False),
            UserTextEvent(text="from delhi to bengaluru", is_final=True),
        ]
    )
    session = Session(bridge=b)
    session.dialog_machine = adapter
    await session.run()

    assert adapter.stream_calls == ["from delhi to bengaluru"]
    # Exactly one retract; no commit (the draft was discarded, not released).
    assert len(_verbs(b, AgentDraftRetractVerb)) == 1
    assert _verbs(b, AgentDraftCommitVerb) == []
    # Committed text is spoken (not suppressed) since nothing is held.
    committed = [d.text for d in _deltas(b.send_verb) if not d.draft]
    assert committed == ["committed-reply"]


@pytest.mark.anyio
async def test_newer_partial_supersedes_draft() -> None:
    """A2b: a newer partial aborts the stale draft (retract) and redrafts; the
    matching final then commits the fresh draft."""
    adapter = _DraftAdapter()
    b = _bridge(
        [
            UserTextEvent(text="from delhi to", is_final=False),
            UserTextEvent(text="from delhi to bengaluru", is_final=False),
            UserTextEvent(text="from delhi to bengaluru", is_final=True),
        ]
    )
    session = Session(bridge=b)
    session.dialog_machine = adapter
    await session.run()

    # The fresh draft (drained on the matching final) assumed the full utterance.
    assert "from delhi to bengaluru" in adapter.draft_calls
    # At least one retract fired for the superseded draft; the fresh draft commits.
    assert len(_verbs(b, AgentDraftRetractVerb)) >= 1
    assert len(_verbs(b, AgentDraftCommitVerb)) == 1
    assert [d for d in _deltas(b.send_verb) if not d.draft] == []  # committed suppressed


@pytest.mark.anyio
async def test_final_without_prior_draft_emits_no_commit() -> None:
    """A plain final turn (no partial) emits neither commit nor retract."""
    adapter = _DraftAdapter()
    b = _bridge([UserTextEvent(text="hello", is_final=True)])
    session = Session(bridge=b)
    session.dialog_machine = adapter
    await session.run()

    assert adapter.draft_calls == []
    assert _verbs(b, AgentDraftCommitVerb) == []
    assert _verbs(b, AgentDraftRetractVerb) == []


@pytest.mark.anyio
async def test_partial_to_adapter_without_draft_surface_is_ignored() -> None:
    """A partial is silently dropped when the adapter has no draft_stream."""

    class _PlainAdapter:
        is_complete = False

        async def turn(self, text: str, context: dict | None = None) -> str:
            return "ok"

        async def stream(  # type: ignore[override]
            self, text: str, context: dict | None = None, language: str | None = None
        ):
            yield "ok"

        def assist(self, text: str) -> None:
            pass

    b = _bridge([UserTextEvent(text="partial", is_final=False)])
    session = Session(bridge=b)
    session.dialog_machine = _PlainAdapter()
    await session.run()

    # No draft surface → no deltas, no commit, no crash.
    assert _deltas(b.send_verb) == []
