// playground/web/src/components/VoiceProfilePanel.tsx
import { useEffect, useRef, useState } from "react";

import type { VoiceProfile } from "../config";

type CSSVars = React.CSSProperties & Record<string, string>;

// Deterministic accent per profile (no color field comes from the backend).
const SWATCHES = ["var(--indigo)", "var(--amber)", "var(--mint)", "var(--blue)"];
const swatchFor = (i: number) => SWATCHES[i % SWATCHES.length];

function meta(vp: VoiceProfile): string {
  return [vp.stt, vp.tts].filter(Boolean).join(" · ");
}

function ChevDown() {
  return (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

interface VoiceProfilePanelProps {
  voiceProfiles: VoiceProfile[];
  selected: string;
  onChange: (id: string) => void;
  disabled: boolean;
}

export function VoiceProfilePanel({
  voiceProfiles,
  selected,
  onChange,
  disabled,
}: VoiceProfilePanelProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const selIdx = voiceProfiles.findIndex((v) => v.id === selected);
  const sel = selIdx >= 0 ? voiceProfiles[selIdx] : undefined;

  useEffect(() => {
    if (!open) return;
    const h = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [open]);

  return (
    <div className="rail-section voices">
      <div className="rail-label">Voice profile</div>
      <div
        className={`voice-select ${open ? "open" : ""}`}
        ref={ref}
        onKeyDown={(e) => {
          if (e.key === "Escape" && open) {
            setOpen(false);
            ref.current?.querySelector<HTMLButtonElement>(".vs-trigger")?.focus();
          }
        }}
      >
        <button
          className={`vs-trigger ${sel ? "has" : ""}`}
          onClick={() => !disabled && setOpen((o) => !o)}
          disabled={disabled}
          aria-haspopup="listbox"
          aria-expanded={open}
          style={sel ? ({ "--vc": swatchFor(selIdx) } as CSSVars) : undefined}
        >
          {sel ? (
            <>
              <span className="vs-swatch" style={{ background: swatchFor(selIdx) }} />
              <span className="vs-name">{sel.name}</span>
              {meta(sel) && <span className="vs-meta mono">{meta(sel)}</span>}
            </>
          ) : (
            <span className="vs-ph">Choose a voice…</span>
          )}
          <span className="vs-chev"><ChevDown /></span>
        </button>
        {open && (
          <div className="vs-menu" role="listbox" aria-label="Voice profile">
            {voiceProfiles.map((v, i) => (
              <button
                key={v.id}
                role="option"
                aria-selected={selected === v.id}
                className={`vs-opt ${selected === v.id ? "sel" : ""}`}
                style={{ "--vc": swatchFor(i) } as CSSVars}
                onClick={() => {
                  onChange(v.id);
                  setOpen(false);
                }}
              >
                <span className="vs-swatch" style={{ background: swatchFor(i) }} />
                <span className="vs-opt-main">
                  <span className="vs-opt-top">
                    <span className="vs-name">{v.name}</span>
                  </span>
                  {meta(v) && <span className="vs-opt-meta mono">{meta(v)}</span>}
                </span>
                {selected === v.id && <span className="vs-check">✓</span>}
              </button>
            ))}
            {voiceProfiles.length === 0 && (
              <div className="vs-ph" style={{ padding: "8px 10px" }}>
                No voice profiles available.
              </div>
            )}
          </div>
        )}
      </div>
      {sel?.description && <p className="vs-tone">{sel.description}</p>}
    </div>
  );
}
