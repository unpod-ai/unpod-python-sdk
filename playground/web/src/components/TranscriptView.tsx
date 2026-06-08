// playground/web/src/components/TranscriptView.tsx
import { useEffect, useRef } from "react";

export interface Turn {
  role: "user" | "agent" | "system";
  text: string;
}

interface TranscriptViewProps {
  turns: Turn[];
}

export function TranscriptView({ turns }: TranscriptViewProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns]);

  return (
    <div className="transcript">
      {turns.map((turn, i) => (
        <div key={i} className={`bubble bubble--${turn.role}`}>
          {turn.role !== "system" && (
            <div className="bubble__label">
              {turn.role === "user" ? "You" : "Agent"}
            </div>
          )}
          <div className="bubble__text">{turn.text}</div>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}
