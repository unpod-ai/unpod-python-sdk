/**
 * Web Audio helpers — microphone capture (→16 kHz PCM16) and playback
 * (24 kHz PCM16 → speakers). Self-contained; no third-party audio library.
 */

export const MIC_SAMPLE_RATE = 16000;

function floatToPcm16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

function pcm16ToFloat(input: Int16Array): Float32Array {
  const out = new Float32Array(input.length);
  for (let i = 0; i < input.length; i++) out[i] = input[i] / 0x8000;
  return out;
}

/** Linear-resample a Float32 buffer from `inRate` to `outRate`. */
function resample(input: Float32Array, inRate: number, outRate: number): Float32Array {
  if (inRate === outRate) return input;
  const ratio = inRate / outRate;
  const outLen = Math.floor(input.length / ratio);
  const out = new Float32Array(outLen);
  for (let i = 0; i < outLen; i++) {
    const srcPos = i * ratio;
    const i0 = Math.floor(srcPos);
    const i1 = Math.min(i0 + 1, input.length - 1);
    const frac = srcPos - i0;
    out[i] = input[i0] * (1 - frac) + input[i1] * frac;
  }
  return out;
}

/** Captures the mic and emits 16 kHz mono PCM16 chunks via `onChunk`. */
export class MicCapture {
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private processor: ScriptProcessorNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;

  constructor(private readonly onChunk: (pcm: Int16Array) => void) {}

  async start(): Promise<void> {
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
    });
    this.ctx = new AudioContext();
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.processor = this.ctx.createScriptProcessor(4096, 1, 1);

    const inRate = this.ctx.sampleRate;
    this.processor.onaudioprocess = (e: AudioProcessingEvent) => {
      const input = e.inputBuffer.getChannelData(0);
      const down = resample(input, inRate, MIC_SAMPLE_RATE);
      this.onChunk(floatToPcm16(down));
    };

    // Keep the graph alive without feeding the mic back to the speakers.
    const silent = this.ctx.createGain();
    silent.gain.value = 0;
    this.source.connect(this.processor);
    this.processor.connect(silent);
    silent.connect(this.ctx.destination);
  }

  async stop(): Promise<void> {
    this.processor?.disconnect();
    this.source?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctx?.close();
    this.ctx = null;
    this.stream = null;
    this.processor = null;
    this.source = null;
  }
}

/** Schedules incoming PCM16 chunks for gapless playback. */
export class AudioPlayer {
  private ctx: AudioContext | null = null;
  private playHead = 0;

  start(): void {
    this.ctx = new AudioContext();
    this.playHead = this.ctx.currentTime;
  }

  enqueue(pcm: Int16Array, sampleRate: number): void {
    if (!this.ctx || pcm.length === 0) return;
    const float = pcm16ToFloat(pcm);
    const buffer = this.ctx.createBuffer(1, float.length, sampleRate);
    buffer.getChannelData(0).set(float);
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.ctx.destination);
    const now = this.ctx.currentTime;
    if (this.playHead < now) this.playHead = now;
    src.start(this.playHead);
    this.playHead += buffer.duration;
  }

  async stop(): Promise<void> {
    await this.ctx?.close();
    this.ctx = null;
  }
}
