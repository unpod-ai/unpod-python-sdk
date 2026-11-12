"""Answer server + stream endpoint for a Pipecat bot on Unpod calls.

Routes:
    POST/GET /answer   SuperSBC asks what to do with a call → stream it to /ws
    WS       /ws       SuperSBC streams the call's audio; the bot answers
    POST     /start    place an outbound call that streams to the bot

Setup, once per number (inbound):
    await client.telephony.inbound.set_did(
        "+911234567890", answer_url="https://<your-host>/answer")

Env:
    UNPOD_PLATFORM_TOKEN, UNPOD_ORG_HANDLE   SDK auth (org needs the call API on)
    UNPOD_WEBHOOK_SECRET                     client.telephony.webhook_secret
    UNPOD_FROM_NUMBER                        caller ID for outbound calls
    OPENAI_API_KEY, DEEPGRAM_API_KEY, CARTESIA_API_KEY

Run:
    pip install "unpod[pipecat]" "pipecat-ai[websocket,silero,deepgram,openai,cartesia]" uvicorn
    python server.py   # behind https/wss, e.g. ngrok http 7860
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

import uvicorn
from bot import run_bot
from fastapi import FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import Response

from unpod import AsyncClient
from unpod.pipecat import parse_unpod_websocket, verify_stream_request
from unpod.telephony.answer import Response as Answer

WEBHOOK_SECRET = os.environ["UNPOD_WEBHOOK_SECRET"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncClient() as client:
        app.state.unpod = client
        yield


app = FastAPI(lifespan=lifespan)


def _ws_url(request: Request, direction: str) -> str:
    host = request.headers.get("host")
    if not host:
        raise HTTPException(400, "no Host header")
    return f"wss://{host}/ws?direction={direction}"


@app.api_route("/answer", methods=["GET", "POST"])
async def answer(request: Request, direction: str = "inbound") -> Response:
    """Tell the SBC to stream this call to our bot. Plivo-style XML, so an
    existing Plivo answer server only needs its ``<Stream>`` URL changed."""
    xml = Answer().stream(_ws_url(request, direction)).to_xml()
    return Response(content=xml, media_type="application/xml")


@app.websocket("/ws")
async def stream(websocket: WebSocket, direction: str = "inbound"):
    # Only Unpod's SBC holds the org's webhook secret.
    if not verify_stream_request(websocket.headers, WEBHOOK_SECRET):
        await websocket.close(code=1008)
        return
    await websocket.accept()
    start = await parse_unpod_websocket(websocket)
    await run_bot(websocket, start, app.state.unpod, greet=direction == "inbound")


@app.post("/start")
async def start_outbound(request: Request) -> dict:
    """Dial ``{"phone_number": "+1..."}``; once answered, the call streams to /ws."""
    body = await request.json()
    to = body.get("phone_number")
    if not to:
        raise HTTPException(400, "phone_number is required")
    host = request.headers["host"]
    call = await app.state.unpod.telephony.calls.create(
        os.environ["UNPOD_FROM_NUMBER"],
        to,
        answer_url=f"https://{host}/answer?direction=outbound",
    )
    return {"call_id": call.call_id, "state": call.state}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=7860)
