/**
 * Transport abstraction — one media transport behind the SupervoiceClient.
 *
 * Mirrors the shape of a pluggable client transport without any third-party
 * naming. M1 ships `SupervoiceWSTransport`; a LiveKit transport slots in later
 * behind the same interface. Beyond connect/disconnect it surfaces mic + bot
 * audio levels (for the meters) and the session descriptor (for the status
 * panel) via optional callbacks.
 */

export interface TransportCallbacks {
  onConnected?: () => void;
  onDisconnected?: () => void;
  onError?: (message: string) => void;
  /** Mic input level, 0..1, emitted while capturing. */
  onMicLevel?: (level: number) => void;
  /** Bot output level, 0..1, emitted as playback frames arrive. */
  onBotLevel?: (level: number) => void;
  /** The resolved session descriptor from `POST /playground/sessions`. */
  onSession?: (info: Record<string, unknown>) => void;
}

export abstract class Transport {
  protected callbacks: TransportCallbacks = {};

  setCallbacks(callbacks: TransportCallbacks): void {
    this.callbacks = callbacks;
  }

  /** Acquire a session and open the media channel. */
  abstract connect(): Promise<void>;

  /** Tear down the media channel and release devices. */
  abstract disconnect(): Promise<void>;
}
