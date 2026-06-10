// playground/web/src/components/TopBar.tsx
import type { AppState } from "../types";

interface TopBarProps {
  hasVoice: boolean;
  hasFlow: boolean;
  flowName: string;
  appState: AppState;
  onConnect: () => void;
  onDisconnect: () => void;
}

function Stepper({ hasVoice, hasFlow }: { hasVoice: boolean; hasFlow: boolean }) {
  const steps = [
    { n: 1, label: "Voice profile", done: hasVoice },
    { n: 2, label: "Conversation flow", done: hasFlow },
    { n: 3, label: "Connect", done: false },
  ];
  return (
    <div className="stepper">
      {steps.map((s, i) => {
        const active = !s.done && steps.slice(0, i).every((x) => x.done);
        return (
          <div key={s.n} className="stepper__row">
            <div className={`step ${s.done ? "done" : active ? "active" : ""}`}>
              <span className="step-n">{s.done ? "✓" : s.n}</span>
              <span className="step-l">{s.label}</span>
            </div>
            {i < steps.length - 1 && <span className="step-sep" />}
          </div>
        );
      })}
    </div>
  );
}

export function TopBar({
  hasVoice,
  hasFlow,
  flowName,
  appState,
  onConnect,
  onDisconnect,
}: TopBarProps) {
  const connected = appState === "active";
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark">⏧</span>
        <span className="brand-name">
          unpod <span>playground</span>
        </span>
      </div>

      <div className="topbar-mid">
        {connected ? (
          <div className="live-chip">
            <span className="led green" /> Live session · {flowName}
          </div>
        ) : (
          <Stepper hasVoice={hasVoice} hasFlow={hasFlow} />
        )}
      </div>

      <div className="topbar-right">
        {connected ? (
          <button className="btn btn--disconnect" onClick={onDisconnect}>
            Disconnect
          </button>
        ) : (
          <button
            className="btn btn--connect"
            onClick={onConnect}
            disabled={appState === "connecting"}
          >
            {appState === "connecting" ? (
              <>
                <span className="spin-mini" /> Connecting…
              </>
            ) : (
              <>
                <span className="led-dark" /> Connect
              </>
            )}
          </button>
        )}
      </div>
    </header>
  );
}
