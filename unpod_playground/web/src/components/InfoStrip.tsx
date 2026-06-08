// unpod_playground/web/src/components/InfoStrip.tsx

interface InfoStripProps {
  voiceProfileName: string;
  activeLlm: string;
  latencyMs: number | null;
}

export function InfoStrip({ voiceProfileName, activeLlm, latencyMs }: InfoStripProps) {
  return (
    <div className="info-strip">
      <span className="info-chip">{voiceProfileName}</span>
      <span className="info-sep">·</span>
      <span className="info-chip">{activeLlm}</span>
      {latencyMs !== null && (
        <>
          <span className="info-sep">·</span>
          <span className="info-chip info-chip--latency">{latencyMs}ms</span>
        </>
      )}
    </div>
  );
}
