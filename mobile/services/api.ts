// API client — Phase 8 additions: chat, rules, fixtures with state
// Money is always a decimal string; prices in cents as strings.

import {
  ApiEnvelope,
  StatusResponse,
  FixtureSummary,
  FixtureDetail,
  FixtureState,
  OrderBookResponse,
  Proposal,
  Order,
  Position,
  Limit,
  Device,
  Rule,
  ChatTurn,
  ChatHistory,
} from "../types/api";
import { DEFAULT_SERVER_URL } from "../utils/defaults";

const DEFAULT_BASE = DEFAULT_SERVER_URL;

class ApiClient {
  private token: string | null = null;
  private base: string = DEFAULT_BASE;

  setBase(url: string) {
    this.base = url.trim().replace(/\/+$/, "");
  }

  getBase() {
    return this.base;
  }

  private unauthorizedHandler: (() => void) | null = null;

  onUnauthorized(handler: () => void) {
    this.unauthorizedHandler = handler;
  }

  setToken(token: string | null) {
    this.token = token;
  }

  private async request<T>(
    path: string,
    opts: RequestInit = {}
  ): Promise<ApiEnvelope<T>> {
    const sentToken = this.token;
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
      ...(sentToken ? { Authorization: `Bearer ${sentToken}` } : {}),
      ...(opts.headers as Record<string, string> || {}),
    };

    const resp = await fetch(`${this.base}${path}`, {
      ...opts,
      headers,
    });

    let body: any;
    try {
      body = await resp.json();
    } catch {
      body = {};
    }

    if (resp.status === 401 && sentToken && sentToken === this.token && this.unauthorizedHandler) {
      this.unauthorizedHandler();
    }

    if (!resp.ok) {
      return {
        ok: false,
        error: body.error || { code: "http_error", message: `HTTP ${resp.status}` },
      } as ApiEnvelope<T>;
    }

