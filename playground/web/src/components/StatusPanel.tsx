// playground/web/src/components/StatusPanel.tsx
import type { AppState, SessionInfo } from "../types";

interface StatusPanelProps {
  appState: AppState;
  agentReady: boolean;
  micLevel: number; // 0..1
  session: SessionInfo | null;
  voiceProfileName: string;
  activeLlm: string;
}

const CLIENT_LABEL: Record<AppState, string> = {
  idle: "IDLE",
  connecting: "CONNECTING",
  active: "CONNECTED",
  disconnected: "DISCONNECTED",
};

const MIC_SEGMENTS = 12;

function truncate(value: string, head = 8, tail = 4): string {
  return value.length > head + tail + 1
    ? `${value.slice(0, head)}…${value.slice(-tail)}`
    : value;
}

function copy(value: string): void {
  navigator.clipboard?.writeText(value).catch(() => {});
}

export function StatusPanel({
  appState,
  agentReady,
  micLevel,
  session,
  voiceProfileName,
  activeLlm,
}: StatusPanelProps) {
  const sessionId = typeof session?.session_id === "string" ? session.session_id : "";
  const lit = Math.round(micLevel * MIC_SEGMENTS);

  return (
    <aside className="status">
      <div className="status__group">
        <div className="status__title">STATUS</div>
        <div className="status__row">
          <span>Client</span>
          <span className={`status__value status__value--${appState}`}>
            {CLIENT_LABEL[appState]}
          </span>
        </div>
        <div className="status__row">
          <span>Agent</span>
          <span className="status__value">{agentReady ? "READY" : "—"}</span>
        </div>
      </div>

      <div className="status__group">
        <div className="status__title">DEVICES</div>
        <div className="status__mic">
          <span className="status__mic-icon">🎙</span>
          <div className="meter">
            {Array.from({ length: MIC_SEGMENTS }, (_, i) => (
              <span
                key={i}
                className={`meter__seg${i < lit ? " meter__seg--on" : ""}`}
              />
            ))}
          </div>
        </div>
      </div>

      <div className="status__group">
        <div className="status__title">SESSION</div>
        <div className="status__row">
          <span>Transport</span>
          <span className="status__value">{session?.transport ?? "—"}</span>
        </div>
        <div className="status__row">
          <span>Session ID</span>
          <span className="status__value status__value--mono">
            {sessionId ? truncate(sessionId) : "—"}
            {sessionId && (
              <button
                className="status__copy"
                onClick={() => copy(sessionId)}
                aria-label="Copy session id"
              >
                ⧉
              </button>
            )}
          </span>
        </div>
        <div className="status__row">
          <span>Agent</span>
          <span className="status__value">{session?.agent ?? "—"}</span>
        </div>
        <div className="status__row">
          <span>Voice</span>
          <span className="status__value">{voiceProfileName || "—"}</span>
        </div>
        <div className="status__row">
          <span>LLM</span>
          <span className="status__value">{activeLlm || "—"}</span>
        </div>
      </div>
    </aside>
  );
}
