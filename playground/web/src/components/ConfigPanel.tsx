// playground/web/src/components/ConfigPanel.tsx
import { useRef } from "react";
import type { VoiceProfile, FlowOption } from "../config";

interface ConfigPanelProps {
  voiceProfiles: VoiceProfile[];
  flows: FlowOption[];
  activeLlm: string;
  selectedVoiceProfile: string;
  selectedFlow: string;
  onVoiceProfileChange: (id: string) => void;
  onFlowChange: (id: string) => void;
  onCustomFlow: (flow: Record<string, unknown>) => void;
  disabled: boolean;
}

export function ConfigPanel({
  voiceProfiles,
  flows,
  activeLlm,
  selectedVoiceProfile,
  selectedFlow,
  onVoiceProfileChange,
  onFlowChange,
  onCustomFlow,
  disabled,
}: ConfigPanelProps) {
  const fileRef = useRef<HTMLInputElement>(null);

  function handleFlowChange(e: React.ChangeEvent<HTMLSelectElement>) {
    if (e.target.value === "__custom__") {
      fileRef.current?.click();
      // Reset select back to current flow — dialog may be cancelled
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
    reader.onerror = () => { alert("Could not read file."); };
    reader.readAsText(file);
    e.target.value = "";
  }

  return (
    <div className={`config-panel${disabled ? " config-panel--disabled" : ""}`}>
      <div className="config-row">
        <label className="config-label">Voice Profile</label>
        <select
          className="config-select"
          value={selectedVoiceProfile}
          onChange={(e) => onVoiceProfileChange(e.target.value)}
          disabled={disabled}
        >
          {voiceProfiles.map((vp) => (
            <option key={vp.id} value={vp.id}>{vp.name}</option>
          ))}
        </select>
      </div>

      <div className="config-row">
        <label className="config-label">Flow</label>
        <select
          className="config-select"
          value={selectedFlow}
          onChange={handleFlowChange}
          disabled={disabled}
        >
          {flows.map((f) => (
            <option key={f.id} value={f.id}>{f.name}</option>
          ))}
          <option value="__custom__">Upload your own…</option>
        </select>
        <input
          ref={fileRef}
          type="file"
          accept=".json"
          style={{ display: "none" }}
          onChange={handleFileChange}
        />
      </div>

      <div className="config-row">
        <label className="config-label">LLM</label>
        <div className="llm-badge" title="Set by the agent backend — not configurable here">
          <span className="llm-lock">🔒</span>
          <span>{activeLlm}</span>
        </div>
      </div>
    </div>
  );
}
