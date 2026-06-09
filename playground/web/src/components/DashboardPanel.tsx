import { useState } from "react";

import type {
  LLMCallEvent,
  MetricSnapshot,
  Turn,
  TurnCompleteEvent,
} from "../types";

interface Props {
  llmCalls: LLMCallEvent[];
  turnTimings: TurnCompleteEvent[];
  metrics: MetricSnapshot;
  turns: Turn[];
}

function PipelineBar({ turn }: { turn: TurnCompleteEvent }) {
  const asr = turn.asr_ms ?? 0;
  const llm = turn.llm_total_ms ?? 0;
  const tts = turn.tts_ttfb_ms ?? 0;
  const total = asr + llm + tts || 1;
  return (
    <div
      style={{
        display: "flex",
        height: 12,
        borderRadius: 4,
        overflow: "hidden",
        background: "#222",
      }}
    >
      <div
        style={{ flex: asr / total, background: "#4ade80" }}
        title={`ASR ${asr.toFixed(0)}ms`}
      />
      <div
        style={{ flex: llm / total, background: "#60a5fa" }}
        title={`LLM ${llm.toFixed(0)}ms`}
      />
      <div
        style={{ flex: tts / total, background: "#f59e0b" }}
        title={`TTS ${tts.toFixed(0)}ms`}
      />
    </div>
  );
}

function LLMCallCard({ call }: { call: LLMCallEvent }) {
  const [showPrompt, setShowPrompt] = useState(false);
  const [showResponse, setShowResponse] = useState(false);

  return (
    <div className="llm-call-card">
      <div className="llm-call-header">
        <span>
          T{call.turn_id} · {call.node_id} · {call.call_type} ·{" "}
          {call.latency_ms.toFixed(0)}ms
        </span>
        <span className="llm-call-model">
          {call.model} · {call.tokens_in} in / {call.tokens_out} out
        </span>
      </div>
      <div className="llm-call-actions">
        <button type="button" onClick={() => setShowPrompt(!showPrompt)}>
          {showPrompt ? "▾" : "▸"} Prompt
        </button>
        <button type="button" onClick={() => setShowResponse(!showResponse)}>
          {showResponse ? "▾" : "▸"} Response
        </button>
      </div>
      {showPrompt && (
        <pre className="llm-call-json">
          {JSON.stringify(call.prompt_messages, null, 2)}
        </pre>
      )}
      {showResponse && (
        <pre className="llm-call-json">
          {JSON.stringify(call.response_json, null, 2)}
        </pre>
      )}
    </div>
  );
}

