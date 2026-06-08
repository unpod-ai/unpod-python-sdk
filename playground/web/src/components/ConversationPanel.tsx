// playground/web/src/components/ConversationPanel.tsx
import { memo, useEffect, useRef } from "react";

import type { AppState, Turn } from "../types";

interface ConversationPanelProps {
  turns: Turn[];
  appState: AppState;
}

function roleLabel(role: Turn["role"]): string {
  if (role === "user") return "user";
  if (role === "agent") return "assistant";
  return "system";
}

export const ConversationPanel = memo(function ConversationPanel({
  turns,
  appState,
}: ConversationPanelProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [turns]);

  return (
    <div className="conversation">
      <div className="conversation__scroll">
        {turns.length === 0 && (
          <p className="conversation__empty">
            {appState === "active"
              ? "Listening… say hello to the agent."
              : "Connect and speak to start a conversation."}
          </p>
        )}
        {turns.map((turn, i) => (
          <div key={i} className={`turn turn--${turn.role}`}>
            <div className="turn__head">
              <span className={`turn__role turn__role--${turn.role}`}>
                {roleLabel(turn.role)}
              </span>
              <span className="turn__time">{turn.time}</span>
            </div>
            <div className="turn__text">{turn.text}</div>
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      <div className="conversation__input">
        <input
          type="text"
          placeholder={
            appState === "active"
              ? "Voice-first — speak to the agent"
              : "Connect to talk"
          }
          disabled
        />
        <button className="conversation__send" disabled aria-label="Send">
          ➤
        </button>
      </div>
    </div>
  );
});
