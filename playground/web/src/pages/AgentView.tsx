// playground/web/src/pages/AgentView.tsx
import { useCallback, useEffect, useRef, useState } from "react";

import { fetchConfig, type UnpodConfig } from "../config";
import { SupervoiceClient } from "../client/SupervoiceClient";
import { SupervoiceWSTransport } from "../transport/SupervoiceWSTransport";
import { BotAudioPanel } from "../components/BotAudioPanel";
import { ConversationPanel } from "../components/ConversationPanel";
import { MetricsPanel } from "../components/MetricsPanel";
import { StatusPanel } from "../components/StatusPanel";
import { EventsLog } from "../components/EventsLog";
import { TopBar } from "../components/TopBar";
import {
  clockTime,
  EMPTY_METRICS,
  type AppState,
  type LogEntry,
  type MetricSnapshot,
  type SessionInfo,
  type Turn,
} from "../types";

type Tab = "conversation" | "metrics";

const num = (v: unknown): number | null =>
  typeof v === "number" ? v : null;

/** One-line, human-readable summary of a side-channel event for the log. */
function describe(type: string, d: Record<string, unknown>): string {
  switch (type) {
    case "connected":
      return "audio websocket open";
    case "disconnected":
      return String(d.reason ?? "connection closed");
    case "session":
      return `${d.transport ?? "ws"} · ${d.session_id ?? "no id"}`;
    case "ready":
      return `agent ${d.agent ?? ""} ready`;
    case "user_turn":
      return `"${d.text ?? ""}"`;
    case "agent_turn":
      return `"${d.text ?? ""}"`;
    case "metric":
      return `ttfa=${d.ttfa_ms ?? "—"}ms turns=${d.turns ?? 0} cost=$${d.cost_usd_so_far ?? 0}`;
    case "interruption":
      return "user interrupted the agent";
    case "flow_node_changed":
      return JSON.stringify(d);
    case "error":
      return `[${d.severity ?? "error"}] ${d.code ?? ""}: ${d.message ?? ""}`;
    default:
      return JSON.stringify(d);
  }
}

