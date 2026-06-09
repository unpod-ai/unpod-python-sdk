import { useState } from "react";

import type { LLMCallEvent, MetricSnapshot, TurnCompleteEvent } from "../types";

interface Props {
  llmCalls: LLMCallEvent[];
  turnTimings: TurnCompleteEvent[];
  metrics: MetricSnapshot;
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

export function DashboardPanel({ llmCalls, turnTimings, metrics }: Props) {
  const [selectedTurn, setSelectedTurn] = useState<number | null>(null);
  const selected =
    turnTimings.find((t) => t.turn_id === selectedTurn) ??
    turnTimings[turnTimings.length - 1] ??
    null;

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
                <th>Node</th>
                <th>ASR</th>
                <th>LLM</th>
                <th>TTFA</th>
              </tr>
            </thead>
            <tbody>
              {turnTimings.map((t) => (
                <tr
                  key={t.turn_id}
                  className={t.turn_id === selectedTurn ? "selected" : ""}
                  onClick={() => setSelectedTurn(t.turn_id)}
                  style={{ cursor: "pointer" }}
                >
                  <td>{t.turn_id}</td>
                  <td>{t.to_node ?? "—"}</td>
                  <td>{t.asr_ms != null ? `${t.asr_ms.toFixed(0)}ms` : "—"}</td>
                  <td>
                    {t.llm_total_ms != null ? `${t.llm_total_ms.toFixed(0)}ms` : "—"}
                  </td>
                  <td>{t.ttfa_ms != null ? `${t.ttfa_ms.toFixed(0)}ms` : "—"}</td>
                </tr>
              ))}
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

      {llmCalls.length === 0 && turnTimings.length === 0 && (
        <div className="dashboard-empty">No data yet — start a call.</div>
      )}
    </div>
  );
}
