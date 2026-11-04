#!/usr/bin/env python3
"""End-to-end check of the Telephony Call API through SDK -> Unpod -> SuperSBC
(UNPO-186). The sibling of SuperSBC's own ``scripts/telephony_call_api_e2e.py``,
which calls the SBC directly and so never exercises the Unpod proxy at all.

Proves the attached "Plivo Hotline Event Map - Vahan AI.html" end to end: every
call goes through ``client.telephony.calls``/``inbound``/``webhook_secret``
(this SDK), never a raw SuperSBC URL. A local webhook receiver collects the
delivered events and verifies their signature with ``parse_call_event`` and
the org's real secret, exactly as a customer's receiver would.

Use ONLY the test number and test SBC named in the runbook -- never Prod.

Environment (all required unless noted):
  UNPOD_PLATFORM_TOKEN, UNPOD_ORG_HANDLE   org A's SDK credentials
  UNPOD_BASE_URL (or UNPOD_SERVICE_BASE_URL)  the Unpod deployment under test
  FROM                  org A's DID (telephony_call_api on, human_call or
                        agent-capable; UNPO-184/185 must be deployed there)
  TO_A, TO_B            two reachable numbers (party A, party B)
  TO_BUSY               a number that answers 486/600
  TO_NOANSWER           a number that rings out
  RECEIVER_URL          URL the SBC can reach for this machine's receiver
  RECEIVER_PORT         local port (default 8099)
  WEBHOOK_SECRET        org A's full (unmasked) webhook secret -- fetch once
                        with ``client.telephony.webhook_secret.rotate()`` and
                        reuse; rotating on every run invalidates it for any
                        concurrent receiver
  PLAY_URL              https audio URL (optional; skips scenario 4 when unset)
  INBOUND_DID           a DID with an answer_url -> RECEIVER_URL/answer
                        (optional; skips scenario 6 when unset)
  UNPOD_PLATFORM_TOKEN_2, UNPOD_ORG_HANDLE_2, TO_A_2   org B's credentials and
                        one of its own DIDs (optional; skips scenario 7's
                        cross-org checks when unset)

Run: python3 examples/telephony_call_api_e2e_sdk.py
Exit status is non-zero if any check fails.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from unpod import AsyncClient
from unpod.management._auth import TokenAuth
from unpod.telephony import TelephonyAPIError
from unpod.telephony.events import CallEvent, WebhookSignatureError, parse_call_event

ENV = os.environ
FAILS: list[str] = []
EVENTS: list[tuple[float, CallEvent]] = []
STARTAPPS: list[tuple[float, CallEvent]] = []
LOCK = threading.Lock()


def check(cond: bool, msg: str) -> None:
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


class Receiver(BaseHTTPRequestHandler):
    def log_message(self, *a):  # noqa: D401 - silence BaseHTTPRequestHandler's own logging
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            ev = parse_call_event(body, self.headers, ENV.get("WEBHOOK_SECRET"))
        except WebhookSignatureError as exc:
            check(False, f"signature verification failed on {self.path}: {exc}")
            ev = parse_call_event(body, self.headers, None, verify=False)
        with LOCK:
            (STARTAPPS if self.path.startswith("/answer") else EVENTS).append((time.time(), ev))
        self.send_response(200)
        self.end_headers()


def events_for(call_id: str) -> list[tuple[float, CallEvent]]:
    with LOCK:
        return [(t, e) for t, e in EVENTS if e.call_id == call_id]


def wait_for(call_id: str, name: str, leg_suffix: str | None = None, timeout: float = 45):
    end = time.time() + timeout
    while time.time() < end:
        for t, e in events_for(call_id):
            if e.event == name and (leg_suffix is None or (e.leg_id or "").endswith(leg_suffix)):
                return t, e
        time.sleep(0.2)
    return None, None


def order_ok(call_id: str) -> bool:
    want = ["Initiated", "Ringing", "Answered", "Hangup"]
    legs: dict[str, list[str]] = {}
    for _, e in events_for(call_id):
        if e.event in want:
            legs.setdefault(e.leg_id or "", []).append(e.event)
    return bool(legs) and all(
        v[0] == "Initiated" and v == sorted(v, key=want.index) and v.count("Hangup") <= 1
        for v in legs.values()
    )


async def scenario_start_call_and_add_customer(client: AsyncClient, lat: dict) -> None:
    """Scenario 1+2: Start Call (agent), customer dialed on Answered, signed events in order."""
    calls = client.telephony.calls
    call = await calls.create(
        ENV["FROM"],
        ENV["TO_A"],
        role="agent",
        status_callback_url=ENV["RECEIVER_URL"] + "/events",
        metadata={"ticket": "e2e-sdk"},
    )
    check(call.call_id is not None, "calls.create -> a call_id")
    cid = call.call_id
    init_ev_recv_time, init_ev = wait_for(cid, "Initiated")
    check(init_ev is not None, "Initiated delivered, signature verified")
    if init_ev:
        check(
            all([init_ev.call_id, init_ev.leg_id, init_ev.role, init_ev.event_id, init_ev.sequence]),
            "event has contract fields (call_id/leg_id/role/event_id/sequence)",
        )
    _, ans = wait_for(cid, "Answered", "-callee")
    check(ans is not None, "party A (agent) answered")
    try:
        leg = await calls.add_participant(
            cid, ENV["FROM"], ENV["TO_B"], role="customer",
            status_callback_url=ENV["RECEIVER_URL"] + "/events",
        )
        check(leg.leg_id is not None, "add_participant (customer) -> a leg_id")
    except TelephonyAPIError as exc:
        check(False, f"add_participant failed: {exc.status_code} {exc.code}")
    try:
        await calls.add_participant(cid, ENV["FROM"], ENV["TO_B"])
        check(False, "a third participant must be refused")
    except TelephonyAPIError as exc:
        check(exc.status_code == 409, f"third participant -> 409 (got {exc.status_code})")
    wait_for(cid, "Answered", timeout=45)
    await calls.end(cid)
    _, hu = wait_for(cid, "Hangup")
    check(hu is not None and hu.hangup_cause, "Hangup with hangup_cause, signature verified")
    check(order_ok(cid), "events in order Initiated < [Ringing] < Answered < Hangup, per leg")
    if init_ev and init_ev.timestamp is not None:
        lat["initiated_delivery_s"].append(init_ev_recv_time - init_ev.timestamp)


async def scenario_failure_endings(client: AsyncClient) -> None:
    """Scenario 3: Busy Line / Ring Timeout Reached both lead to calls.end being reachable."""
    calls = client.telephony.calls
    for to, cause, label in (
        (ENV["TO_BUSY"], "Busy Line", "busy"),
        (ENV["TO_NOANSWER"], "Ring Timeout Reached", "ring timeout"),
    ):
        call = await calls.create(
            ENV["FROM"], to, ring_timeout=15,
            status_callback_url=ENV["RECEIVER_URL"] + "/events",
        )
        _, hu = wait_for(call.call_id, "Hangup", timeout=60)
        check(
            hu is not None and hu.hangup_cause == cause,
            f"{label}: Hangup cause == {cause!r} (got {hu and hu.hangup_cause!r})",
        )
        try:
            await calls.end(call.call_id)
        except TelephonyAPIError:
            pass  # already ended by the carrier; calls.end on an ended call is UNPO-182's own check


async def scenario_wait_tune_one_leg(client: AsyncClient) -> None:
    """Scenario 4: looped wait tune on one leg, stopped on that leg.

    Whether playback reaches participant B is its own, never-live-tested
    session (noted explicitly in the ticket) -- this only proves the SDK/proxy
    round-trip (play_id issued, stop accepted), not what the far end heard.
    """
    if not ENV.get("PLAY_URL"):
        print("SKIP wait tune (PLAY_URL not set)")
        return
    calls = client.telephony.calls
    call = await calls.create(ENV["FROM"], ENV["TO_A"], role="agent")
    try:
        played = await calls.play_audio(call.call_id, url=ENV["PLAY_URL"], leg_id="caller", loop=True)
        check(played.play_id is not None, "play_audio (loop) on one leg -> a play_id")
        await asyncio.sleep(3)
        await calls.stop_audio(call.call_id, leg_id="caller")
        check(True, "stop_audio on that leg accepted")
        print(
            "NOTE: whether participant B's leg (a separate session) ever heard "
            "this loop is UNVERIFIED -- requires a second live listener, not "
            "just the REST round-trip this scenario checks."
        )
    finally:
        await calls.end(call.call_id)


async def scenario_recording_and_download(client: AsyncClient) -> None:
    """Scenario 5: recording callback, then the WAV downloaded through the proxy."""
    calls = client.telephony.calls
    call = await calls.create(
        ENV["FROM"], ENV["TO_A"], role="agent", record=True,
        recording_callback_url=ENV["RECEIVER_URL"] + "/events",
        status_callback_url=ENV["RECEIVER_URL"] + "/events",
    )
    wait_for(call.call_id, "Answered", timeout=45)
    await asyncio.sleep(5)
    await calls.end(call.call_id)
    _, rec_ev = wait_for(call.call_id, "MPCRecordingCompleted", timeout=30)
    check(rec_ev is not None, "MPCRecordingCompleted delivered, signature verified")
    try:
        recording = await calls.get_recording(call.call_id)
        check(recording.available, "get_recording reports available")
    except TelephonyAPIError as exc:
        check(False, f"get_recording failed: {exc.status_code} {exc.code}")
        return
    # No SDK download helper yet (UNPO-183 deferred item) -- authenticate the
    # same way the SDK's own HTTP client does, through the proxy, never a raw
    # SBC URL.
    import httpx

    headers = {"Accept": "audio/wav"}
    if ENV.get("UNPOD_PLATFORM_TOKEN"):
        headers["Authorization"] = f"Token {ENV['UNPOD_PLATFORM_TOKEN']}"
        if ENV.get("UNPOD_ORG_HANDLE"):
            headers["Org-Handle"] = ENV["UNPOD_ORG_HANDLE"]
    # recording.url is already absolute (the proxy builds it from the
    # request's own host, UNPO-182); only prefix it if it ever comes back
    # relative instead.
    download_url = recording.url
    if download_url and not download_url.startswith(("http://", "https://")):
        from unpod._base_url import http_base

        download_url = (http_base() or "").rstrip("/") + download_url
    async with httpx.AsyncClient() as http:
        resp = await http.get(download_url, headers=headers, timeout=30)
    check(
        resp.status_code == 200 and resp.headers.get("content-type", "").startswith("audio/"),
        f"WAV download through the proxy -> 200 audio/* (got {resp.status_code}, "
        f"{resp.headers.get('content-type')!r})",
    )
    check(resp.content[:4] == b"RIFF", "downloaded bytes are a real WAV (RIFF header)")


async def scenario_incoming_call(client: AsyncClient) -> None:
    """Scenario 6: Incoming Call -> StartApp with Record+Dial, or Speak+Hangup when no agent."""
    if not ENV.get("INBOUND_DID"):
        print("SKIP incoming call (INBOUND_DID not set)")
        return
    inbound = client.telephony.inbound
    cfg = await inbound.get_did(ENV["INBOUND_DID"])
    check(
        cfg.answer_url == ENV["RECEIVER_URL"] + "/answer",
        "inbound.get_did reads back exactly what the runbook configured",
    )
    start = time.time()
    call = await client.telephony.calls.create(ENV["FROM"], ENV["INBOUND_DID"])
    check(call.call_id is not None, "placed a call to the answer-URL DID")
    end = time.time() + 30
    while time.time() < end and not STARTAPPS:
        time.sleep(0.2)
    check(bool(STARTAPPS), "StartApp POST reached the answer URL, signature verified")
    if STARTAPPS:
        t, sa = STARTAPPS[-1]
        check(sa.event == "StartApp" and sa.call_status in ("ringing", None), "StartApp fields")
        print(
            f"LATENCY answer_url_seen_after_post_s: {t - start:.3f}s "
            "(Record+Dial expected with an agent available, else Speak+Hangup)"
        )
    _, hu = wait_for(call.call_id, "Hangup", timeout=30)
    check(hu is not None, "Hangup delivered for the inbound call")
    try:
        await client.telephony.calls.end(call.call_id)
    except TelephonyAPIError:
        pass


async def scenario_isolation(client: AsyncClient) -> None:
    """Scenario 7: another org never sees org A's call, numbers, or its from.

    "Flag off -> 403" (the ticket's fourth isolation check) is not covered
    here: SbcCallApiSettings.call_api_enabled is an Unpod admin-only toggle,
    nothing the SDK exposes to flip for this run. Check it by hand: disable
    the flag for org A in the admin, retry calls.create, expect 403
    call_api_disabled, then re-enable it before anything else in this script
    runs.
    """
    calls = client.telephony.calls
    call = await calls.create(ENV["FROM"], ENV["TO_A"], role="agent")
    try:
        if not (ENV.get("UNPOD_PLATFORM_TOKEN_2") and ENV.get("UNPOD_ORG_HANDLE_2")):
            print("SKIP cross-org isolation (UNPOD_PLATFORM_TOKEN_2/UNPOD_ORG_HANDLE_2 not set)")
            return
        auth_b = TokenAuth(token=ENV["UNPOD_PLATFORM_TOKEN_2"], org_handle=ENV["UNPOD_ORG_HANDLE_2"])
        async with AsyncClient(auth=auth_b) as client_b:
            try:
                await client_b.telephony.calls.get(call.call_id)
                check(False, "org B must not see org A's call")
            except TelephonyAPIError as exc:
                check(exc.status_code == 404, f"org B get -> 404 (got {exc.status_code})")
            listed = await client_b.telephony.calls.list()
            check(
                all(c.call_id != call.call_id for c in listed.items),
                "org A's call never appears in org B's list",
            )
            if ENV.get("TO_A_2"):
                try:
                    await client_b.telephony.calls.create(ENV["FROM"], ENV["TO_A_2"])
                    check(False, "org B dialing org A's from number must be refused")
                except TelephonyAPIError as exc:
                    check(
                        exc.code == "from_not_owned",
                        f"org B using org A's from -> from_not_owned (got {exc.code})",
                    )
    finally:
        try:
            await calls.end(call.call_id)
        except TelephonyAPIError:
            pass


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else float("nan")


async def main() -> None:
    required = (
        "UNPOD_PLATFORM_TOKEN", "UNPOD_ORG_HANDLE", "FROM", "TO_A", "TO_B",
        "TO_BUSY", "TO_NOANSWER", "RECEIVER_URL", "WEBHOOK_SECRET",
    )
    missing = [k for k in required if not ENV.get(k)]
    if missing:
        sys.exit("missing env: " + ", ".join(missing))

    srv = HTTPServer(("0.0.0.0", int(ENV.get("RECEIVER_PORT", "8099"))), Receiver)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    lat: dict[str, list[float]] = {"initiated_delivery_s": []}
    async with AsyncClient() as client:
        await scenario_start_call_and_add_customer(client, lat)
        await scenario_failure_endings(client)
        await scenario_wait_tune_one_leg(client)
        await scenario_recording_and_download(client)
        await scenario_incoming_call(client)
        await scenario_isolation(client)

    for name, xs in lat.items():
        if xs:
            print(f"LATENCY {name}: n={len(xs)} p50={statistics.median(xs):.3f}s p95={pct(xs, 0.95):.3f}s")
    if ENV.get("DEBUG"):
        for t, e in EVENTS:
            print("EVENT", json.dumps({
                "call_id": e.call_id, "leg_id": e.leg_id, "event": e.event,
                "sequence": e.sequence, "hangup_cause": e.hangup_cause,
                "sip_code": e.sip_code, "role": e.role,
            }))
    print("RESULT:", "FAIL" if FAILS else "PASS", f"({len(FAILS)} failed)")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    asyncio.run(main())