export function AgentView() {
  const [appState, setAppState] = useState<AppState>("idle");
  const [config, setConfig] = useState<UnpodConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const [selectedVoiceProfile, setSelectedVoiceProfile] = useState("");
  const [selectedFlow, setSelectedFlow] = useState("");
  const [tab, setTab] = useState<Tab>("conversation");

  const [turns, setTurns] = useState<Turn[]>([]);
  const [metrics, setMetrics] = useState<MetricSnapshot>(EMPTY_METRICS);
  const [events, setEvents] = useState<LogEntry[]>([]);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [agentReady, setAgentReady] = useState(false);
  const [micLevel, setMicLevel] = useState(0);
  const [botLevel, setBotLevel] = useState(0);

  const clientRef = useRef<SupervoiceClient | null>(null);
  const logId = useRef(0);

  useEffect(() => {
    fetchConfig()
      .then((cfg) => {
        setConfig(cfg);
        setSelectedVoiceProfile(cfg.voice_profiles[0]?.id ?? "");
        setSelectedFlow(cfg.flows[0]?.id ?? "");
      })
      .catch((err) => setConfigError((err as Error).message));
  }, []);

  // Level meters decay toward 0 between audio frames.
  useEffect(() => {
    if (appState !== "active") return;
    const id = window.setInterval(() => {
      setMicLevel((l) => (l > 0.01 ? l * 0.65 : 0));
      setBotLevel((l) => (l > 0.01 ? l * 0.6 : 0));
    }, 100);
    return () => window.clearInterval(id);
  }, [appState]);

  const pushEvent = useCallback((type: string, data: Record<string, unknown>) => {
    setEvents((prev) => {
      const entry: LogEntry = {
        id: logId.current++,
        time: clockTime(),
        type,
        detail: describe(type, data),
      };
      const next = [...prev, entry];
      return next.length > 500 ? next.slice(-500) : next;
    });
  }, []);

  const addTurn = useCallback((role: Turn["role"], text: string) => {
    setTurns((prev) => [...prev, { role, text, time: clockTime() }]);
  }, []);

  const connect = useCallback(async () => {
    const stale = clientRef.current;
    clientRef.current = null;
    if (stale) await stale.disconnect();

    setAppState("connecting");
    setTurns([]);
    setMetrics(EMPTY_METRICS);
    setEvents([]);
    setSession(null);
    setAgentReady(false);
    setMicLevel(0);
    setBotLevel(0);

    const agentId = selectedFlow !== "__custom__" ? selectedFlow : undefined;
    const transport = new SupervoiceWSTransport(agentId, selectedVoiceProfile);
    const client = new SupervoiceClient({ transport });
    clientRef.current = client;

    client.onAny(pushEvent);
    client.onMicLevel((l) => setMicLevel((cur) => Math.max(cur, l)));
    client.onBotLevel((l) => setBotLevel((cur) => Math.max(cur, l)));

    client.on("connected", () => setAppState("active"));
    client.on("disconnected", () => {
      setAppState("disconnected");
      setAgentReady(false);
    });
    client.on("ready", () => setAgentReady(true));
    client.on("session", (d) => setSession(d as SessionInfo));
    client.on("user_turn", (d) => addTurn("user", String(d.text ?? "")));
    client.on("agent_turn", (d) => addTurn("agent", String(d.text ?? "")));
    client.on("metric", (d) =>
      setMetrics({
        ttfa_ms: num(d.ttfa_ms),
        asr_p95_ms: num(d.asr_p95_ms),
        tts_p95_ms: num(d.tts_p95_ms),
        turns: num(d.turns) ?? 0,
        cost_usd_so_far: num(d.cost_usd_so_far),
      }),
    );
    client.on("error", (d) => {
      addTurn("system", `Error: ${String(d.message ?? "unknown")}`);
      setAppState("idle");
    });

    try {
      await client.connect();
    } catch (err) {
      addTurn("system", `Connect failed: ${(err as Error).message}`);
      pushEvent("error", { message: (err as Error).message });
      setAppState("idle");
      clientRef.current = null;
    }
  }, [selectedFlow, selectedVoiceProfile, addTurn, pushEvent]);

  const disconnect = useCallback(async () => {
    await clientRef.current?.disconnect();
    clientRef.current = null;
  }, []);

  if (configError) {
    return (
      <div className="loading loading--error">
        Could not reach the harness: {configError}
        <br />
        Is it running on this port? (<code>task pg</code>)
      </div>
    );
  }
  if (!config) return <div className="loading">Loading…</div>;

  const voiceProfileName =
    config.voice_profiles.find((vp) => vp.id === selectedVoiceProfile)?.name ?? "";

  return (
    <div className={`shell shell--${appState}`}>
      <TopBar
        voiceProfiles={config.voice_profiles}
        flows={config.flows}
        selectedVoiceProfile={selectedVoiceProfile}
        selectedFlow={selectedFlow}
        onVoiceProfileChange={setSelectedVoiceProfile}
        onFlowChange={setSelectedFlow}
        onCustomFlow={() => setSelectedFlow("__custom__")}
        appState={appState}
        onConnect={connect}
        onDisconnect={disconnect}
      />

      <div className="grid">
        <BotAudioPanel appState={appState} level={botLevel} />

        <section className="panel center">
          <div className="tabs">
            <button
              className={`tab${tab === "conversation" ? " tab--active" : ""}`}
              onClick={() => setTab("conversation")}
            >
              Conversation
            </button>
            <button
              className={`tab${tab === "metrics" ? " tab--active" : ""}`}
              onClick={() => setTab("metrics")}
            >
              Metrics
            </button>
          </div>
          {tab === "conversation" ? (
            <ConversationPanel turns={turns} appState={appState} />
          ) : (
            <MetricsPanel metrics={metrics} />
          )}
        </section>

        <StatusPanel
          appState={appState}
          agentReady={agentReady}
          micLevel={micLevel}
          session={session}
          voiceProfileName={voiceProfileName}
          activeLlm={config.active_llm}
        />
      </div>

      <EventsLog entries={events} />
    </div>
  );
}
