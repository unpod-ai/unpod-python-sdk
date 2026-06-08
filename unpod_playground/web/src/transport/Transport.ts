/**
 * Transport abstraction — one media transport behind the SupervoiceClient.
 *
 * Mirrors the shape of a pluggable client transport without any third-party
 * naming. M1 ships `SupervoiceWSTransport`; a LiveKit transport slots in later
 * behind the same interface.
 */

export interface TransportCallbacks {
  onConnected?: () => void;
  onDisconnected?: () => void;
  onError?: (message: string) => void;
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
