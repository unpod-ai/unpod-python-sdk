/**
 * Conversation state — the single source of truth for the playground UI.
 *
 * The state is computed by supervoice (the media worker) from real pipeline
 * frames and pushed down the WS transport; the UI only renders it. These pure
 * mappings turn one `ConvState` into the derived pipeline-segment booleans and
 * the "Agent" pill label — no timers, no `botLevel`-as-state. Keeping them pure
 * makes the mapping unit-testable in isolation (see `convState.test.ts`).
 */

/** The five states the worker can report. */
export type ConvState =
  | "idle"
  | "listening"
  | "thinking"
  | "speaking"
  | "interrupted";

/** Which pipeline segments are lit for a given conversation state. */
export interface PipelineFlags {
  /** mic + STT lit (user speaking / just spoke). */
  sttOn: boolean;
  /** DialogMachine pulsing (agent thinking). */
  dmOn: boolean;
  /** TTS + "Caller hears" lit (agent speaking). */
  ttsOn: boolean;
}

/** Map a conversation state to the lit pipeline segments (pure, no timers). */
export function pipelineFlags(state: ConvState): PipelineFlags {
  return {
    // `interrupted` is a brief flash on the way back to `listening`, so it
    // reads as the listening leg of the pipeline.
    sttOn: state === "listening" || state === "interrupted",
    dmOn: state === "thinking",
    ttsOn: state === "speaking",
  };
}

/** The "Agent" status-pill label for a given conversation state. */
export function agentPill(state: ConvState): string {
  switch (state) {
    case "idle":
      return "Ready";
    case "listening":
      return "Listening";
    case "thinking":
      return "Thinking";
    case "speaking":
      return "Speaking";
    case "interrupted":
      return "Interrupted";
  }
}

/** True while a turn is mid-flight — drives the tab "live" dot. */
export function isTurnActive(state: ConvState): boolean {
  return state !== "idle" && state !== "listening";
}
