/**
 * SupervoiceClient — one event/control API over an injected Transport.
 *
 * Transport-agnostic (mirrors how a pluggable client wraps any transport). It
 * surfaces supervoice-named events from two sources:
 *   - the Transport: `connected` / `disconnected` / `error`
 *   - the harness side-channel WS (`/playground/events`): `ready`, `user_turn`,
 *     `agent_turn`, `flow_node_changed`, `error`, `disconnected`
 */

import { Transport } from "../transport/Transport";

export type SupervoiceEvent =
  | "connected"
  | "disconnected"
  | "ready"
  | "error"
  | "user_turn"
  | "agent_turn"
  | "flow_node_changed"
  | "metric";

type Handler = (data: Record<string, unknown>) => void;

export interface SupervoiceClientOptions {
  transport: Transport;
  /** Side-channel events URL (default: `/playground/events`). */
  eventsUrl?: string;
}

export class SupervoiceClient {
  private readonly transport: Transport;
  private readonly eventsUrl: string;
  private events: WebSocket | null = null;
  private handlers = new Map<SupervoiceEvent, Set<Handler>>();

  constructor(options: SupervoiceClientOptions) {
    this.transport = options.transport;
    this.eventsUrl = options.eventsUrl ?? this.defaultEventsUrl();
    this.transport.setCallbacks({
      onConnected: () => this.emit("connected", {}),
      onDisconnected: () => this.emit("disconnected", {}),
      onError: (message) => this.emit("error", { message }),
    });
  }

  private defaultEventsUrl(): string {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    return `${proto}//${location.host}/playground/events`;
  }

  on(event: SupervoiceEvent, handler: Handler): void {
    let set = this.handlers.get(event);
    if (!set) {
      set = new Set();
      this.handlers.set(event, set);
    }
    set.add(handler);
  }

  private emit(event: SupervoiceEvent, data: Record<string, unknown>): void {
    this.handlers.get(event)?.forEach((h) => h(data));
  }

  private openSideChannel(): void {
    const ws = new WebSocket(this.eventsUrl);
    this.events = ws;
    ws.onmessage = (e: MessageEvent) => {
      try {
        const msg = JSON.parse(e.data as string) as {
          type: SupervoiceEvent;
          data: Record<string, unknown>;
        };
        this.emit(msg.type, msg.data ?? {});
      } catch {
        /* ignore malformed side-channel frames */
      }
    };
  }

  async connect(): Promise<void> {
    this.openSideChannel();
    await this.transport.connect();
  }

  async disconnect(): Promise<void> {
    await this.transport.disconnect();
    this.events?.close();
    this.events = null;
  }
}
