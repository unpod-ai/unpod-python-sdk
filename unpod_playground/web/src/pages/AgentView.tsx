// unpod_playground/web/src/pages/AgentView.tsx
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchConfig, type UnpodConfig } from "../config";
import { SupervoiceClient } from "../client/SupervoiceClient";
import { SupervoiceWSTransport } from "../transport/SupervoiceWSTransport";
import { TranscriptView, type Turn } from "../components/TranscriptView";

type AppState = "idle" | "connecting" | "active" | "disconnected";

export function AgentView() {
  const [appState, setAppState] = useState<AppState>("idle");
  const [config, setConfig] = useState<UnpodConfig | null>(null);
  const [selectedVoiceProfile, setSelectedVoiceProfile] = useState("");
  const [selectedFlow, setSelectedFlow] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const clientRef = useRef<SupervoiceClient | null>(null);

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
  }, []);

  const connect = useCallback(async () => {
    const stale = clientRef.current;
    clientRef.current = null;
    if (stale) await stale.disconnect();

    setAppState("connecting");
    setTurns([]);

    const transport = new SupervoiceWSTransport(
      selectedFlow || undefined,
      selectedVoiceProfile || undefined,
    );
    const client = new SupervoiceClient({ transport });
    clientRef.current = client;

    client.on("connected", () => setAppState("active"));
    client.on("disconnected", () => setAppState("disconnected"));
    client.on("user_turn", (d) =>
      addTurn({ role: "user", text: String((d as Record<string, unknown>).text ?? "") }),
    );
    client.on("agent_turn", (d) =>
      addTurn({ role: "agent", text: String((d as Record<string, unknown>).text ?? "") }),
    );
    client.on("error", (d) => {
      addTurn({
        role: "system",
        text: `Error: ${String((d as Record<string, unknown>).message ?? "unknown")}`,
      });
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

  if (!config) return <div className="loading">Loading…</div>;

  const isConnected = appState === "active";
  const isConnecting = appState === "connecting";

  return (
    <div className="page">
      <div className="logo">unpod</div>
      <p className="tagline">Open voice infrastructure for AI agents.</p>
      <p className="try-heading">Try Unpod</p>

      <div className="widget">
        {/* Row 1: Voice selector */}
        <div className="widget__selector-row">
          <select
            className="widget__selector"
            value={selectedVoiceProfile}
            onChange={(e) => setSelectedVoiceProfile(e.target.value)}
            disabled={isConnected || isConnecting}
          >
            {config.voice_profiles.map((vp) => (
              <option key={vp.id} value={vp.id}>{vp.name}</option>
            ))}
          </select>
          <button className="widget__expand" title="Expand" onClick={() => {}}>⤢</button>
        </div>

        {/* Row 2: Controls */}
        <div className="widget__controls">
          {!isConnected ? (
            <button
              className="btn--connect"
              onClick={connect}
              disabled={isConnecting}
            >
              {isConnecting ? "Connecting…" : "Connect"}
            </button>
          ) : (
            <button className="btn--disconnect" onClick={disconnect}>
              Disconnect
            </button>
          )}
          <button className="btn--mute">
            🎤
            <span className="dots">
              <span /><span /><span />
            </span>
          </button>
          <button className="btn--gear">⚙</button>
        </div>

        {/* Row 3: Status */}
        <div className="widget__status-row">
          <span className={`status-dot${isConnected ? " status-dot--active" : ""}`} />
          <span>
            {appState === "idle" && "Not connected"}
            {appState === "connecting" && "Connecting to agent…"}
            {appState === "active" && `Connected · ${config.active_llm}`}
            {appState === "disconnected" && "Session ended"}
          </span>
          {config.active_llm && appState === "active" && (
            <span style={{ marginLeft: "auto", fontSize: "0.75rem" }}>
              {selectedFlow}
            </span>
          )}
        </div>

        {/* Row 4: Transcript */}
        <div className="widget__transcript">
          {turns.length === 0 ? (
            <span className="transcript-placeholder">Transcript appears here</span>
          ) : (
            <TranscriptView turns={turns} />
          )}
        </div>
      </div>

      <div className="footer">
        <a href="https://github.com/unpod-ai" target="_blank" rel="noreferrer">GitHub</a>
        <a href="#docs">Docs</a>
      </div>
    </div>
  );
}
