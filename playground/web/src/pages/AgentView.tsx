// playground/web/src/pages/AgentView.tsx
import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchConfig,
  fetchFlows,
  switchFlow,
  type Flow,
  type UnpodConfig,
} from "../config";
import { SupervoiceClient } from "../client/SupervoiceClient";
import { SupervoiceWSTransport } from "../transport/SupervoiceWSTransport";
import { BotAudioPanel } from "../components/BotAudioPanel";
import { DashboardPanel } from "../components/DashboardPanel";
import { ConversationPanel } from "../components/ConversationPanel";
import { MetricsPanel } from "../components/MetricsPanel";
import { Sidebar } from "../components/Sidebar";
import { StatusPanel } from "../components/StatusPanel";
import { EventsLog } from "../components/EventsLog";
import { TopBar } from "../components/TopBar";
import {
  clockTime,
  EMPTY_METRICS,
  type AppState,
  type LLMCallEvent,
  type LogEntry,
  type MetricSnapshot,
  type SessionInfo,
  type TurnCompleteEvent,
  type Turn,
} from "../types";

type Tab = "conversation" | "metrics" | "trace";

const num = (v: unknown): number | null => (typeof v === "number" ? v : null);

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
      return `node → ${d.node_id ?? "?"}`;
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
  const [tab, setTab] = useState<Tab>("conversation");

  const [flows, setFlows] = useState<Flow[]>([]);
  const [activeFlow, setActiveFlow] = useState("");
  const [currentNode, setCurrentNode] = useState<string | null>(null);
  const [switchingFlow, setSwitchingFlow] = useState<string | null>(null);

  const [turns, setTurns] = useState<Turn[]>([]);
  const [metrics, setMetrics] = useState<MetricSnapshot>(EMPTY_METRICS);
  const [llmCalls, setLlmCalls] = useState<LLMCallEvent[]>([]);
  const [turnTimings, setTurnTimings] = useState<TurnCompleteEvent[]>([]);
  const [events, setEvents] = useState<LogEntry[]>([]);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [agentReady, setAgentReady] = useState(false);
  const [micLevel, setMicLevel] = useState(0);
  const [botLevel, setBotLevel] = useState(0);
  const [switchNote, setSwitchNote] = useState<{
    kind: "ok" | "err";
    text: string;
  } | null>(null);

  const clientRef = useRef<SupervoiceClient | null>(null);
  const logId = useRef(0);
  const flowsDefaultRef = useRef("");
  const activeFlowRef = useRef(""); // current selection, read at switch/ready time
  const connectingRef = useRef(false); // synchronous re-entrancy guard
  const pendingAgentQueue = useRef<Array<{ text: string; shown: boolean }>>([]);
  const botWasSpeaking = useRef(false);

  useEffect(() => {
    Promise.all([fetchConfig(), fetchFlows()])
      .then(([cfg, fl]) => {
        setConfig(cfg);
        setSelectedVoiceProfile(cfg.voice_profiles[0]?.id ?? "");
        setFlows(fl.flows);
        const initial = fl.active ?? fl.flows[0]?.id ?? "";
        setActiveFlow(initial);
        flowsDefaultRef.current = initial;
      })
      .catch((err) => setConfigError((err as Error).message));
  }, []);

  // Keep a ref in sync so the connect()/ready closures read the live selection.
  useEffect(() => {
    activeFlowRef.current = activeFlow;
  }, [activeFlow]);

  // Tear down the live client (mic + WebSockets) if the view unmounts.
  useEffect(
    () => () => {
      void clientRef.current?.disconnect();
      clientRef.current = null;
    },
    [],
  );

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

  const flowLabel = useCallback(
    (id: string) => flows.find((f) => f.id === id)?.label ?? id,
    [flows],
  );

  const connect = useCallback(async () => {
    if (connectingRef.current || appState === "active") return; // re-entrancy guard
    connectingRef.current = true;

    const stale = clientRef.current;
    clientRef.current = null;
    if (stale) await stale.disconnect();

    setAppState("connecting");
    setTurns([]);
    setMetrics(EMPTY_METRICS);
    setLlmCalls([]);
    setTurnTimings([]);
    setEvents([]);
    setSession(null);
    setAgentReady(false);
    setCurrentNode(null);
    setSwitchNote(null);
    setMicLevel(0);
    setBotLevel(0);
    pendingAgentQueue.current = [];
    botWasSpeaking.current = false;

    const transport = new SupervoiceWSTransport(undefined, selectedVoiceProfile);
    const client = new SupervoiceClient({ transport });
    clientRef.current = client;

    client.onAny(pushEvent);
    client.onMicLevel((l) => setMicLevel((cur) => Math.max(cur, l)));
    client.onBotLevel((l) => {
      const speaking = l > 0.04;
      if (speaking && !botWasSpeaking.current && pendingAgentQueue.current.length > 0) {
        const items = pendingAgentQueue.current.splice(0);
        for (const item of items) {
          if (!item.shown) {
            item.shown = true;
            addTurn("agent", item.text);
          }
        }
      }
      botWasSpeaking.current = speaking;
      setBotLevel((cur) => Math.max(cur, l));
    });

    client.on("connected", () => setAppState("active"));
    client.on("disconnected", () => {
      setAppState("disconnected");
      setAgentReady(false);
    });
    client.on("ready", () => {
      setAgentReady(true);
      // Start the conversation on the currently-selected flow (worker defaults
      // to the first flow in the set). Read the live selection (the user may
      // have changed it during "connecting"); fresh memory on initial select.
      const startFlow = activeFlowRef.current;
      if (startFlow && startFlow !== flowsDefaultRef.current) {
        switchFlow(startFlow, false).catch(() => {});
      }
    });
    client.on("session", (d) => setSession(d as SessionInfo));
    client.on("flow_node_changed", (d) =>
      setCurrentNode(d.node_id != null ? String(d.node_id) : null),
    );
    client.on("user_turn", (d) => addTurn("user", String(d.text ?? "")));
    client.on("agent_turn", (d) => {
      const text = String(d.text ?? "");
      const entry: { text: string; shown: boolean } = { text, shown: false };
      pendingAgentQueue.current.push(entry);
      // Safety fallback: show after 900ms even if bot audio never starts
      window.setTimeout(() => {
        if (!entry.shown) {
          entry.shown = true;
          const idx = pendingAgentQueue.current.indexOf(entry);
          if (idx !== -1) pendingAgentQueue.current.splice(idx, 1);
          addTurn("agent", text);
        }
      }, 900);
    });
    client.on("metric", (d) =>
      setMetrics({
        ttfa_ms: num(d.ttfa_ms),
        asr_p95_ms: num(d.asr_p95_ms),
        tts_p95_ms: num(d.tts_p95_ms),
        turns: num(d.turns) ?? 0,
        cost_usd_so_far: num(d.cost_usd_so_far),
      }),
    );
    client.on("llm_call", (d) => {
      setLlmCalls((prev) => {
        const next = [...prev, d as unknown as LLMCallEvent];
        return next.length > 500 ? next.slice(-500) : next;
      });
    });
    client.on("turn_complete", (d) => {
      setTurnTimings((prev) => {
        const next = [...prev, d as unknown as TurnCompleteEvent];
        return next.length > 200 ? next.slice(-200) : next;
      });
    });
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
    } finally {
      connectingRef.current = false;
    }
  }, [appState, selectedVoiceProfile, addTurn, pushEvent]);

  const disconnect = useCallback(async () => {
    await clientRef.current?.disconnect();
    clientRef.current = null;
  }, []);

  const selectFlow = useCallback(
    async (flowId: string) => {
      if (appState !== "active") {
        setActiveFlow(flowId); // pre-call: applied on connect
        return;
      }
      setSwitchingFlow(flowId);
      setCurrentNode(null); // don't show the previous flow's node on the new card
      const res = await switchFlow(flowId, true);
      setSwitchingFlow(null);
      if (res.ok) {
        setActiveFlow(flowId);
        setSwitchNote({ kind: "ok", text: `Switched to ${flowLabel(flowId)}` });
        addTurn("system", `Switched to ${flowLabel(flowId)}`);
      } else {
        const text = `Switch failed: ${res.error ?? "unknown"}`;
        setSwitchNote({ kind: "err", text });
        addTurn("system", text);
      }
    },
    [appState, addTurn, flowLabel],
  );

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
        selectedVoiceProfile={selectedVoiceProfile}
        onVoiceProfileChange={setSelectedVoiceProfile}
        appState={appState}
        onConnect={connect}
        onDisconnect={disconnect}
      />

      <div className="grid">
        <div className="leftcol">
          <Sidebar
            flows={flows}
            activeFlow={activeFlow}
            currentNode={currentNode}
            switching={switchingFlow}
            appState={appState}
            note={switchNote}
            onSelect={selectFlow}
          />
          <BotAudioPanel appState={appState} level={botLevel} />
        </div>

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
            <button
              className={`tab${tab === "trace" ? " tab--active" : ""}`}
              onClick={() => setTab("trace")}
            >
              Trace
            </button>
          </div>
          {tab === "trace" ? (
            <DashboardPanel
              llmCalls={llmCalls}
              turnTimings={turnTimings}
              metrics={metrics}
            />
          ) : tab === "conversation" ? (
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