export function DashboardPanel({ llmCalls, turnTimings, metrics, turns }: Props) {
  const [selectedTurn, setSelectedTurn] = useState<number | null>(null);
  const selected =
    turnTimings.find((t) => t.turn_id === selectedTurn) ??
    turnTimings[turnTimings.length - 1] ??
    null;
  const userTurns = turns.filter((turn) => turn.role === "user");
  const userTextForTurn = (turnId: number): string => {
    const userTurn = userTurns[turnId - 1];
    return userTurn?.text ?? "—";
  };
  const callsForTurn = (turnId: number) =>
    llmCalls.filter((call) => call.turn_id === turnId);

  return (
    <div className="dashboard-panel">
      <div className="dashboard-summary">
        <span>
          TTFA p95:{" "}
          <b>{metrics.ttfa_ms != null ? `${metrics.ttfa_ms}ms` : "—"}</b>
        </span>
        <span>
          ASR p95:{" "}
          <b>{metrics.asr_p95_ms != null ? `${metrics.asr_p95_ms}ms` : "—"}</b>
        </span>
        <span>
          Turns: <b>{metrics.turns}</b>
        </span>
        <span>
          Session cost:{" "}
          <b>
            {metrics.cost_usd_so_far != null
              ? `$${metrics.cost_usd_so_far.toFixed(4)}`
              : "—"}
          </b>
        </span>
        <span>
          LLM calls: <b>{llmCalls.length}</b>
        </span>
      </div>

      {turnTimings.length > 0 && (
        <div className="dashboard-section">
          <div className="dashboard-section-title">Per-Turn Breakdown</div>
          <table className="turn-table">
            <thead>
              <tr>
                <th>#</th>
                <th>User</th>
                <th>Node</th>
                <th>Agent</th>
                <th title="Speech-to-text latency (STT)">ASR</th>
                <th>LLM</th>
                <th>TTS</th>
                <th>TTFA</th>
                <th>Calls</th>
              </tr>
            </thead>
            <tbody>
              {turnTimings.map((t) => {
                const userText = t.user_text ?? userTextForTurn(t.turn_id);
                const turnCalls = callsForTurn(t.turn_id);
                const llmCount = turnCalls.length || t.llm_call_count || 0;
                const llmTotalMs =
                  turnCalls.length > 0
                    ? turnCalls.reduce((sum, call) => sum + call.latency_ms, 0)
                    : t.llm_total_ms ?? null;
                const asrMs = t.asr_ms ?? t.stt_ms;
                const ttsMs = t.tts_ms ?? t.tts_ttfb_ms;
                return (
                  <tr
                    key={t.turn_id}
                    className={t.turn_id === selectedTurn ? "selected" : ""}
                    onClick={() => setSelectedTurn(t.turn_id)}
                    style={{ cursor: "pointer" }}
                  >
                    <td>{t.turn_id}</td>
                    <td title={userText}>
                      {userText.length > 28
                        ? `${userText.slice(0, 28)}…`
                        : userText}
                    </td>
                    <td>
                      {t.from_node != null || t.to_node != null
                        ? `${t.from_node ?? "—"} → ${t.to_node ?? "—"}`
                        : "—"}
                    </td>
                    <td title={t.agent_text ?? "—"}>
                      {t.agent_text
                        ? t.agent_text.length > 32
                          ? `${t.agent_text.slice(0, 32)}…`
                          : t.agent_text
                        : "—"}
                    </td>
                    <td title="Speech-to-text latency (STT)">
                      {asrMs != null ? `${asrMs.toFixed(0)}ms` : "—"}
                    </td>
                    <td>
                      {llmTotalMs != null
                        ? `${llmTotalMs.toFixed(0)}ms`
                        : "—"}
                    </td>
                    <td>{ttsMs != null ? `${ttsMs.toFixed(0)}ms` : "—"}</td>
                    <td>{t.ttfa_ms != null ? `${t.ttfa_ms.toFixed(0)}ms` : "—"}</td>
                    <td>{llmCount}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {selected && (
            <div className="pipeline-bar-row">
              <span className="pipeline-bar-label">
                <span style={{ color: "#4ade80" }}>■</span> ASR{" "}
                {(selected.asr_ms ?? 0).toFixed(0)}ms
                <span style={{ color: "#60a5fa" }}> ■</span> LLM{" "}
                {(selected.llm_total_ms ?? 0).toFixed(0)}ms
                <span style={{ color: "#f59e0b" }}> ■</span> TTS{" "}
                {(selected.tts_ttfb_ms ?? 0).toFixed(0)}ms
              </span>
              <PipelineBar turn={selected} />
            </div>
          )}
        </div>
      )}

      {llmCalls.length > 0 && (
        <div className="dashboard-section">
          <div className="dashboard-section-title">LLM Calls</div>
          {[...llmCalls].reverse().map((call, i) => (
            <LLMCallCard key={i} call={call} />
          ))}
        </div>
      )}

      {turnTimings.length === 0 && llmCalls.length === 0 && (
        <div className="dashboard-empty">
          No trace events yet. Start a call to see turn_complete and llm_call data.
        </div>
      )}

      {metrics.turns > 0 && turnTimings.length === 0 && (
        <div className="dashboard-empty">
          Metrics are coming through, but no per-turn trace events have arrived
          yet. Check the backend turn_complete / llm_call wiring.
        </div>
      )}
    </div>
  );
}