    return body as ApiEnvelope<T>;
  }

  // ── Status ────────────────────────────────────────────────
  async getStatus(): Promise<ApiEnvelope<StatusResponse>> {
    return this.request<StatusResponse>("/v1/status");
  }

  // ── Fixtures (Phase 8: state filter + detail) ─────────────
  async getFixtures(state?: FixtureState): Promise<ApiEnvelope<FixtureSummary[]>> {
    const params = state && state !== "all" ? `?state=${state}` : "";
    return this.request<FixtureSummary[]>(`/v1/fixtures${params}`);
  }

  async getFixtureDetail(id: string): Promise<ApiEnvelope<FixtureDetail>> {
    return this.request<FixtureDetail>(`/v1/fixtures/${id}`);
  }

  // ── Order book ────────────────────────────────────────────
  async getOrderBook(marketId: string): Promise<ApiEnvelope<OrderBookResponse>> {
    return this.request<OrderBookResponse>(`/v1/markets/${marketId}/book`);
  }

  // ── Proposals ────────────────────────────────────────────
  async getProposals(status?: string): Promise<ApiEnvelope<Proposal[]>> {
    const params = status ? `?status=${status}` : "";
    return this.request<Proposal[]>(`/v1/proposals${params}`);
  }

  async approveProposal(
    id: string,
    body: { amount_usd?: string },
    xConfirm?: string
  ): Promise<ApiEnvelope<Proposal>> {
    return this.request<Proposal>(`/v1/proposals/${id}/approve`, {
      method: "POST",
      body: JSON.stringify(body),
      headers: xConfirm ? { "X-Confirm": xConfirm } : {},
    });
  }

  async rejectProposal(id: string): Promise<ApiEnvelope<Proposal>> {
    return this.request<Proposal>(`/v1/proposals/${id}/reject`, {
      method: "POST",
    });
  }

  // ── Orders ────────────────────────────────────────────────
  async getOrders(): Promise<ApiEnvelope<Order[]>> {
    return this.request<Order[]>("/v1/orders");
  }

  async cancelOrder(id: string, xConfirm?: string): Promise<ApiEnvelope<Order>> {
    return this.request<Order>(`/v1/orders/${id}/cancel`, {
      method: "POST",
      headers: xConfirm ? { "X-Confirm": xConfirm } : {},
    });
  }

  // ── Positions ─────────────────────────────────────────────
  async getPositions(): Promise<ApiEnvelope<Position[]>> {
    return this.request<Position[]>("/v1/positions");
  }

  async closePosition(id: string): Promise<ApiEnvelope<Proposal>> {
    return this.request<Proposal>(`/v1/positions/${id}/close`, {
      method: "POST",
    });
  }

  // ── Limits ────────────────────────────────────────────────
  async getLimits(): Promise<ApiEnvelope<Limit[]>> {
    return this.request<Limit[]>("/v1/limits");
  }

  async putLimits(body: Record<string, number>, xConfirm?: string): Promise<ApiEnvelope<Limit[]>> {
    return this.request<Limit[]>("/v1/limits", {
      method: "PUT",
      body: JSON.stringify(body),
      headers: xConfirm ? { "X-Confirm": xConfirm } : {},
    });
  }

  // ── Pause / resume / panic ────────────────────────────────
  async pause(xConfirm?: string): Promise<ApiEnvelope<StatusResponse>> {
    return this.request<StatusResponse>("/v1/pause", {
      method: "POST",
      headers: xConfirm ? { "X-Confirm": xConfirm } : {},
    });
  }

  async resume(xConfirm?: string): Promise<ApiEnvelope<StatusResponse>> {
    return this.request<StatusResponse>("/v1/resume", {
      method: "POST",
      headers: xConfirm ? { "X-Confirm": xConfirm } : {},
    });
  }

  async panic(xConfirm?: string): Promise<ApiEnvelope<StatusResponse>> {
    return this.request<StatusResponse>("/v1/panic", {
      method: "POST",
      headers: xConfirm ? { "X-Confirm": xConfirm } : {},
    });
  }

  // ── Login / Logout ────────────────────────────────────────
  async login(username: string, password: string): Promise<ApiEnvelope<{ token: string; expires_in: number }>> {
    return this.request<{ token: string; expires_in: number }>("/v1/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
  }

  async logout(): Promise<ApiEnvelope<{ logged_out: boolean }>> {
    return this.request<{ logged_out: boolean }>("/v1/logout", { method: "POST" });
  }

  // ── Pairing / Devices ─────────────────────────────────────
  async pair(code: string, deviceName: string, devicePublicKey: string): Promise<ApiEnvelope<{ device_token: string }>> {
    return this.request<{ device_token: string }>("/v1/pair", {
      method: "POST",
      body: JSON.stringify({ code, device_name: deviceName, device_public_key: devicePublicKey }),
    });
  }

  async getDevices(): Promise<ApiEnvelope<{ devices: Device[] }>> {
    return this.request<{ devices: Device[] }>("/v1/devices");
  }

  async revokeDevice(id: string): Promise<ApiEnvelope<{ revoked: boolean }>> {
    return this.request<{ revoked: boolean }>(`/v1/devices/${id}`, {
      method: "DELETE",
    });
  }

  // ── Rules (Phase 8) ───────────────────────────────────────
  async getRules(status?: string): Promise<ApiEnvelope<Rule[]>> {
    const params = status ? `?status=${status}` : "";
    return this.request<Rule[]>(`/v1/rules${params}`);
  }

  async confirmRule(id: string, xConfirm?: string): Promise<ApiEnvelope<Rule>> {
    return this.request<Rule>(`/v1/rules/${id}/confirm`, {
      method: "POST",
      body: JSON.stringify({}),
      headers: xConfirm ? { "X-Confirm": xConfirm } : {},
    });
  }

  async cancelRule(id: string): Promise<ApiEnvelope<Rule>> {
    return this.request<Rule>(`/v1/rules/${id}/cancel`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  }

  // ── Chat (Phase 8) ────────────────────────────────────────
  async chat(message: string, sessionId?: string | null): Promise<ApiEnvelope<ChatTurn>> {
    return this.request<ChatTurn>("/v1/chat", {
      method: "POST",
      body: JSON.stringify({ message, ...(sessionId ? { session_id: sessionId } : {}) }),
    });
  }

  async getChatHistory(sessionId: string): Promise<ApiEnvelope<ChatHistory>> {
    return this.request<ChatHistory>(`/v1/chat/${sessionId}`);
  }

  async clearChat(sessionId: string): Promise<ApiEnvelope<{ cleared: boolean }>> {
    return this.request<{ cleared: boolean }>(`/v1/chat/${sessionId}`, {
      method: "DELETE",
    });
  }

  // ── Reports ───────────────────────────────────────────────
  async getDailyReport(): Promise<ApiEnvelope<unknown>> {
    return this.request("/v1/reports/daily");
  }

  async getCalibrationReport(): Promise<ApiEnvelope<unknown>> {
    return this.request("/v1/reports/calibration");
  }
}

export const api = new ApiClient();
