"""Verify all public exports are accessible."""


def test_top_level_imports() -> None:
    from unpod import AgentRunner, AsyncClient, CallContext, Client, Session

    assert AgentRunner is not None
    assert AsyncClient is not None
    assert CallContext is not None
    assert Client is not None
    assert Session is not None


def test_model_imports() -> None:
    from unpod.models import (
        Call,
        CallCreate,
        CallMetrics,
        CostBreakdown,
        Number,
        Pipe,
        PipeCreate,
        Recording,
        RunnerStats,
        TokenUsage,
        Transcript,
        VoiceProfile,
    )

    assert Pipe is not None
    assert PipeCreate is not None
    assert Call is not None
    assert CallCreate is not None
    assert CallMetrics is not None
    assert CostBreakdown is not None
    assert Number is not None
    assert Recording is not None
    assert RunnerStats is not None
    assert TokenUsage is not None
    assert Transcript is not None
    assert VoiceProfile is not None


def test_adapter_imports() -> None:
    from unpod.adapters import (
        DialogAdapter,
        HTTPAdapter,
        LangChainAdapter,
        SuperDialogAdapter,
    )

    assert DialogAdapter is not None
    assert HTTPAdapter is not None
    assert LangChainAdapter is not None
    assert SuperDialogAdapter is not None


def test_connectivity_imports() -> None:
    from unpod.connectivity import (
        AgentRunner,
        CallContext,
        HookRegistry,
        MetricsTracker,
        Session,
    )

    assert AgentRunner is not None
    assert CallContext is not None
    assert HookRegistry is not None
    assert MetricsTracker is not None
    assert Session is not None


def test_protocol_imports() -> None:
    from unpod._protocol import (
        AgentEndCallVerb,
        AgentSayVerb,
        AgentTextDeltaEvent,
        AgentTextEndEvent,
        AgentTransferVerb,
        Dispatch,
        DispatchAck,
        Heartbeat,
        HelloAckEvent,
        HelloEvent,
        JobCompleted,
        Register,
        Registered,
        StateChanged,
        UserTextEvent,
        parse_bridge_event,
        parse_dispatch_frame,
    )

    assert AgentEndCallVerb is not None
    assert AgentSayVerb is not None
    assert AgentTextDeltaEvent is not None
    assert AgentTextEndEvent is not None
    assert AgentTransferVerb is not None
    assert Dispatch is not None
    assert DispatchAck is not None
    assert Heartbeat is not None
    assert HelloAckEvent is not None
    assert HelloEvent is not None
    assert JobCompleted is not None
    assert Register is not None
    assert Registered is not None
    assert StateChanged is not None
    assert UserTextEvent is not None
    assert parse_bridge_event is not None
    assert parse_dispatch_frame is not None


def test_management_subresources() -> None:
    from unpod import Client

    c = Client(api_key="unpod_sk_test", base_url="https://api.example.test")
    assert hasattr(c, "numbers")
    assert hasattr(c, "voice_profiles")
    assert hasattr(c, "pipes")
    assert hasattr(c, "calls")
    assert hasattr(c, "recordings")
    assert hasattr(c, "transcripts")


def test_async_client_subresources() -> None:
    from unpod import AsyncClient

    c = AsyncClient(api_key="unpod_sk_test", base_url="https://api.example.test")
    assert hasattr(c, "numbers")
    assert hasattr(c, "voice_profiles")
    assert hasattr(c, "pipes")
    assert hasattr(c, "calls")
    assert hasattr(c, "recordings")
    assert hasattr(c, "transcripts")
