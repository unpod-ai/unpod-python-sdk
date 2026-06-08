// playground/web/src/components/TopBar.tsx
import { useRef } from "react";

import type { VoiceProfile, FlowOption } from "../config";
import type { AppState } from "../types";

interface TopBarProps {
  voiceProfiles: VoiceProfile[];
  flows: FlowOption[];
  selectedVoiceProfile: string;
  selectedFlow: string;
  onVoiceProfileChange: (id: string) => void;
  onFlowChange: (id: string) => void;
  onCustomFlow: (flow: Record<string, unknown>) => void;
  appState: AppState;
  onConnect: () => void;
  onDisconnect: () => void;
}

export function TopBar({
  voiceProfiles,
  flows,
  selectedVoiceProfile,
  selectedFlow,
  onVoiceProfileChange,
  onFlowChange,
  onCustomFlow,
  appState,
  onConnect,
  onDisconnect,
}: TopBarProps) {
  const fileRef = useRef<HTMLInputElement>(null);
  const locked = appState === "connecting" || appState === "active";

  function handleFlowChange(e: React.ChangeEvent<HTMLSelectElement>) {
    if (e.target.value === "__custom__") {
      fileRef.current?.click();
      e.target.value = selectedFlow;
    } else {
      onFlowChange(e.target.value);
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      try {
        const flow = JSON.parse(reader.result as string) as Record<string, unknown>;
        onCustomFlow(flow);
      } catch {
        alert("Invalid flow JSON — check the file and try again.");
      }
    };
    reader.onerror = () => alert("Could not read file.");
    reader.readAsText(file);
    e.target.value = "";
  }

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

        <label className="sr-only" htmlFor="flow-select">Flow</label>
        <select
          id="flow-select"
          className="topbar__select"
          value={selectedFlow}
          onChange={handleFlowChange}
          disabled={locked}
        >
          {flows.map((f) => (
            <option key={f.id} value={f.id}>{f.name}</option>
          ))}
          <option value="__custom__">Upload flow…</option>
        </select>
        <input
          ref={fileRef}
          type="file"
          accept=".json"
          style={{ display: "none" }}
          onChange={handleFileChange}
        />
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
