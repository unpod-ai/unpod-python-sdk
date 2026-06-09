// Shared UI types for the playground.

export type AppState = "idle" | "connecting" | "active" | "disconnected";

export interface Turn {
  role: "user" | "agent" | "system";
  text: string;
  time: string;
}

export interface MetricSnapshot {
  ttfa_ms: number | null;
  asr_p95_ms: number | null;
  tts_p95_ms: number | null;
  turns: number;
  cost_usd_so_far: number | null;
}

export interface LLMCallEvent {
  turn_id: number;
  node_id: string;
  model: string;
  call_type: "routing" | "generate_reply";
  latency_ms: number;
  tokens_in: number;
  tokens_out: number;
  prompt_messages: unknown[];
  response_json: unknown;
  edge_id: string | null;
}

export interface TurnCompleteEvent {
  turn_id: number;
  from_node: string | null;
  to_node: string | null;
  ttfa_ms: number | null;
  asr_ms: number | null;
  tts_ttfb_ms: number | null;
  llm_call_count: number;
  llm_total_ms: number | null;
}

export interface LogEntry {
  id: number;
  time: string;
  type: string;
  detail: string;
}

export interface SessionInfo {
  agent?: string;
  transport?: string;
  session_id?: string;
  [key: string]: unknown;
}

export const EMPTY_METRICS: MetricSnapshot = {
  ttfa_ms: null,
  asr_p95_ms: null,
  tts_p95_ms: null,
  turns: 0,
  cost_usd_so_far: null,
};

/** Local wall-clock time as `h:mm:ss AM` (matches the reference log style). */
export function clockTime(): string {
  return new Date().toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}
