// playground/web/src/components/PipelinePanel.tsx
//
// Live transit schematic, driven by real side-channel events:
//   User speaks → [voice stack · STT] → hook user_turn
//   → [SuperDialog · DialogMachine.turn()] → hook agent_turn
//   → [voice stack · TTS] → Caller hears
//
// `phase` is derived from the actual user_turn / agent_turn events in
// AgentView (see derivePipeline), so a turn visibly travels the pipeline as the
// conversation happens.
import { memo, useEffect, useRef, useState } from "react";

import type { PipelineState } from "../types";

type CSSVars = React.CSSProperties & Record<string, string>;

function MicIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 2a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3z" />
      <path d="M19 10v1a7 7 0 0 1-14 0v-1M12 18v4" />
    </svg>
  );
}

function HeadphonesIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M3 14v-2a9 9 0 0 1 18 0v2" />
      <path d="M21 16a2 2 0 0 1-2 2h-1v-5h1a2 2 0 0 1 2 2zM3 16a2 2 0 0 0 2 2h1v-5H5a2 2 0 0 0-2 2z" />
    </svg>
  );
}

function ArrowIcon() {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
      strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round">
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

/** Animated mini-equalizer inside an endpoint, jittering while active. */
function TransitWave({ active, color, n = 7 }: { active: boolean; color: string; n?: number }) {
  const [, force] = useState(0);
  const seeds = useRef(Array.from({ length: n }, () => Math.random()));
  useEffect(() => {
    if (!active) return;
    const id = window.setInterval(() => {
      seeds.current = seeds.current.map(() => Math.random());
      force((v) => v + 1);
    }, 120);
    return () => window.clearInterval(id);
  }, [active]);
  return (
    <div className="tw" style={{ "--twc": color } as CSSVars}>
      {seeds.current.map((s, i) => (
        <span
          key={i}
          style={{
            height: active ? `${24 + s * 76}%` : "18%",
            opacity: active ? 0.5 + s * 0.5 : 0.25,
          }}
        />
      ))}
    </div>
  );
}

function Endpoint({
  side,
  label,
  icon,
  active,
  color,
}: {
  side: "in" | "out";
  label: string;
  icon: "mic" | "headphones";
  active: boolean;
  color: string;
}) {
  return (
    <div className={`ep ${side} ${active ? "on" : ""}`}>
      <span className="ep-ico">{icon === "mic" ? <MicIcon /> : <HeadphonesIcon />}</span>
      <span className="ep-label">{label}</span>
      <TransitWave active={active} color={color} n={7} />
    </div>
  );
}

function Segment({
  label,
  hook,
  active,
  accent,
}: {
  label: string;
  hook: string;
  active: boolean;
  accent: string;
}) {
  return (
    <div className={`seg ${active ? "active" : ""}`} style={{ "--sc": accent } as CSSVars}>
      <span className={`seg-hook mono ${active ? "fired" : ""}`}>hook · {hook}</span>
      <div className="seg-rail">
        <span className="seg-line" />
        <span className="seg-token" />
        <span className="seg-arrow">
          <ArrowIcon />
        </span>
      </div>
      <span className="seg-label mono">{label}</span>
    </div>
  );
}

const DM_CHIPS = [
  { id: "state", label: "flow state" },
  { id: "tools", label: "tools" },
  { id: "memory", label: "memory" },
  { id: "transitions", label: "transitions" },
];

interface PipelinePanelProps {
  live: boolean;
  pipe: PipelineState;
  node: string | null; // current flow node (from flow_node_changed)
}

export const PipelinePanel = memo(function PipelinePanel({
  live,
  pipe,
  node,
}: PipelinePanelProps) {
  const { phase, userText, replyText } = pipe;
  const sttOn = phase === "listen" || phase === "stt";
  const dmOn = phase === "turn" || phase === "tool" || phase === "reply";
  const ttsOn = phase === "tts";
  const seg1On = phase === "stt" || phase === "turn";
  const seg2On = phase === "reply" || phase === "tts";
  const turning = phase === "turn" || phase === "tool";

  const chipLit = (id: string): boolean => {
    if (turning && (id === "state" || id === "transitions")) return true;
    if (phase === "reply" && id === "memory") return true;
    return false;
  };

  const live1 = userText && (phase === "stt" || phase === "turn" || phase === "tool");
  const live2 = replyText && (phase === "reply" || phase === "tts");
  let readout: React.ReactNode = null;
  if (live1) {
    readout = (
      <>
        <span className="ro-who cyan">caller</span>
        <span className="ro-txt">“{userText}”</span>
      </>
    );
  } else if (live2) {
    readout = (
      <>
        <span className="ro-who indigo">agent</span>
        <span className="ro-txt">“{replyText}”</span>
      </>
    );
  }

  return (
    <div className={`pipe ${live ? "is-live" : ""}`}>
      <div className="pipe-cap">SuperDialog · Session</div>

      <div className="pipe-grid">
        <Endpoint side="in" label="User speaks" icon="mic" active={sttOn} color="var(--cyan)" />

        <div className={`frame voice ${sttOn ? "on" : ""}`}>
          <span className="frame-cap mono">voice stack</span>
          <div className="node">
            <span className="node-name">STT</span>
          </div>
        </div>

        <Segment label="user text" hook="user_turn" active={seg1On} accent="var(--cyan)" />

        <div className={`frame super ${dmOn ? "on" : ""} ${turning ? "turning" : ""}`}>
          <span className="frame-cap mono indigo">SuperDialog · your flow</span>
          <div className="node dm">
            <span className="dm-name mono">
              DialogMachine<span className="dm-fn">.turn()</span>
              {turning && <span className="dm-pulse" />}
            </span>
            <div className="dm-chips">
              {DM_CHIPS.map((c) => (
                <span key={c.id} className={`dm-chip mono ${chipLit(c.id) ? "lit" : ""}`}>
                  {c.label}
                </span>
              ))}
            </div>
            {node && dmOn && <span className="dm-node mono">▸ {node}</span>}
          </div>
        </div>

        <Segment label="agent reply" hook="agent_turn" active={seg2On} accent="var(--indigo)" />

        <div className={`frame voice ${ttsOn ? "on" : ""}`}>
          <span className="frame-cap mono">voice stack</span>
          <div className="node">
            <span className="node-name">TTS</span>
          </div>
        </div>

        <Endpoint side="out" label="Caller hears" icon="headphones" active={ttsOn} color="var(--cyan)" />
      </div>

      <div className="pipe-foot">
        <div className={`readout ${readout ? "show" : ""}`}>
          {readout || (
            <span className="ro-idle">
              {live
                ? "Connected — speak to run a turn through the pipeline."
                : "Pick a voice + flow, then Connect to watch a turn travel the pipeline."}
            </span>
          )}
        </div>
      </div>
    </div>
  );
});
