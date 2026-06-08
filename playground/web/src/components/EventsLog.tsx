// playground/web/src/components/EventsLog.tsx
import { memo, useEffect, useMemo, useRef, useState } from "react";

import type { LogEntry } from "../types";

interface EventsLogProps {
  entries: LogEntry[];
}

export const EventsLog = memo(function EventsLog({ entries }: EventsLogProps) {
  const [filter, setFilter] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);

  const shown = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter(
      (e) =>
        e.type.toLowerCase().includes(q) || e.detail.toLowerCase().includes(q),
    );
  }, [entries, filter]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [shown]);

  return (
    <section className="events">
      <div className="events__bar">
        <span className="events__title">EVENTS</span>
        <input
          className="events__filter"
          placeholder="Filter"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        />
        <span className="events__count">{shown.length}</span>
      </div>
      <div className="events__list">
        {shown.map((e) => (
          <div key={e.id} className={`event-line event-line--${e.type}`}>
            <span className="event-line__time">{e.time}</span>
            <span className="event-line__type">{e.type}</span>
            <span className="event-line__detail">{e.detail}</span>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
    </section>
  );
});
