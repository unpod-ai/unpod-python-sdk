/**
 * pipe-ui — Pipecat WebSocket voice client with realtime transcript + latency.
 *
 * Latency tracked per turn:
 *   STT latency  = final user transcript - first non-final user transcript
 *   TTFA         = first agent text chunk - user final transcript
 *   Total        = agent stopped speaking - user final transcript
 */

import { PipecatClient, PipecatClientOptions, RTVIEvent } from "@pipecat-ai/client-js";
import { WebSocketTransport } from "@pipecat-ai/websocket-transport";

// ── Config ───────────────────────────────────────────────────────────────────

const AGENT_ID = "browser-playground";

// ── DOM helpers ──────────────────────────────────────────────────────────────

const $  = (id: string) => document.getElementById(id)!;
const btnConnect    = $("btn-connect")    as HTMLButtonElement;
const btnDisconnect = $("btn-disconnect") as HTMLButtonElement;
const transcript    = $("transcript");
const logEl         = $("log");

function setDot(id: string, state: "ok" | "warn" | "err" | "") {
  $("dot-" + id).className = "dot" + (state ? " " + state : "");
}

function addLog(msg: string) {
  const ts = new Date().toISOString().slice(11, 23);
  logEl.textContent += `${ts}  ${msg}\n`;
  logEl.scrollTop = logEl.scrollHeight;
}

function fmtMs(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(2)}s`;
}

// ── Turn builder ─────────────────────────────────────────────────────────────

function makeTurn(role: "user" | "agent" | "system"): {
  wrap: HTMLDivElement;
  msg: HTMLDivElement;
} {
  const wrap = document.createElement("div");
  wrap.className = `turn ${role}`;

  if (role !== "system") {
    const label = document.createElement("div");
    label.className = "turn-label";
    label.textContent = role === "user" ? "You" : "Agent";
    wrap.appendChild(label);
  }

  const msg = document.createElement("div");
  msg.className = "msg";
  wrap.appendChild(msg);

  transcript.appendChild(wrap);
  transcript.scrollTop = transcript.scrollHeight;
  return { wrap, msg };
}

function addLatency(wrap: HTMLDivElement, badges: { label: string; cls: string; value: number }[]) {
  const bar = document.createElement("div");
  bar.className = "latency";
  for (const b of badges) {
    const el = document.createElement("span");
    el.className = `badge ${b.cls}`;
    el.textContent = `${b.label} ${fmtMs(b.value)}`;
    bar.appendChild(el);
  }
  wrap.appendChild(bar);
}

function makeThinking(): HTMLDivElement {
  const { wrap, msg } = makeTurn("agent");
  msg.innerHTML = `<span class="thinking-dots"><span>●</span><span>●</span><span>●</span></span>`;
  return wrap;
}

// ── Client state ─────────────────────────────────────────────────────────────

let pcClient: PipecatClient | null = null;

// Latency timestamps
let tSttStart  = 0;   // first non-final user transcript
let tUserFinal = 0;   // final user transcript
let tFirstChunk = 0;  // first agent text chunk

// Live agent turn state
let agentWrap: HTMLDivElement | null = null;
let agentMsg: HTMLDivElement | null  = null;
let agentBuf = "";
let thinkingEl: HTMLDivElement | null = null;

// ── Connect / disconnect ─────────────────────────────────────────────────────

async function connect() {
  btnConnect.disabled = true;

  const options: PipecatClientOptions = {
    transport: new WebSocketTransport(),
    enableMic: true,
    enableCam: false,
    callbacks: {
      onConnected: () => {
        setDot("ws", "ok");
        btnDisconnect.disabled = false;
        const { msg } = makeTurn("system");
        msg.textContent = "Connected — speak now.";
        addLog("transport connected");
      },
      onDisconnected: () => {
        setDot("ws", "err");
        setDot("stt", "");
        setDot("bridge", "");
        setDot("agent", "");
        btnConnect.disabled = false;
        btnDisconnect.disabled = true;
        const { msg } = makeTurn("system");
        msg.textContent = "Disconnected.";
        addLog("transport disconnected");
      },
      onBotReady: (data) => {
        addLog(`bot ready: ${JSON.stringify(data)}`);
      },

      onUserTranscript: (data) => {
        if (!data.final) {
          // First non-final = start of STT window
          if (tSttStart === 0) {
            tSttStart = Date.now();
            setDot("stt", "warn");
          }
          return;
        }

        const now = Date.now();
        const sttLatency = tSttStart > 0 ? now - tSttStart : 0;
        tUserFinal = now;
        tSttStart  = 0;
        tFirstChunk = 0;

        setDot("stt", "ok");

        const { wrap, msg } = makeTurn("user");
        msg.textContent = data.text;
        if (sttLatency > 0) {
          addLatency(wrap, [{ label: "STT", cls: "stt", value: sttLatency }]);
        }
        addLog(`STT (${fmtMs(sttLatency)}): ${data.text}`);

        // Show thinking indicator while waiting for agent
        thinkingEl = makeThinking();
      },

      onBotTranscript: (data) => {
        const now = Date.now();

        // Remove thinking indicator on first chunk
        if (thinkingEl) {
          thinkingEl.remove();
          thinkingEl = null;
        }

        if (!agentMsg) {
          // First chunk of this agent turn
          tFirstChunk = now;
          setDot("bridge", "ok");
          setDot("agent", "warn");
          agentBuf = "";
          const t = makeTurn("agent");
          agentWrap = t.wrap;
          agentMsg  = t.msg;
          agentMsg.classList.add("cursor");
        }

        agentBuf += data.text;
        agentMsg.textContent = agentBuf;
        transcript.scrollTop = transcript.scrollHeight;
      },

      onError: (err) => {
        addLog(`error: ${JSON.stringify(err)}`);
        setDot("ws", "err");
      },
    },
  };

  pcClient = new PipecatClient(options);

  pcClient.on(RTVIEvent.BotStartedSpeaking, () => {
    setDot("bridge", "ok");
    setDot("agent", "warn");
  });

  pcClient.on(RTVIEvent.BotStoppedSpeaking, () => {
    setDot("agent", "ok");

    if (agentMsg) {
      agentMsg.classList.remove("cursor");
    }

    if (agentWrap && tUserFinal > 0 && tFirstChunk > 0) {
      const ttfa  = tFirstChunk - tUserFinal;
      const total = Date.now() - tUserFinal;
      addLatency(agentWrap, [
        { label: "TTFA",  cls: "ttfa",  value: ttfa },
        { label: "Total", cls: "total", value: total },
      ]);
      addLog(`Agent (TTFA ${fmtMs(ttfa)}, Total ${fmtMs(total)}): ${agentBuf}`);
    }

    agentWrap   = null;
    agentMsg    = null;
    agentBuf    = "";
    tFirstChunk = 0;
    transcript.scrollTop = transcript.scrollHeight;
  });

  try {
    addLog("initialising devices…");
    await pcClient.initDevices();
    addLog("connecting to playground server…");
    await pcClient.startBotAndConnect({
      endpoint: `/connect?agent_id=${AGENT_ID}`,
    });
  } catch (err) {
    addLog(`connect failed: ${(err as Error).message}`);
    setDot("ws", "err");
    btnConnect.disabled = false;
    btnDisconnect.disabled = true;
    pcClient = null;
  }
}

async function disconnect() {
  if (pcClient) {
    await pcClient.disconnect().catch(() => {});
    pcClient = null;
  }
}

btnConnect.addEventListener("click", connect);
btnDisconnect.addEventListener("click", disconnect);
