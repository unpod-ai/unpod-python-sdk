"""Tests for Session and CallContext."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from unpod.connectivity.call_context import CallContext
from unpod.connectivity.session import Session


def test_session_data():
    session = Session(bridge=MagicMock())
    session.data["verified"] = True
    assert session.data["verified"] is True


def test_session_data_isolated():
    s1 = Session(bridge=MagicMock())
    s2 = Session(bridge=MagicMock())
    s1.data["x"] = 1
    assert "x" not in s2.data


def test_session_dialog_machine_auto_wrap_superdialog():
    """Assigning a superdialog DialogMachine auto-wraps in SuperDialogAdapter."""
    session = Session(bridge=MagicMock())
    mock_dm = MagicMock()
    # Simulate superdialog.DialogMachine by setting class attributes
    mock_dm.__class__ = type(
        "DialogMachine", (), {"__module__": "superdialog.dialog_machine"}
    )
    session.dialog_machine = mock_dm
    from unpod.adapters.superdialog import SuperDialogAdapter

    assert isinstance(session._dialog_adapter, SuperDialogAdapter)


def test_session_dialog_machine_accepts_adapter():
    session = Session(bridge=MagicMock())

    class FakeAdapter:
        async def turn(self, text: str, context: dict | None = None) -> str:
            return "ok"

        async def stream(self, text: str, context: dict | None = None):  # type: ignore[override]
            yield "ok"

        def assist(self, text: str) -> None:
            pass

    adapter = FakeAdapter()
    session.dialog_machine = adapter
    assert session._dialog_adapter is adapter


def test_session_dialog_machine_rejects_invalid():
    session = Session(bridge=MagicMock())
    with pytest.raises(TypeError):
        session.dialog_machine = "not an adapter"


@pytest.mark.anyio
async def test_session_on_decorator():
    session = Session(bridge=MagicMock())
    received: list[str] = []

    @session.on("user_turn")
    async def _(text: str) -> None:
        received.append(text)

    await session._hooks.fire("user_turn", "hello")
    assert received == ["hello"]


@pytest.mark.anyio
async def test_session_say():
    mock_bridge = AsyncMock()
    session = Session(bridge=mock_bridge)
    await session.say("Please hold")
    mock_bridge.send_verb.assert_called_once()
    call_args = mock_bridge.send_verb.call_args[0][0]
    assert call_args.text == "Please hold"


@pytest.mark.anyio
async def test_session_end():
    mock_bridge = AsyncMock()
    session = Session(bridge=mock_bridge)
    await session.end(reason="completed")
    mock_bridge.send_verb.assert_called_once()
    call_args = mock_bridge.send_verb.call_args[0][0]
    assert call_args.reason == "completed"


@pytest.mark.anyio
async def test_session_transfer_to_human():
    mock_bridge = AsyncMock()
    session = Session(bridge=mock_bridge)
    await session.transfer_to_human(queue="tier2")
    mock_bridge.send_verb.assert_called_once()
    verb = mock_bridge.send_verb.call_args[0][0]
    assert verb.transfer_type == "human"
    assert verb.target == "tier2"


@pytest.mark.anyio
async def test_session_transfer_to_agent():
    mock_bridge = AsyncMock()
    session = Session(bridge=mock_bridge)
    await session.transfer_to_agent(agent_id="senior-bot")
    mock_bridge.send_verb.assert_called_once()
    verb = mock_bridge.send_verb.call_args[0][0]
    assert verb.transfer_type == "agent"
    assert verb.target == "senior-bot"


@pytest.mark.anyio
async def test_session_interrupt():
    mock_bridge = AsyncMock()
    session = Session(bridge=mock_bridge)
    await session.interrupt()
    mock_bridge.send_verb.assert_called_once()


@pytest.mark.anyio
async def test_session_recording_pause():
    mock_bridge = AsyncMock()
    session = Session(bridge=mock_bridge)
    await session.recording.pause(reason="pii")
    mock_bridge.send_verb.assert_called_once()


@pytest.mark.anyio
async def test_session_recording_resume():
    mock_bridge = AsyncMock()
    session = Session(bridge=mock_bridge)
    await session.recording.resume()
    mock_bridge.send_verb.assert_called_once()


def test_session_metrics_live():
    session = Session(bridge=MagicMock())
    m = session.metrics.live()
    assert m.turns == 0


def test_call_context_fields():
    ctx = CallContext(
        call_id="call_1",
        session_id="sess_1",
        agent_id="kyc-bot",
        direction="inbound",
        user_number="+919800000001",
        instructions=None,
        data={},
        session=MagicMock(),
    )
    assert ctx.call_id == "call_1"
    assert ctx.direction == "inbound"


def test_call_context_data_access():
    mock_session = MagicMock()
    ctx = CallContext(
        call_id="call_1",
        session_id="sess_1",
        agent_id="kyc-bot",
        direction="outbound",
        user_number="+919800000002",
        instructions="Speak Hindi",
        data={"customer_id": "C123"},
        session=mock_session,
    )
    assert ctx.data["customer_id"] == "C123"
    assert ctx.instructions == "Speak Hindi"


@pytest.mark.anyio
async def test_session_error_hook_fires_on_error_event():
    """on('error') hook fires when ErrorEvent is received."""
    from unpod._protocol import ErrorEvent

    received: list[dict] = []
    mock_bridge = AsyncMock()
    mock_bridge.recv_event = AsyncMock(
        return_value=ErrorEvent(
            severity="error", source="stt", code="rate_limit", message="Quota exceeded"
        )
    )

    session = Session(bridge=mock_bridge)

    @session.on("error")
    async def _(code: str, message: str, severity: str, source: str) -> None:
        received.append(
            {"code": code, "message": message, "severity": severity, "source": source}
        )

    await session.run()

    assert len(received) == 1
    assert received[0]["code"] == "rate_limit"
    assert received[0]["severity"] == "error"
    assert received[0]["source"] == "stt"


@pytest.mark.anyio
async def test_session_call_end_reason_is_error_on_error_event():
    """call_end hook receives reason='error' when session ends via ErrorEvent."""
    from unpod._protocol import ErrorEvent

    end_reasons: list[str] = []
    mock_bridge = AsyncMock()
    mock_bridge.recv_event = AsyncMock(
        return_value=ErrorEvent(
            severity="error", source="bridge", code="disconnect", message=""
        )
    )

    session = Session(bridge=mock_bridge)

    @session.on("call_end")
    async def _(reason: str) -> None:
        end_reasons.append(reason)

    await session.run()
    assert end_reasons == ["error"]


@pytest.mark.anyio
async def test_session_call_end_reason_is_hangup_on_normal_exit():
    """call_end hook receives reason='hangup' when bridge closes cleanly (exception, not ErrorEvent)."""
    end_reasons: list[str] = []
    mock_bridge = AsyncMock()
    mock_bridge.recv_event = AsyncMock(side_effect=Exception("connection closed"))

    session = Session(bridge=mock_bridge)

    @session.on("call_end")
    async def _(reason: str) -> None:
        end_reasons.append(reason)

    await session.run()
    assert end_reasons == ["hangup"]


@pytest.mark.anyio
async def test_session_fires_metric_hook():
    """A MetricEvent from the bridge fires the 'metric' hook (regression).

    Previously run() dropped MetricEvents with a bare ``continue``, so the
    declared 'metric' hook never fired and metrics could not be surfaced.
    """
    from unpod._protocol import MetricEvent

    received: list[MetricEvent] = []
    mock_bridge = AsyncMock()
    mock_bridge.recv_event = AsyncMock(
        side_effect=[
            MetricEvent(ttfa_ms=120, turns=2, cost_usd_so_far=0.01),
            Exception("connection closed"),
        ]
    )

    session = Session(bridge=mock_bridge)

    @session.on("metric")
    async def _(metric: MetricEvent) -> None:
        received.append(metric)

    await session.run()
    assert len(received) == 1
    assert received[0].ttfa_ms == 120
    assert received[0].turns == 2


@pytest.mark.anyio
async def test_session_fires_state_hook():
    """A StateEvent from the bridge fires the 'state' hook (mirrors metric).

    The hook is observability only — it must never drive dialog or end the
    call, so the loop continues past it (here the next recv raises to stop).
    """
    from unpod._protocol import StateEvent

    received: list[StateEvent] = []
    mock_bridge = AsyncMock()
    mock_bridge.recv_event = AsyncMock(
        side_effect=[
            StateEvent(state="thinking", turn_id=2),
            Exception("connection closed"),
        ]
    )

    session = Session(bridge=mock_bridge)

    @session.on("state")
    async def _(evt: StateEvent) -> None:
        received.append(evt)

    await session.run()
    assert len(received) == 1
    assert received[0].state == "thinking"
    assert received[0].turn_id == 2
    # State is not a dialog driver: no verbs were sent in response.
    mock_bridge.send_verb.assert_not_called()


@pytest.mark.anyio
async def test_session_wires_llm_callback_to_superdialog_adapter():
    """Session registers _on_llm_complete on SuperDialogAdapter if present."""
    from unittest.mock import AsyncMock, MagicMock

    from unpod.adapters.superdialog import SuperDialogAdapter
    from unpod.connectivity.session import Session

    mock_dm = MagicMock()
    mock_adapter = MagicMock()
    mock_dm._adapter = mock_adapter
    mock_dm.is_complete = False

    sd_adapter = SuperDialogAdapter(mock_dm)

    mock_bridge = AsyncMock()
    mock_bridge.session_id = "sess-wire-test"

    ctx = MagicMock()
    ctx.call_id = "c1"
    ctx.session_id = "sess-wire-test"

    session = Session(ctx, mock_bridge)
    session.dialog_machine = sd_adapter

    # After setting dialog_machine, the LLM callback should be registered.
    assert mock_adapter._on_llm_complete is not None


@pytest.mark.anyio
async def test_session_emits_turn_complete_without_turn_metrics_event() -> None:
    """A completed turn still emits turn_complete if turn.metrics never arrives."""
    from unpod._protocol import UserTextEvent

    received: list[dict[str, object]] = []

    class FakeAdapter:
        is_complete = False

        async def turn(self, text: str, context: dict | None = None) -> str:
            return "ok"

        async def stream(self, text: str, context: dict | None = None):  # type: ignore[override]
            yield "hello"

        def assist(self, text: str) -> None:
            pass

    mock_bridge = AsyncMock()
    mock_bridge.recv_event = AsyncMock(
        side_effect=[
            UserTextEvent(text="hi", is_final=True),
            Exception("done"),
        ]
    )
    mock_bridge.send_verb = AsyncMock()

    session = Session(bridge=mock_bridge)
    session.dialog_machine = FakeAdapter()

    @session.on("turn_complete")
    async def _(**data: object) -> None:
        received.append(data)

    await session.run()

    assert len(received) == 1
    assert received[0]["turn_id"] == 1
