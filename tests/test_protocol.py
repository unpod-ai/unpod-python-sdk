import json

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
    StateChanged,
    UserTextEvent,
    parse_bridge_event,
    parse_dispatch_frame,
)


def test_register_frame():
    r = Register(
        worker_id="w1",
        pool="default",
        capabilities={"voice_profiles": ["hi-female"], "max_concurrent": 50},
    )
    data = json.loads(r.model_dump_json())
    assert data["type"] == "register"


def test_register_carries_agent_id_and_serving_url():
    reg = Register(
        worker_id="ag-x#1",
        pool="ag-x",
        capabilities={
            "agent_id": "ag-x",
            "serving_url": "wss://r1/agent",
            "voice_profiles": [],
            "max_concurrent": 50,
        },
    )
    parsed = parse_dispatch_frame(reg.model_dump_json())
    assert isinstance(parsed, Register)
    caps = parsed.capabilities
    got = caps.get("serving_url") if isinstance(caps, dict) else caps.serving_url
    assert got == "wss://r1/agent"


def test_parse_dispatch_frame():
    raw = json.dumps(
        {
            "type": "dispatch",
            "job_id": "j1",
            "session_id": "s1",
            "room": {},
            "voice_profile_id": "vp_1",
            "runner_url": "ws://localhost:8000/agent",
            "agent_secret": "secret",
            "metadata": {},
        }
    )
    frame = parse_dispatch_frame(raw)
    assert isinstance(frame, Dispatch)
    assert frame.job_id == "j1"


def test_user_text_event():
    evt = UserTextEvent(text="hello", is_final=True)
    data = json.loads(evt.model_dump_json())
    assert data["event"] == "user.text"


def test_parse_bridge_event():
    raw = json.dumps({"event": "user.text", "text": "hi", "is_final": True})
    evt = parse_bridge_event(raw)
    assert isinstance(evt, UserTextEvent)


def test_hello_handshake():
    hello = HelloEvent(
        protocol_version="2",
        supported_events=["user.text"],
        supported_verbs=["agent.text.delta"],
    )
    assert hello.event == "hello"


def test_dispatch_ack():
    ack = DispatchAck(job_id="j1", accepted=True)
    data = json.loads(ack.model_dump_json())
    assert data["type"] == "dispatch.ack"
    assert data["accepted"] is True


def test_heartbeat():
    hb = Heartbeat(active_jobs=5)
    data = json.loads(hb.model_dump_json())
    assert data["type"] == "heartbeat"


def test_agent_say_verb():
    verb = AgentSayVerb(text="Please hold")
    data = json.loads(verb.model_dump_json())
    assert data["event"] == "agent.say"


def test_agent_transfer_verb():
    verb = AgentTransferVerb(
        transfer_type="human",
        target="tier2",
        mode="cold",
    )
    data = json.loads(verb.model_dump_json())
    assert data["event"] == "agent.transfer"


def test_agent_end_call_verb():
    verb = AgentEndCallVerb(reason="completed")
    data = json.loads(verb.model_dump_json())
    assert data["event"] == "agent.end_call"


def test_agent_text_delta():
    evt = AgentTextDeltaEvent(text="hel")
    data = json.loads(evt.model_dump_json())
    assert data["event"] == "agent.text.delta"


def test_agent_text_end():
    evt = AgentTextEndEvent()
    data = json.loads(evt.model_dump_json())
    assert data["event"] == "agent.text.end"


def test_state_changed():
    sc = StateChanged(job_id="j1", state="connected")
    data = json.loads(sc.model_dump_json())
    assert data["type"] == "state.changed"


def test_job_completed():
    jc = JobCompleted(job_id="j1", final_state="ended", duration_s=42.5)
    data = json.loads(jc.model_dump_json())
    assert data["type"] == "job.completed"


def test_parse_bridge_event_agent_say():
    raw = json.dumps({"event": "agent.say", "text": "hold on"})
    evt = parse_bridge_event(raw)
    assert isinstance(evt, AgentSayVerb)


def test_hello_ack():
    ack = HelloAckEvent(
        negotiated_events=["user.text"],
        negotiated_verbs=["agent.text.delta"],
        call_id="c1",
        session_id="s1",
        job_id="j1",
        room_id="r1",
    )
    assert ack.event == "hello.ack"


def test_bridge_frames_use_event_discriminator() -> None:
    from unpod._protocol import HelloEvent, parse_bridge_event

    hello = HelloEvent(protocol_version="2", supported_events=[], supported_verbs=[])
    dumped = hello.model_dump()
    assert dumped["event"] == "hello" and "type" not in dumped
    assert isinstance(parse_bridge_event(hello.model_dump_json()), HelloEvent)
