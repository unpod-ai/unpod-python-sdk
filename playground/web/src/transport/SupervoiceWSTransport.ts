/**
 * SupervoiceWSTransport — the WS audio path.
 *
 * Acquires a session via `POST /playground/sessions` (which proxies supervoice
 * `POST /connect`), opens the returned `/ws/audio` WebSocket, streams 16 kHz mic
 * PCM as `Frame{audio}` messages, and plays back the 24 kHz audio frames the
 * server returns. Audio only — transcript/metrics arrive on the client's
 * side-channel. Mic/bot levels and the session descriptor are surfaced via the
 * transport callbacks for the meters and status panel.
 */

import { AudioPlayer, MicCapture, MIC_SAMPLE_RATE, pcmLevel } from "./audio";
import { decodeFrame, encodeAudioFrame } from "./protobuf";
import { Transport } from "./Transport";
import type { ConvState } from "../state/convState";

const CONV_STATES: ReadonlySet<string> = new Set([
  "idle",
  "listening",
  "thinking",
  "speaking",
  "interrupted",
]);

/** Parse a decoded control `message` frame; emit conv-state if it is one. */
function parseStateMessage(data: string): ConvState | null {
  try {
    const msg = JSON.parse(data) as { type?: unknown; state?: unknown };
    if (msg.type === "state" && typeof msg.state === "string" && CONV_STATES.has(msg.state)) {
      return msg.state as ConvState;
    }
  } catch {
    /* ignore malformed control frame */
  }
  return null;
}

interface SessionDescriptor {
  ws_url: string;
  transport?: string;
  agent?: string;
  session_id?: string;
  error?: string;
  [key: string]: unknown;
}

export class SupervoiceWSTransport extends Transport {
  private ws: WebSocket | null = null;
  private mic: MicCapture | null = null;
  private player: AudioPlayer | null = null;

  constructor(
    private readonly agent?: string,
    private readonly voiceProfileId?: string,
  ) {
    super();
  }

  private async createSession(): Promise<SessionDescriptor> {
    const params = new URLSearchParams();
    if (this.agent) params.set("agent", this.agent);
    if (this.voiceProfileId) params.set("voice_profile_id", this.voiceProfileId);
    const query = params.size > 0 ? `?${params.toString()}` : "";
    const resp = await fetch(`/playground/sessions${query}`, { method: "POST" });
    if (!resp.ok) throw new Error(`session create failed: HTTP ${resp.status}`);
    const data = (await resp.json()) as SessionDescriptor;
    if (data.error) throw new Error(data.error);
    if (!data.ws_url) throw new Error("session descriptor missing ws_url");
    return data;
  }

  async connect(): Promise<void> {
    const session = await this.createSession();
    this.callbacks.onSession?.(session);

    this.player = new AudioPlayer();
    this.player.start();

    const ws = new WebSocket(session.ws_url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;

    ws.onopen = async () => {
      this.mic = new MicCapture((pcm) => {
        if (ws.readyState === WebSocket.OPEN) {
          ws.send(encodeAudioFrame(pcm, MIC_SAMPLE_RATE, 1));
          this.callbacks.onMicLevel?.(pcmLevel(pcm));
        }
      });
      try {
        await this.mic.start();
        this.callbacks.onConnected?.();
      } catch (err) {
        this.callbacks.onError?.((err as Error).message);
        await this.disconnect();
      }
    };

    ws.onmessage = (event: MessageEvent) => {
      if (!(event.data instanceof ArrayBuffer)) return;
      const frame = decodeFrame(new Uint8Array(event.data));
      if (frame.kind === "audio") {
        this.player?.enqueue(frame.pcm, frame.sampleRate || 24000);
        this.callbacks.onBotLevel?.(pcmLevel(frame.pcm));
      } else if (frame.kind === "message") {
        const state = parseStateMessage(frame.data);
        if (state) this.callbacks.onState?.(state);
      }
    };

    ws.onerror = () => this.callbacks.onError?.("websocket error");
    ws.onclose = () => this.callbacks.onDisconnected?.();
  }

  async disconnect(): Promise<void> {
    await this.mic?.stop();
    await this.player?.stop();
    this.mic = null;
    this.player = null;
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) this.ws.close();
    this.ws = null;
  }
}
