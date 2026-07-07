"""Tests for unpod.models Pydantic models."""

from unpod.models import (
    Call,
    CallCreate,
    CallMetrics,
    CostBreakdown,
    Number,
    OrchestratorSession,
    Pipe,
    PipeCreate,
    Recording,
    RunnerStats,
    TokenUsage,
    Transcript,
    TranscriptTurn,
    TurnTiming,
    VoiceProfile,
)


def test_number_model():
    n = Number(
        id="num_1",
        number="+919800000001",
        country="IN",
        capabilities=["voice"],
        status="active",
    )
    assert n.number == "+919800000001"


def test_number_optional_created_at():
    n = Number(
        id="num_1",
        number="+919800000001",
        country="IN",
        capabilities=["voice"],
        status="active",
    )
    assert n.created_at is None



def test_voice_profile_model():
    vp = VoiceProfile(
        id="vp_1",
        persona="hindi-female-warm-hd",
        language="hi",
        quality_tier="hd",
        price_per_minute=2.50,
        latency_p95_ms=240,
    )
    assert vp.persona == "hindi-female-warm-hd"


def test_agent_create_runner_mode():
    ac = PipeCreate(
        name="kyc-bot",
        voice_profile="vp_1",
        number="+919800000001",
        agent_id="kyc-bot",
        first_speaker="agent",
    )
    assert ac.agent_id == "kyc-bot"
    assert ac.agent_endpoint is None


def test_agent_create_endpoint_mode():
    ac = PipeCreate(
        name="kyc-bot",
        voice_profile="vp_1",
        number="+919800000001",
        agent_endpoint="wss://example.com/agent",
        first_speaker="agent",
    )
    assert ac.agent_endpoint == "wss://example.com/agent"


def test_agent_model():
    a = Pipe(
        pipe_id="pipe_1",
        project_id="proj_1",
        name="kyc-bot",
        voice_profile="vp_1",
        number="+919800000001",
        agent_id="kyc-bot",
        first_speaker="agent",
        fillers={},
        recording={},
        status="active",
        created="2025-05-25T10:00:00Z",
        modified="2025-05-25T10:00:00Z",
    )
    assert a.pipe_id == "pipe_1"
    assert a.status == "active"


def test_call_create():
    cc = CallCreate(
        agent="agt_1",
        user_number="+919800000002",
        instructions="Speak Hindi",
        data={"customer_id": "C1"},
    )
    assert cc.agent == "agt_1"


def test_call_model():
    c = Call(
        id="call_1",
        agent_id="agt_1",
        user_number="+919800000002",
        status="completed",
        direction="outbound",
        started_at="2026-01-01T00:00:00Z",
    )
    assert c.id == "call_1"
    assert c.duration_s is None
    assert c.ended_at is None


def test_recording_model():
    r = Recording(
        id="rec_1",
        call_id="call_1",
        duration_s=47.2,
        format="wav",
        size_bytes=1024000,
    )
    assert r.format == "wav"


def test_transcript_model():
    t = Transcript(
        call_id="call_1",
        turns=[
            TranscriptTurn(
                speaker="agent",
                text="Hello",
                timestamp_ms=0,
                timing=TurnTiming(
                    audio_ingress_ms=10,
                    stt_ms=180,
                    bridge_to_dev_ms=40,
                    dev_brain_ms=300,
                    tts_ms=200,
                ),
            ),
        ],
    )
    assert len(t.turns) == 1
    assert t.turns[0].timing.dev_brain_ms == 300


def test_call_metrics():
    m = CallMetrics(
        duration_s=47.2,
        turns=8,
        stt_p95_ms=320,
        llm_p95_ms=680,
        tts_p95_ms=240,
        cost=CostBreakdown(voice=1.96, llm=0.04, total=2.00),
        tokens=TokenUsage(input=1840, output=320),
        active_llm="anthropic/claude-haiku-4-5",
    )
    assert m.cost.total == 2.00


def test_runner_stats():
    rs = RunnerStats(
        in_flight=3,
        queued=5,
        completed_last_hour=42,
        failed_last_hour=1,
        capacity=10,
        mean_call_duration_s=47.5,
    )
    assert rs.in_flight == 3
    assert rs.capacity == 10


def test_orchestrator_session_parses_orchestrator_shape():
    s = OrchestratorSession(
        session_id="sess-1",
        tenant_id="tenant-1",
        state="active",
        job_id="job-1",
        room_id="room-1",
        participants=[{"participant_id": "p1", "type": "caller"}],
    )
    assert s.state == "active"
    assert s.participants[0].participant_id == "p1"
