// playground/web/src/components/BotAudioPanel.tsx
import type { AppState } from "../types";

interface BotAudioPanelProps {
  appState: AppState;
  level: number; // 0..1, live bot output loudness
}

// Taller in the middle, shorter at the edges — a simple symmetric envelope.
const BAR_WEIGHTS = [0.4, 0.6, 0.8, 1, 0.85, 1, 0.9, 1, 0.85, 1, 0.8, 0.6, 0.4];
const MIN_H = 6;
const MAX_H = 96;

export function BotAudioPanel({ appState, level }: BotAudioPanelProps) {
  const active = appState === "active";
  return (
    <section className="panel bot-audio">
      <div className="panel__header">
        <span>BOT AUDIO</span>
        <span className="panel__icon">{level > 0.04 ? "🔊" : "🔈"}</span>
      </div>
      <div className="bot-audio__viz">
        {BAR_WEIGHTS.map((w, i) => {
          const h = active ? MIN_H + level * (MAX_H - MIN_H) * w : MIN_H;
          return (
            <div
              key={i}
              className={`bot-audio__bar${active ? " bot-audio__bar--idle" : ""}`}
              style={{ height: `${h}px`, animationDelay: `${i * 90}ms` }}
            />
          );
        })}
      </div>
    </section>
  );
}
