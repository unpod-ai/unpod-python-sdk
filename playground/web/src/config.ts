// playground/web/src/config.ts

export interface VoiceProfile {
  id: string;
  name: string;
}

export interface FlowOption {
  id: string;
  name: string;
  description: string;
}

export interface UnpodConfig {
  voice_profiles: VoiceProfile[];
  flows: FlowOption[];
  active_llm: string;
}

export async function fetchConfig(): Promise<UnpodConfig> {
  const resp = await fetch("/playground/config");
  if (!resp.ok) throw new Error(`config fetch failed: HTTP ${resp.status}`);
  return resp.json() as Promise<UnpodConfig>;
}