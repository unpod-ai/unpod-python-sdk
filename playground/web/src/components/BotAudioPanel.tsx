// playground/web/src/components/BotAudioPanel.tsx
import type { AppState } from "../types";

interface BotAudioPanelProps {
  appState: AppState;
  level: number; // 0..1, live bot output loudness
}

// Symmetric envelope — taller in the middle. Driven by the real bot audio level.
const BAR_WEIGHTS = [
  0.4, 0.6, 0.78, 0.9, 1, 0.92, 1, 0.85, 1, 0.92, 1, 0.9, 0.78, 0.6, 0.4,
];

export function BotAudioPanel({ appState, level }: BotAudioPanelProps) {
  const active = appState === "active";
  return (
    <div className="wave" style={{ height: 30 }} aria-hidden="true">
      {BAR_WEIGHTS.map((w, i) => {
        const h = active ? 14 + level * 86 * w : 14;
        return (
          <span
            key={i}
            style={{
              height: `${h}%`,
              background: "var(--indigo)",
              opacity: active ? 0.55 + level * w * 0.45 : 0.3,
            }}
          />
        );
      })}
    </div>
  );
}
