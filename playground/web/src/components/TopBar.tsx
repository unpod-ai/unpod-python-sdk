// playground/web/src/components/TopBar.tsx
import type { VoiceProfile } from "../config";
import type { AppState } from "../types";

interface TopBarProps {
  voiceProfiles: VoiceProfile[];
  selectedVoiceProfile: string;
  onVoiceProfileChange: (id: string) => void;
  appState: AppState;
  onConnect: () => void;
  onDisconnect: () => void;
}

export function TopBar({
  voiceProfiles,
  selectedVoiceProfile,
  onVoiceProfileChange,
  appState,
  onConnect,
  onDisconnect,
}: TopBarProps) {
  const locked = appState === "connecting" || appState === "active";

  return (
    <header className="topbar">
      <div className="topbar__selectors">
        <label className="sr-only" htmlFor="vp-select">Voice profile</label>
        <select
          id="vp-select"
          className="topbar__select"
          value={selectedVoiceProfile}
          onChange={(e) => onVoiceProfileChange(e.target.value)}
          disabled={locked}
        >
          {voiceProfiles.map((vp) => (
            <option key={vp.id} value={vp.id}>{vp.name}</option>
          ))}
        </select>
      </div>

      <div className="topbar__brand">
        <span className="topbar__mark">⏧</span>
        <span className="topbar__title">unpod playground</span>
      </div>

      <div className="topbar__actions">
        {appState === "active" ? (
          <button className="btn btn--disconnect" onClick={onDisconnect}>
            Disconnect
          </button>
        ) : (
          <button
            className="btn btn--connect"
            onClick={onConnect}
            disabled={appState === "connecting"}
          >
            {appState === "connecting" ? "Connecting…" : "Connect"}
          </button>
        )}
      </div>
    </header>
  );
}
