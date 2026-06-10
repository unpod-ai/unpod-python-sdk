// playground/web/src/components/FlowsList.tsx
import { useMemo, useState } from "react";

import type { Flow } from "../config";
import type { AppState } from "../types";

type CSSVars = React.CSSProperties & Record<string, string>;

const ACCENTS = ["var(--indigo)", "var(--mint)", "var(--blue)", "var(--amber)", "var(--rose)"];
const accentFor = (i: number) => ACCENTS[i % ACCENTS.length];

interface FlowsListProps {
  flows: Flow[];
  activeFlow: string;
  currentNode: string | null;
  switching: string | null;
  appState: AppState;
  note: { kind: "ok" | "err"; text: string } | null;
  onSelect: (flowId: string) => void;
}

export function FlowsList({
  flows,
  activeFlow,
  currentNode,
  switching,
  appState,
  note,
  onSelect,
}: FlowsListProps) {
  const [q, setQ] = useState("");
  const live = appState === "active";
  const locked = switching !== null || appState === "connecting";

  // Stable accent per flow id (avoids O(n²) indexOf inside the map).
  const accentIndex = useMemo(
    () => new Map(flows.map((f, i) => [f.id, i])),
    [flows],
  );

  const list = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return needle
      ? flows.filter(
          (f) =>
            f.label.toLowerCase().includes(needle) ||
            f.description.toLowerCase().includes(needle),
        )
      : flows;
  }, [flows, q]);

  return (
    <div className="flows-rail">
      <div className="flows-head">
        <div className="rail-label">Flows</div>
        <span className="flows-count mono">{flows.length}</span>
      </div>

      <div className="flows-search">
        <span aria-hidden="true">⌕</span>
        <input
          placeholder="Search flows"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search flows"
        />
      </div>

      <div className="flows-scroll">
        {list.map((f) => {
          const i = accentIndex.get(f.id) ?? 0;
          const isActive = f.id === activeFlow;
          const isSwitching = switching === f.id;
          return (
            <button
              key={f.id}
              type="button"
              className={`flow-item ${isActive ? "sel" : ""}`}
              style={{ "--fa": accentFor(i) } as CSSVars}
              onClick={() => onSelect(f.id)}
              disabled={locked}
              aria-pressed={isActive}
              aria-busy={isSwitching}
            >
              <span className="flow-bar" />
              <div className="flow-main">
                <div className="flow-top">
                  <span className="flow-name">{f.label}</span>
                  <span className="flow-turns mono" aria-label={`${f.nodes} nodes`}>
                    {f.nodes}
                  </span>
                </div>
                <p className="flow-prompt">{f.description}</p>
                {isSwitching ? (
                  <div className="flow-node mono">switching…</div>
                ) : (
                  isActive &&
                  live &&
                  currentNode && <div className="flow-node mono">▸ {currentNode}</div>
                )}
              </div>
            </button>
          );
        })}
        {list.length === 0 && <div className="flows-empty">No flows match.</div>}
      </div>

      <div
        className={`flows-note${note ? ` flows-note--${note.kind}` : ""}`}
        role="status"
        aria-live={note?.kind === "err" ? "assertive" : "polite"}
      >
        {note
          ? note.text
          : live
            ? "Tap a flow to switch live"
            : "Pick a flow, then Connect"}
      </div>

      <button className="create-flow" type="button" disabled title="Coming soon">
        <span className="cf-ico">+</span>
        <span className="cf-body">
          <span className="cf-title">Create a flow</span>
          <span className="cf-sub">Visual builder — connect your reference</span>
        </span>
      </button>
    </div>
  );
}
