// playground/web/src/components/Sidebar.tsx
import type { Flow } from "../config";
import type { AppState } from "../types";

interface SidebarProps {
  flows: Flow[];
  activeFlow: string;
  currentNode: string | null;
  switching: string | null; // id of the flow currently being switched to
  appState: AppState;
  note: { kind: "ok" | "err"; text: string } | null;
  onSelect: (flowId: string) => void;
}

export function Sidebar({
  flows,
  activeFlow,
  currentNode,
  switching,
  appState,
  note,
  onSelect,
}: SidebarProps) {
  const live = appState === "active";
  // Lock the list while switching, and while connecting (so the chosen start
  // flow can't drift out from under the in-flight connect).
  const locked = switching !== null || appState === "connecting";

  return (
    <aside className="sidebar">
      <div className="sidebar__title">FLOWS</div>
      <div className="sidebar__list">
        {flows.map((f) => {
          const isActive = f.id === activeFlow;
          const isSwitching = switching === f.id;
          return (
            <button
              key={f.id}
              type="button"
              className={`flow-card${isActive ? " flow-card--active" : ""}`}
              onClick={() => onSelect(f.id)}
              disabled={locked}
              aria-pressed={isActive}
              aria-busy={isSwitching}
            >
              <div className="flow-card__head">
                <span className="flow-card__name">
                  {isActive && <span className="flow-card__dot" aria-hidden="true">●</span>}
                  {f.label}
                </span>
                <span className="flow-card__nodes" aria-label={`${f.nodes} nodes`}>
                  {f.nodes}
                </span>
              </div>
              {f.description && (
                <div className="flow-card__desc">{f.description}</div>
              )}
              {isSwitching ? (
                <div className="flow-card__node">switching…</div>
              ) : (
                isActive &&
                live &&
                currentNode && <div className="flow-card__node">▸ {currentNode}</div>
              )}
            </button>
          );
        })}
      </div>
      <div
        className={`sidebar__note${note ? ` sidebar__note--${note.kind}` : ""}`}
        role={note?.kind === "err" ? "alert" : "status"}
        aria-live="polite"
      >
        {note ? note.text : live ? "Tap a flow to switch live" : "Pick a flow, then Connect"}
      </div>
    </aside>
  );
}
