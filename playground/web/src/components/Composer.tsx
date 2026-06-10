// playground/web/src/components/Composer.tsx
import type { AppState } from "../types";

function MicIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
      <path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4" />
    </svg>
  );
}

function SendIcon() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M22 2L11 13M22 2l-7 20-4-9-9-4 20-7z" />
    </svg>
  );
}

interface ComposerProps {
  appState: AppState;
  speaking: boolean;
}

// Voice-first: there is no browser→agent text channel in the dev path, so the
// composer is display-only (it mirrors the design but the input stays disabled).
export function Composer({ appState, speaking }: ComposerProps) {
  const live = appState === "active";
  return (
    <div className="composer">
      <button
        className={`comp-mic ${speaking ? "live" : ""}`}
        disabled
        title={live ? "Voice is open — just talk" : "Connect to talk"}
        aria-label="Microphone"
      >
        <MicIcon />
      </button>
      <input
        className="comp-input"
        placeholder={live ? "Voice-first — speak to the agent" : "Connect to talk"}
        disabled
      />
      <button className="comp-send" disabled aria-label="Send">
        <SendIcon />
      </button>
    </div>
  );
}
