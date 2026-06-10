// playground/web/src/config.ts

export interface VoiceProfile {
  id: string;
  name: string;
  stt?: string;
  tts?: string;
  description?: string;
}

export interface UnpodConfig {
  voice_profiles: VoiceProfile[];
  active_llm: string;
}

export async function fetchConfig(): Promise<UnpodConfig> {
  const resp = await fetch("/playground/config");
  if (!resp.ok) throw new Error(`config fetch failed: HTTP ${resp.status}`);
  return resp.json() as Promise<UnpodConfig>;
}

export interface Flow {
  id: string;
  label: string;
  nodes: number;
  initial_node: string;
  description: string;
}

export interface FlowsResponse {
  flows: Flow[];
  active: string | null;
}

export async function fetchFlows(): Promise<FlowsResponse> {
  const resp = await fetch("/playground/flows");
  if (!resp.ok) throw new Error(`flows fetch failed: HTTP ${resp.status}`);
  return resp.json() as Promise<FlowsResponse>;
}

export interface ControlResult {
  ok: boolean;
  error?: string;
}

/** Switch the live conversation's flow via the harness control plane. */
export async function switchFlow(
  flowId: string,
  preserveMemory = true,
): Promise<ControlResult> {
  let resp: Response;
  try {
    resp = await fetch("/playground/sessions/active/control", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action: "switch_flow",
        params: { flow: flowId, preserve_memory: preserveMemory },
      }),
    });
  } catch (err) {
    return { ok: false, error: (err as Error).message };
  }
  if (!resp.ok) return { ok: false, error: `HTTP ${resp.status}` };
  return resp.json() as Promise<ControlResult>;
}