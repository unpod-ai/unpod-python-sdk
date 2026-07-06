"""
Test call to +918847348129 using the unpod SDK.

Usage:
    # Step 1 — one-time setup (creates pipe if not exists)
    uv run python examples/test_call.py --setup

    # Step 2 — start the agent runner (keep this running in a terminal)
    uv run python examples/test_call.py --run

    # Step 3 — trigger the outbound call (separate terminal)
    uv run python examples/test_call.py --call

    # Check call status / transcript after call ends
    uv run python examples/test_call.py --status <call_id>

Required env vars:
    UNPOD_API_KEY
    UNPOD_SERVICE_BASE_URL   (e.g. http://localhost:8000/platform)
    UNPOD_ORCHESTRATOR_URL   (e.g. ws://localhost:8000)
    OPENAI_API_KEY           (used by the agent brain)
"""

from __future__ import annotations

import asyncio
import sys

from unpod import AgentRunner, AsyncClient, CallContext

# ── Config ────────────────────────────────────────────────────────────────────

TO_NUMBER = "+918847348129"
PIPE_NAME = "test-call-pipe"
AGENT_ID = "test-call-agent"
RUNNER_BRIDGE_URL = "ws://localhost:8765"  # where speech worker connects

# ── Setup: voice profile + pipe ───────────────────────────────────────────────


async def setup() -> None:
    client = AsyncClient()

    print("[1/2] Fetching voice profiles...")
    profiles = await client.voice_profiles.list()
    if not profiles:
        print("  No voice profiles found. Is supervoice running and seeded?")
        await client.close()
        return

    profile = profiles[0]
    print(f"  Using profile: {profile.name}")

    print(f"\n[2/2] Creating pipe '{PIPE_NAME}'...")
    pipes = await client.pipes.list()
    pipe = next((p for p in pipes if p.name == PIPE_NAME), None)

    if pipe is None:
        pipe = await client.pipes.create(
            name=PIPE_NAME,
            voice_profile=profile.name,
            agent_id=AGENT_ID,
            recording=True,
            max_call_duration_s=300,
        )
        print(f"  Created pipe: {pipe.pipe_id}")
    else:
        print(f"  Pipe already exists: {pipe.pipe_id}")

    print(f"\nSetup done. pipe_id={pipe.pipe_id}")
    print("Run --run in one terminal, then --call in another.")
    await client.close()


# ── Agent entrypoint ──────────────────────────────────────────────────────────


async def entrypoint(ctx: CallContext) -> None:
    """Called once per call."""
    import traceback

    try:
        from superdialog import LLMAgent  # type: ignore[import-not-found]

        ctx.session.dialog_machine = LLMAgent(
            llm="openai/gpt-4o-mini",
            system_prompt=(
                "You are a friendly test assistant. "
                "Greet the caller, confirm this is a test call from the team, "
                "ask if they can hear you clearly, then say goodbye. "
                "Keep every reply under two sentences — your words will be spoken aloud."
            ),
        )
        await ctx.session.say(
            "Hello! This is a test call from the engineering team. Can you hear me clearly?"
        )
        await ctx.session.run()

    except Exception as exc:
        print(f"[ENTRYPOINT ERROR] {type(exc).__name__}: {exc}")
        traceback.print_exc()
        raise


def run_agent() -> None:
    print(
        f"Starting AgentRunner (agent_id={AGENT_ID!r}, bridge={RUNNER_BRIDGE_URL})..."
    )
    print("Press Ctrl-C to stop.\n")
    AgentRunner(
        entrypoint=entrypoint,
        agent_id=AGENT_ID,
        serving_url=RUNNER_BRIDGE_URL,
        max_concurrent_calls=1,
    ).start()


# ── Trigger outbound call ─────────────────────────────────────────────────────


async def make_call() -> None:
    client = AsyncClient()

    pipes = await client.pipes.list()
    pipe = next((p for p in pipes if p.name == PIPE_NAME), None)
    if pipe is None:
        print(f"Pipe '{PIPE_NAME}' not found — run --setup first.")
        await client.close()
        return

    print(f"Dispatching call to {TO_NUMBER} via pipe {pipe.pipe_id}...")
    call = await client.calls.create(
        pipe_id=pipe.pipe_id,
        to_number=TO_NUMBER,
        instructions="This is a test call. Greet warmly and confirm audio quality.",
    )
    print("\nCall dispatched!")
    print(f"  call_id : {call.call_id}")
    print(f"  status  : {call.status}")
    print(
        f"\nCheck status: uv run python examples/test_call.py --status {call.call_id}"
    )
    await client.close()


# ── Check status / transcript ─────────────────────────────────────────────────


async def check_status(call_id: str) -> None:
    client = AsyncClient()

    call = await client.calls.get(call_id)
    print(f"Call {call_id}:")
    print(f"  status     : {call.status}")
    print(f"  from       : {call.from_number}")
    print(f"  to         : {call.to_number}")
    print(f"  duration   : {getattr(call, 'duration_s', 'n/a')}s")

    try:
        transcript = await client.transcripts.get(call_id)
        print(f"\nTranscript:\n{transcript}")
    except Exception:
        print("\nTranscript not available yet.")

    await client.close()


# ── Entrypoint ────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args:
        print(__doc__)
        sys.exit(0)

    if "--setup" in args:
        asyncio.run(setup())
    elif "--run" in args:
        run_agent()
    elif "--call" in args:
        asyncio.run(make_call())
    elif "--status" in args:
        idx = args.index("--status")
        if idx + 1 >= len(args):
            print("Usage: test_call.py --status <call_id>")
            sys.exit(1)
        asyncio.run(check_status(args[idx + 1]))
    else:
        print(f"Unknown arg: {args[0]}")
        print("Use --setup | --run | --call | --status <call_id>")
        sys.exit(1)
