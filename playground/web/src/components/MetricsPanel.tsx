// playground/web/src/components/MetricsPanel.tsx
import { memo } from "react";

import type { MetricSnapshot } from "../types";

interface MetricsPanelProps {
  metrics: MetricSnapshot;
}

function ms(value: number | null): string {
  return value === null ? "—" : `${value} ms`;
}

function usd(value: number | null): string {
  return value === null ? "—" : `$${value.toFixed(4)}`;
}

export const MetricsPanel = memo(function MetricsPanel({
  metrics,
}: MetricsPanelProps) {
  const rows: Array<{ label: string; value: string; hint: string }> = [
    { label: "TTFA", value: ms(metrics.ttfa_ms), hint: "time to first audio" },
    { label: "ASR p95", value: ms(metrics.asr_p95_ms), hint: "speech-to-text" },
    { label: "TTS p95", value: ms(metrics.tts_p95_ms), hint: "text-to-speech" },
    { label: "Turns", value: String(metrics.turns), hint: "completed turns" },
    { label: "Cost", value: usd(metrics.cost_usd_so_far), hint: "so far" },
  ];
  return (
    <div className="metrics">
      {rows.map((r) => (
        <div key={r.label} className="metric-card">
          <div className="metric-card__value">{r.value}</div>
          <div className="metric-card__label">{r.label}</div>
          <div className="metric-card__hint">{r.hint}</div>
        </div>
      ))}
    </div>
  );
});
