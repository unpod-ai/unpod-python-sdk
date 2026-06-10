// playground/web/src/components/StatusPanel.tsx
// Left-rail "State" section (matches the standalone design): Client/Agent pills
// + a Transport/Session/Voice/LLM/Round-trip block, wired to live session data.
import type { AppState, SessionInfo } from "../types";

interface StatusPanelProps {
  appState: AppState;
  agentReady: boolean;
  speaking: boolean;
  session: SessionInfo | null;
  voiceProfileName: string;
  activeLlm: string;
  latencyMs: number | null;
}

function truncate(value: string, head = 8, tail = 4): string {
  return value.length > head + tail + 1
    ? `${value.slice(0, head)}…${value.slice(-tail)}`
    : value;
}

function StatRow({
  label,
  value,
  accent,
}: {
  label: string;
  value: string;
  accent?: boolean;
}) {
  const dim = value === "—";
  return (
    <div className="stat-row">
      <span className="stat-k">{label}</span>
      <span className={`stat-v${accent ? " accent" : ""}${dim ? " dim" : ""}`}>
        {value}
      </span>
    </div>
  );
}

export function StatusPanel({
  appState,
  agentReady,
  speaking,
  session,
  voiceProfileName,
  activeLlm,
  latencyMs,
}: StatusPanelProps) {
  const connected = appState === "active";
  const sessionId = typeof session?.session_id === "string" ? session.session_id : "";

  return (
    <div className="rail-section">
      <div className="rail-label">State</div>
      <div className="status-pills">
        <div className={`sp ${connected ? "on" : ""}`}>
          <span className="sp-k">Client</span>
          <span className="sp-v">
            <span className={`led ${connected ? "green" : ""}`} />
            {connected ? "Live" : appState === "connecting" ? "Connecting" : "Idle"}
          </span>
        </div>
        <div className={`sp ${connected ? "on" : ""}`}>
          <span className="sp-k">Agent</span>
          <span className="sp-v">
            {connected ? (
              <>
                <span className={`led ${speaking ? "amber" : agentReady ? "green" : ""}`} />
                {speaking ? "Speaking" : agentReady ? "Ready" : "…"}
              </>
            ) : (
              "—"
            )}
          </span>
        </div>
      </div>
      <div className="status-block">
        <StatRow label="Transport" value={session?.transport ?? "—"} />
        <StatRow
          label="Session"
          value={sessionId ? truncate(sessionId) : "—"}
        />
        <StatRow label="Voice" value={voiceProfileName || "—"} accent={!!voiceProfileName} />
        <StatRow label="LLM" value={activeLlm || "—"} />
        <StatRow
          label="First audio"
          value={latencyMs != null ? `~${latencyMs}ms` : "—"}
          accent={latencyMs != null}
        />
      </div>
    </div>
  );
}
