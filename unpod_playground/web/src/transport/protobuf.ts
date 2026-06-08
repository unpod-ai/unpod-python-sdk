/**
 * Supervoice WS frame codec — a self-contained proto3 encoder/decoder for the
 * binary audio frames exchanged over `/ws/audio`.
 *
 * This re-implements the on-the-wire `Frame` schema directly (no third-party
 * client or transport dependency). Each WebSocket binary message is exactly one
 * serialized `Frame` (no length prefix). Schema mirrored here:
 *
 *   message Frame {
 *     oneof frame {
 *       TextFrame          text          = 1;
 *       AudioRawFrame      audio         = 2;
 *       TranscriptionFrame transcription = 3;
 *       MessageFrame       message       = 4;
 *     }
 *   }
 *   message AudioRawFrame      { uint64 id=1; string name=2; bytes audio=3;
 *                                uint32 sample_rate=4; uint32 num_channels=5;
 *                                optional uint64 pts=6; }
 *   message TranscriptionFrame { uint64 id=1; string name=2; string text=3;
 *                                string user_id=4; string timestamp=5; }
 *   message TextFrame          { uint64 id=1; string name=2; string text=3; }
 *   message MessageFrame       { string data=1; }
 *
 * Proto3 wire types used: 0 = varint, 2 = length-delimited.
 */

// ── Outer Frame oneof field numbers ──────────────────────────────────────────

const FRAME_AUDIO = 2;
const FRAME_TRANSCRIPTION = 3;
const FRAME_MESSAGE = 4;

// ── AudioRawFrame field numbers ──────────────────────────────────────────────

const AUDIO_BYTES = 3;
const AUDIO_SAMPLE_RATE = 4;
const AUDIO_NUM_CHANNELS = 5;

// ── Decoded frame union ───────────────────────────────────────────────────────

export type DecodedFrame =
  | { kind: "audio"; pcm: Int16Array; sampleRate: number; numChannels: number }
  | { kind: "transcription"; text: string }
  | { kind: "message"; data: string }
  | { kind: "unknown" };

// ── Varint + key helpers ──────────────────────────────────────────────────────

function pushVarint(out: number[], value: number): void {
  let v = value >>> 0;
  while (v > 0x7f) {
    out.push((v & 0x7f) | 0x80);
    v >>>= 7;
  }
  out.push(v);
}

function pushKey(out: number[], fieldNumber: number, wireType: number): void {
  pushVarint(out, (fieldNumber << 3) | wireType);
}

function pushBytes(out: number[], bytes: Uint8Array): void {
  pushVarint(out, bytes.length);
  for (let i = 0; i < bytes.length; i++) out.push(bytes[i]);
}

// ── Encode: AudioRawFrame wrapped in a Frame ──────────────────────────────────

/** Encode a PCM16 mono buffer as a serialized `Frame{audio=AudioRawFrame}`. */
export function encodeAudioFrame(
  pcm: Int16Array,
  sampleRate: number,
  numChannels = 1,
): Uint8Array {
  const audioBytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);

  const inner: number[] = [];
  pushKey(inner, AUDIO_BYTES, 2);
  pushBytes(inner, audioBytes);
  pushKey(inner, AUDIO_SAMPLE_RATE, 0);
  pushVarint(inner, sampleRate);
  pushKey(inner, AUDIO_NUM_CHANNELS, 0);
  pushVarint(inner, numChannels);

  const outer: number[] = [];
  pushKey(outer, FRAME_AUDIO, 2);
  pushBytes(outer, Uint8Array.from(inner));
  return Uint8Array.from(outer);
}

// ── Decode ────────────────────────────────────────────────────────────────────

class Reader {
  private pos = 0;
  constructor(private readonly buf: Uint8Array) {}

  get done(): boolean {
    return this.pos >= this.buf.length;
  }

  readVarint(): number {
    let result = 0;
    let shift = 0;
    while (true) {
      const byte = this.buf[this.pos++];
      result |= (byte & 0x7f) << shift;
      if ((byte & 0x80) === 0) break;
      shift += 7;
    }
    return result >>> 0;
  }

  readBytes(): Uint8Array {
    const len = this.readVarint();
    const slice = this.buf.subarray(this.pos, this.pos + len);
    this.pos += len;
    return slice;
  }

  /** Skip a field whose key was already consumed, given its wire type. */
  skip(wireType: number): void {
    if (wireType === 0) this.readVarint();
    else if (wireType === 2) this.readBytes();
    else throw new Error(`unsupported wire type ${wireType}`);
  }
}

function decodeAudioRawFrame(bytes: Uint8Array): DecodedFrame {
  const r = new Reader(bytes);
  let pcm = new Int16Array(0);
  let sampleRate = 0;
  let numChannels = 1;
  while (!r.done) {
    const key = r.readVarint();
    const field = key >>> 3;
    const wire = key & 0x7;
    if (field === AUDIO_BYTES && wire === 2) {
      const raw = r.readBytes();
      // Copy into a fresh ArrayBuffer so the Int16 view is 2-byte aligned.
      const copy = new Uint8Array(raw.length);
      copy.set(raw);
      pcm = new Int16Array(copy.buffer);
    } else if (field === AUDIO_SAMPLE_RATE && wire === 0) {
      sampleRate = r.readVarint();
    } else if (field === AUDIO_NUM_CHANNELS && wire === 0) {
      numChannels = r.readVarint();
    } else {
      r.skip(wire);
    }
  }
  return { kind: "audio", pcm, sampleRate, numChannels };
}

function decodeStringField(bytes: Uint8Array, targetField: number): string {
  const r = new Reader(bytes);
  const decoder = new TextDecoder();
  let value = "";
  while (!r.done) {
    const key = r.readVarint();
    const field = key >>> 3;
    const wire = key & 0x7;
    if (field === targetField && wire === 2) {
      value = decoder.decode(r.readBytes());
    } else {
      r.skip(wire);
    }
  }
  return value;
}

/** Decode a serialized `Frame` into a typed union. */
export function decodeFrame(bytes: Uint8Array): DecodedFrame {
  const r = new Reader(bytes);
  if (r.done) return { kind: "unknown" };
  const key = r.readVarint();
  const field = key >>> 3;
  const wire = key & 0x7;
  if (wire !== 2) return { kind: "unknown" };
  const inner = r.readBytes();

  if (field === FRAME_AUDIO) return decodeAudioRawFrame(inner);
  if (field === FRAME_TRANSCRIPTION) {
    return { kind: "transcription", text: decodeStringField(inner, 3) };
  }
  if (field === FRAME_MESSAGE) {
    return { kind: "message", data: decodeStringField(inner, 1) };
  }
  return { kind: "unknown" };
}
