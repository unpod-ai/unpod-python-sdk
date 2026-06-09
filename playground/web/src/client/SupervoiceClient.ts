/**
 * SupervoiceClient — one event/control API over an injected Transport.
 *
 * Transport-agnostic. It surfaces supervoice-named events from two sources:
 *   - the Transport: `connected` / `disconnected` / `error` / `session`
 *   - the harness side-channel WS (`/playground/events`): `ready`, `user_turn`,
 *     `agent_turn`, `metric`, `interruption`, `flow_node_changed`, `error`,
 *     `disconnected`
 *
 * `on(event, …)` targets one event; `onAny(…)` receives every event (for the
 * realtime log). Mic/bot audio levels bypass the event system via dedicated
 * callbacks so they never spam the log.
 */

import { Transport } from "../transport/Transport";

export type SupervoiceEvent =
  | "connected"
  | "disconnected"
  | "ready"
  | "error"
  | "user_turn"
  | "agent_turn"
  | "metric"
  | "interruption"
  | "flow_node_changed"
  | "session";

type Handler = (data: Record<string, unknown>) => void;
type AnyHandler = (event: string, data: Record<string, unknown>) => void;
type LevelHandler = (level: number) => void;

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
  private anyHandlers = new Set<AnyHandler>();
  private micLevelHandlers = new Set<LevelHandler>();
  private botLevelHandlers = new Set<LevelHandler>();

  constructor(options: SupervoiceClientOptions) {
    this.transport = options.transport;
    this.eventsUrl = options.eventsUrl ?? this.defaultEventsUrl();
    this.transport.setCallbacks({
      onConnected: () => this.emit("connected", {}),
      onDisconnected: () => {
        this.emit("disconnected", {});
        // The transport (audio WS) is gone — close the side-channel too so the
        // events WS doesn't leak on server-initiated/ error disconnects.
        this.closeSideChannel();
      },
      onError: (message) => this.emit("error", { message }),
      onSession: (info) => this.emit("session", info),
      onMicLevel: (level) => this.micLevelHandlers.forEach((h) => h(level)),
      onBotLevel: (level) => this.botLevelHandlers.forEach((h) => h(level)),
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

  /** Receive every event (for the realtime log). */
  onAny(handler: AnyHandler): void {
    this.anyHandlers.add(handler);
  }

  onMicLevel(handler: LevelHandler): void {
    this.micLevelHandlers.add(handler);
  }

  onBotLevel(handler: LevelHandler): void {
    this.botLevelHandlers.add(handler);
  }

  private emit(event: SupervoiceEvent, data: Record<string, unknown>): void {
    this.handlers.get(event)?.forEach((h) => h(data));
    this.anyHandlers.forEach((h) => h(event, data));
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

  private closeSideChannel(): void {
    if (this.events) {
      this.events.onmessage = null;
      this.events.close();
      this.events = null;
    }
  }

  async disconnect(): Promise<void> {
    await this.transport.disconnect();
    this.closeSideChannel();
  }
}
