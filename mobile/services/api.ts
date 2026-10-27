// API client — generated from Control API OpenAPI schema (kit/07_CONTROL_API.md)
// Money is always a decimal string; prices in cents as strings.

import {
  ApiEnvelope,
  StatusResponse,
  FixtureSummary,
  FixtureDetail,
  OrderBookResponse,
  Proposal,
  Order,
  Position,
  Limit,
  Device,
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
    // Remember which token this request was sent with: a 401 only means "session expired"
    // if we actually sent the current token (not if it was set while the request was in flight).
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

  // ---- Status ----
  async getStatus(): Promise<ApiEnvelope<StatusResponse>> {
    return this.request<StatusResponse>("/v1/status");
  }

  // ---- Fixtures ----
  async getFixtures(state?: "live" | "upcoming"): Promise<ApiEnvelope<FixtureSummary[]>> {
    const params = state ? `?state=${state}` : "";
    return this.request<FixtureSummary[]>(`/v1/fixtures${params}`);
  }

  async getFixtureDetail(id: string): Promise<ApiEnvelope<FixtureDetail>> {
    return this.request<FixtureDetail>(`/v1/fixtures/${id}`);
  }

  // ---- Order book ----
  async getOrderBook(marketId: string): Promise<ApiEnvelope<OrderBookResponse>> {
    return this.request<OrderBookResponse>(`/v1/markets/${marketId}/book`);
  }

  // ---- Proposals ----
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

  // ---- Orders ----
  async getOrders(): Promise<ApiEnvelope<Order[]>> {
    return this.request<Order[]>("/v1/orders");
  }

  async cancelOrder(id: string, xConfirm?: string): Promise<ApiEnvelope<Order>> {
    return this.request<Order>(`/v1/orders/${id}/cancel`, {
      method: "POST",
      headers: xConfirm ? { "X-Confirm": xConfirm } : {},
    });
  }

  // ---- Positions ----
  async getPositions(): Promise<ApiEnvelope<Position[]>> {
    return this.request<Position[]>("/v1/positions");
  }

  async closePosition(id: string): Promise<ApiEnvelope<Proposal>> {
    return this.request<Proposal>(`/v1/positions/${id}/close`, {
      method: "POST",
    });
  }

  // ---- Limits ----
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

  // ---- Pause / resume / panic ----
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

  // ---- Login (username/password are fixed in the server env) ----
  async login(username: string, password: string): Promise<ApiEnvelope<{ token: string; expires_in: number }>> {
    return this.request<{ token: string; expires_in: number }>("/v1/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
  }

  // Invalidates the session token on the server (best effort; caller clears local state).
  async logout(): Promise<ApiEnvelope<{ logged_out: boolean }>> {
    return this.request<{ logged_out: boolean }>("/v1/logout", { method: "POST" });
  }

  // ---- Pairing (legacy) ----
  async pair(code: string, deviceName: string, devicePublicKey: string): Promise<ApiEnvelope<{ device_token: string }>> {
    return this.request<{ device_token: string }>("/v1/pair", {
      method: "POST",
      body: JSON.stringify({ code, device_name: deviceName, device_public_key: devicePublicKey }),
    });
  }

  // ---- Devices ----
  async getDevices(): Promise<ApiEnvelope<{ devices: Device[] }>> {
    return this.request<{ devices: Device[] }>("/v1/devices");
  }

  async revokeDevice(id: string): Promise<ApiEnvelope<{ revoked: boolean }>> {
    return this.request<{ revoked: boolean }>(`/v1/devices/${id}`, {
      method: "DELETE",
    });
  }

  // ---- Reports ----
  async getDailyReport(): Promise<ApiEnvelope<unknown>> {
    return this.request("/v1/reports/daily");
  }

  async getCalibrationReport(): Promise<ApiEnvelope<unknown>> {
    return this.request("/v1/reports/calibration");
  }
}

export const api = new ApiClient();
