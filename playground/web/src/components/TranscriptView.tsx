/** TranscriptView — renders the live conversation turns. */

export interface Turn {
  role: "user" | "agent" | "system";
  text: string;
}

interface TranscriptViewProps {
  turns: Turn[];
}

export function TranscriptView({ turns }: TranscriptViewProps) {
  return (
    <div className="transcript">
      {turns.map((turn, i) => (
        <div key={i} className={`turn ${turn.role}`}>
          {turn.role !== "system" && (
            <div className="turn-label">{turn.role === "user" ? "You" : "Agent"}</div>
          )}
          <div className="msg">{turn.text}</div>
        </div>
      ))}
    </div>
  );
}
