"""Full agent setup — developer quickstart.

Shows the complete flow from zero to a live outbound call:

    Step 1  Pick a voice profile from the catalog
    Step 2  Create a Speech Pipe
    Step 3  Sync numbers from your SIP trunk
    Step 4  Attach a number to the Speech Pipe
    Step 5  Start the AgentRunner (handles inbound + dispatched outbound)
    Step 6  Make an outbound call
    Step 7  Monitor — list active calls, transcripts, recordings

Run:
    # Terminal 1 — start supervoice (needs Mongo; mint a key: task create-api-key)
    cd supervoice && uv run uvicorn supervoice.main:app --port 8000 --reload

    # For outbound calls (--call) also run, in two more terminals:
    #   cd supervoice && task prefect-server
    #   cd supervoice && task queue-serve      # needs Redis (SUPERVOICE_REDIS_URL)

    # Terminal 2 — setup + runner
    export UNPOD_API_KEY="sk_..."
    export UNPOD_SERVICE_BASE_URL="http://localhost:8000/platform"
    export ANTHROPIC_API_KEY="..."

    uv run python examples/full_agent_setup.py --setup
    uv run python examples/full_agent_setup.py --run
    uv run python examples/full_agent_setup.py --call +19995550001
    uv run python examples/full_agent_setup.py --monitor
"""

from __future__ import annotations

import asyncio
import sys

from unpod import AgentRunner, AsyncClient, CallContext

# ── Constants ────────────────────────────────────────────────────────────────

PIPE_NAME = "my-voice-agent"
RUNNER_AGENT_ID = "my-voice-agent"  # ties AgentRunner to the Speech Pipe
LANGUAGE = "en"  # BCP-47 — change to "hi", "mr", etc.


# ── Step 1–4: One-time resource setup ────────────────────────────────────────


async def setup() -> None:
    """Provision voice profile → Speech Pipe.

    Steps 3–4 (number sync + attach) are skipped when no SIP trunk is
    configured — the AgentRunner works over WebSocket without a phone number.
    Safe to re-run — skips steps that are already done.
    """
    client = AsyncClient()

    # ── 1. Pick a voice profile ───────────────────────────────────────────────
    print("\n[1/2] Fetching voice profiles...")
    profiles = await client.voice_profiles.list(language=LANGUAGE)
    if not profiles:
        print("  No voice profiles found. Check that supervoice is running and seeded.")
        return

    profile = profiles[0]
    print(f"  Using: {profile.name}  ({profile.tts_provider}/{profile.tts_voice})")
    print(f"         languages={profile.languages}  latency~{profile.latency_ms}ms")

    # ── 2. Create Speech Pipe (idempotent — reuse if already exists) ──────────
    print(f"\n[2/2] Creating Speech Pipe '{PIPE_NAME}'...")
    pipes = await client.pipes.list()
    pipe = next((p for p in pipes if p.name == PIPE_NAME), None)

    if pipe is None:
        pipe = await client.pipes.create(
            name=PIPE_NAME,
            voice_profile=profile.name,
            agent_id=RUNNER_AGENT_ID,
            recording=True,
            max_call_duration_s=600,
        )
        print(f"  Created: {pipe.pipe_id}")
    else:
        print(f"  Already exists: {pipe.pipe_id}")

    print(f"\nSetup complete  (pipe_id={pipe.pipe_id})")
    print("Run with --run to start the AgentRunner.")
    print("To enable outbound calls: add a SIP trunk, then re-run --setup.")


# ── Step 5: AgentRunner — handles every call ─────────────────────────────────


async def entrypoint(ctx: CallContext) -> None:
    """Called once per call — inbound or outbound."""
    import traceback

    try:
        from superdialog import LLMAgent  # type: ignore[import-not-found]

        ctx.session.dialog_machine = LLMAgent(
            llm="openai/gpt-4o-mini",
            system_prompt=(
                "You are a friendly voice assistant at GameStop. "
                "Tell users about games and console sale options. "
                "Keep every reply under two sentences — your words will be spoken aloud."
            ),
        )
        await ctx.session.say("Hello! How can I help you today?")
        await ctx.session.run()
    except Exception as exc:
        print(f"\n[ENTRYPOINT ERROR] {type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise


def run_agent() -> None:
    """Start the AgentRunner and block until interrupted."""
    print(f"Starting AgentRunner (agent_id={RUNNER_AGENT_ID!r})...")
    print("Press Ctrl-C to stop.\n")
    AgentRunner(
        entrypoint=entrypoint,
        agent_id=RUNNER_AGENT_ID,
        max_concurrent_calls=1,
        # base_url and api_key read from env: UNPOD_SERVICE_BASE_URL / UNPOD_API_KEY
    ).start()


# ── Step 6: Make an outbound call ────────────────────────────────────────────


async def make_call(to_number: str) -> None:
    """Dispatch an outbound call to the given E.164 number."""
    client = AsyncClient()

    pipes = await client.pipes.list()
    pipe = next((p for p in pipes if p.name == PIPE_NAME), None)
    if pipe is None:
        print(f"Speech Pipe '{PIPE_NAME}' not found — run --setup first.")
        return
    if not pipe.number_id:
        print("Speech Pipe has no attached number — run --setup first.")
        return

    call = await client.calls.create(
        pipe_id=pipe.pipe_id,
        to_number=to_number,
        instructions="Greet the user warmly and ask how you can help.",
    )
    print("Outbound call dispatched:")
    print(f"  call_id  : {call.call_id}")
    print(f"  from     : {call.from_number}")
    print(f"  to       : {call.to_number}")
    print(f"  status   : {call.status}")


# ── Step 7: Monitor ──────────────────────────────────────────────────────────


async def monitor() -> None:
    """Print active calls, recent transcripts, and recordings."""
    client = AsyncClient()

    # Active calls
    calls = await client.calls.list()
    active = [c for c in calls if c.status in ("pending", "ringing", "active")]
    print(f"\nActive calls: {len(active)}")
    for c in active:
        print(f"  {c.call_id}  {c.from_number} → {c.to_number}  [{c.status}]")

    # Recent completed calls
    completed = [c for c in calls if c.status == "completed"]
    print(f"\nCompleted calls: {len(completed)}")
    for c in completed[:5]:
        print(f"  {c.call_id}  duration={c.duration_s}s  session={c.session_id}")

    # Transcripts
    transcript_sessions = await client.transcripts.list()
    print(f"\nSessions with transcripts: {len(transcript_sessions)}")
    for s in transcript_sessions[:3]:
        print(f"\n  Session {s.session_id}  (call={s.call_id})")
        for turn in s.transcript:
            ts = turn.timestamp.isoformat() if turn.timestamp else "--"
            print(f"    [{ts}] [{turn.role:5}] {turn.content}")

    # Recordings
    recording_sessions = await client.recordings.list()
    print(f"\nSessions with recordings: {len(recording_sessions)}")
    for s in recording_sessions[:5]:
        print(f"  {s.session_id}  {s.recording_url}")


# ── CLI entry point ───────────────────────────────────────────────────────────


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or "--help" in args:
        print(__doc__)
    elif "--setup" in args:
        asyncio.run(setup())
    elif "--run" in args:
        run_agent()
    elif "--call" in args:
        idx = args.index("--call")
        if idx + 1 >= len(args):
            print("Usage: full_agent_setup.py --call +19995550001")
            sys.exit(1)
        asyncio.run(make_call(args[idx + 1]))
    elif "--monitor" in args:
        asyncio.run(monitor())
    else:
        print(f"Unknown argument: {args[0]}")
        print("Use --setup | --run | --call <number> | --monitor")
        sys.exit(1)
