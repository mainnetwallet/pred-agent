// WebSocket client — event stream from Control API (kit/07_CONTROL_API.md)

import { EventEmitter } from "events";

type WsEvent =
  | { type: "proposal.created"; data: any }
  | { type: "proposal.updated"; data: any }
  | { type: "proposal.expired"; data: any }
  | { type: "order.state"; data: any }
  | { type: "fill"; data: any }
  | { type: "position.updated"; data: any }
  | { type: "book.update"; data: any }
  | { type: "alert"; data: any }
  | { type: "status"; data: any };

export class ControlWebSocket extends (EventEmitter as new () => EventEmitter) {
  private ws: WebSocket | null = null;
  private url: string;
  private token: string | null = null;
  private subscribedMarkets: string[] = [];
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 10;
  private connected = false;

  constructor(url: string) {
    super();
    this.url = url.replace(/^http/, "ws");
  }

  setUrl(url: string) {
    this.url = url.replace(/^http/, "ws");
  }

  setToken(token: string | null) {
    this.token = token;
    if (token && !this.connected) {
      this.connect();
    } else if (!token && this.ws) {
      this.disconnect();
    }
  }

  connect() {
    if (this.ws) {
      this.ws.close();
    }
    const wsUrl = `${this.url}/v1/ws`;
    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      this.connected = true;
      this.reconnectAttempts = 0;
      if (this.token) {
        this.ws?.send(JSON.stringify({ type: "auth", token: this.token }));
      }
      if (this.subscribedMarkets.length > 0) {
        this.ws?.send(JSON.stringify({ type: "subscribe", markets: this.subscribedMarkets }));
      }
      this.emit("connect");
    };

    this.ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        this.emit("message", msg);
        this.emit(msg.type, msg.data);
      } catch {
        // ignore malformed messages
      }
    };

    this.ws.onclose = () => {
      this.connected = false;
      this.emit("disconnect");
      this.scheduleReconnect();
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private scheduleReconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
    }
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this.emit("max_reconnect_reached");
      return;
    }
    this.reconnectAttempts++;
    const delay = Math.min(1000 * 2 ** this.reconnectAttempts, 30000); // 1s to 30s
    this.reconnectTimer = setTimeout(() => {
      this.connect();
    }, delay);
  }

  disconnect() {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.ws?.close();
    this.ws = null;
    this.connected = false;
  }

  subscribe(markets: string[]) {
    this.subscribedMarkets = markets;
    this.ws?.send(JSON.stringify({ type: "subscribe", markets }));
  }

  onConnect(handler: () => void) {
    this.on("connect", handler);
  }

  onDisconnect(handler: () => void) {
    this.on("disconnect", handler);
  }

  onEvent<T extends WsEvent["type"]>(type: T, handler: (data: (WsEvent & { type: T })["data"]) => void) {
    this.on(type, handler);
  }

  isConnected() {
    return this.connected;
  }
}

export const wsClient = new ControlWebSocket(
  process.env.EXPO_PUBLIC_WS_BASE || "ws://127.0.0.1:8000"
);
