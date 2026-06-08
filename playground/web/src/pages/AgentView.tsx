// playground/web/src/pages/AgentView.tsx
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchConfig, type UnpodConfig, type VoiceProfile } from "../config";
import { SupervoiceClient } from "../client/SupervoiceClient";
import { SupervoiceWSTransport } from "../transport/SupervoiceWSTransport";
import { Orb, type OrbState } from "../components/Orb";
import { ConfigPanel } from "../components/ConfigPanel";
import { InfoStrip } from "../components/InfoStrip";
import { TranscriptView, type Turn } from "../components/TranscriptView";

type AppState = "idle" | "connecting" | "active" | "disconnected";

export function AgentView() {
  const [appState, setAppState] = useState<AppState>("idle");
  const [config, setConfig] = useState<UnpodConfig | null>(null);
  const [selectedVoiceProfile, setSelectedVoiceProfile] = useState<string>("");
  const [selectedFlow, setSelectedFlow] = useState<string>("");
  const [customFlow, setCustomFlow] = useState<Record<string, unknown> | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [latencyMs, setLatencyMs] = useState<number | null>(null);

  const clientRef = useRef<SupervoiceClient | null>(null);
  const turnCountRef = useRef(0);
  const latencySumRef = useRef(0);
  const latencyCountRef = useRef(0);

  useEffect(() => {
    fetchConfig()
      .then((cfg) => {
        setConfig(cfg);
        setSelectedVoiceProfile(cfg.voice_profiles[0]?.id ?? "");
        setSelectedFlow(cfg.flows[0]?.id ?? "");
      })
      .catch(console.error);
  }, []);

  const addTurn = useCallback((turn: Turn) => {
    setTurns((prev) => [...prev, turn]);
    if (turn.role !== "system") turnCountRef.current += 1;
  }, []);

  const connect = useCallback(async () => {
    const stale = clientRef.current;
    clientRef.current = null;
    if (stale) await stale.disconnect();

    setAppState("connecting");
    setTurns([]);
    setLatencyMs(null);
    turnCountRef.current = 0;
    latencySumRef.current = 0;
    latencyCountRef.current = 0;

    const agentId = selectedFlow !== "__custom__" ? selectedFlow : undefined;
    const transport = new SupervoiceWSTransport(agentId, selectedVoiceProfile);
    const client = new SupervoiceClient({ transport });
    clientRef.current = client;

    client.on("connected", () => setAppState("active"));
    client.on("disconnected", () => setAppState("disconnected"));
    client.on("user_turn", (d) =>
      addTurn({ role: "user", text: String(d.text ?? "") }),
    );
    client.on("agent_turn", (d) =>
      addTurn({ role: "agent", text: String(d.text ?? "") }),
    );
    client.on("metric", (d) => {
      const ms = Number((d.ttfa_ms ?? d.latency_ms) ?? 0);
      if (ms > 0) {
        latencySumRef.current += ms;
        latencyCountRef.current += 1;
        setLatencyMs(ms);
      }
    });
    client.on("error", (d) => {
      addTurn({ role: "system", text: `Error: ${String(d.message ?? "unknown")}` });
      setAppState("idle");
    });

    try {
      await client.connect();
    } catch (err) {
      addTurn({ role: "system", text: `Connect failed: ${(err as Error).message}` });
      setAppState("idle");
      clientRef.current = null;
    }
  }, [selectedFlow, selectedVoiceProfile, addTurn]);

  const disconnect = useCallback(async () => {
    await clientRef.current?.disconnect();
    clientRef.current = null;
  }, []);

  const orbState: OrbState =
    appState === "active" ? "active"
    : appState === "connecting" ? "connecting"
    : "idle";

  const activeVoiceProfile = config?.voice_profiles.find(
    (vp: VoiceProfile) => vp.id === selectedVoiceProfile,
  );

  const avgLatency =
    latencyCountRef.current > 0
      ? Math.round(latencySumRef.current / latencyCountRef.current)
      : null;

  if (!config) return <div className="loading">Loading…</div>;

  return (
    <div className={`app app--${appState}`}>
      <header className="app__logo">
        <svg width="110" height="30" viewBox="0 0 110 30" fill="none">
          <text
            x="0" y="24"
            fontSize="22" fontWeight="700"
            fill="white" fontFamily="system-ui, -apple-system, sans-serif"
          >
            unpod
          </text>
        </svg>
        <p className="app__tagline">Talk to your AI agent</p>
      </header>

      <div className="app__orb-wrap">
        <Orb state={orbState} />
      </div>

      {(appState === "idle" || appState === "connecting") && (
        <ConfigPanel
          voiceProfiles={config.voice_profiles}
          flows={config.flows}
          activeLlm={config.active_llm}
          selectedVoiceProfile={selectedVoiceProfile}
          selectedFlow={selectedFlow}
          onVoiceProfileChange={setSelectedVoiceProfile}
          onFlowChange={(id) => { setSelectedFlow(id); setCustomFlow(null); }}
          onCustomFlow={(flow) => { setCustomFlow(flow); setSelectedFlow("__custom__"); }}
          disabled={appState === "connecting"}
        />
      )}

      {appState === "active" && (
        <InfoStrip
          voiceProfileName={activeVoiceProfile?.name ?? selectedVoiceProfile}
          activeLlm={config.active_llm}
          latencyMs={latencyMs}
        />
      )}

      {(appState === "active" || appState === "disconnected") && (
        <TranscriptView turns={turns} />
      )}

      {appState === "disconnected" && (
        <p className="session-summary">
          Session ended · {turnCountRef.current} turns
          {avgLatency !== null ? ` · Avg TTFA: ${avgLatency}ms` : ""}
        </p>
      )}

      <div className="app__actions">
        {appState === "idle" && (
          <button className="btn btn--connect" onClick={connect}>
            Connect
          </button>
        )}
        {appState === "connecting" && (
          <button className="btn btn--connect" disabled>
            Connecting…
          </button>
        )}
        {appState === "active" && (
          <button className="btn btn--disconnect" onClick={disconnect}>
            Disconnect
          </button>
        )}
        {appState === "disconnected" && (
          <button className="btn btn--connect" onClick={() => setAppState("idle")}>
            Start new session
          </button>
        )}
      </div>

      {customFlow !== null && appState !== "active" && (
        <p className="session-summary">
          Custom flow loaded · {String(customFlow?.name ?? "unnamed")}
        </p>
      )}
    </div>
  );
}