"""v2 dispatch frames: job.assign / job.ack / job.cancel round-trips."""

from unpod._protocol import (
    JobAck,
    JobAssign,
    JobCancel,
    Registered,
    parse_dispatch_frame,
)


def test_job_assign_round_trip() -> None:
    frame = JobAssign(
        job_id="j1",
        call_id="s1",
        agent_id="ag-x",
        bridge_url="wss://bridge.example/w/w1/call/s1",
        call_token="tok",
        deadline_ms=2000,
        metadata={"playbook_id": "pb1"},
    )
    parsed = parse_dispatch_frame(frame.model_dump_json())
    assert isinstance(parsed, JobAssign)
    assert parsed.bridge_url.endswith("/call/s1")
    assert parsed.metadata["playbook_id"] == "pb1"


def test_job_ack_round_trip() -> None:
    ok = parse_dispatch_frame(JobAck(job_id="j1", accepted=True).model_dump_json())
    assert isinstance(ok, JobAck) and ok.accepted
    no = parse_dispatch_frame(
        JobAck(job_id="j1", accepted=False, reason="at_capacity").model_dump_json()
    )
    assert isinstance(no, JobAck) and no.reason == "at_capacity"


def test_job_cancel_round_trip() -> None:
    parsed = parse_dispatch_frame(JobCancel(job_id="j1").model_dump_json())
    assert isinstance(parsed, JobCancel)


def test_registered_transport_ack_optional() -> None:
    # Old orchestrators omit transport_ack — must still parse.
    parsed = parse_dispatch_frame('{"type":"registered","heartbeat_interval_s":30}')
    assert isinstance(parsed, Registered)
    assert parsed.transport_ack is None
