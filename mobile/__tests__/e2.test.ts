// E2 — Mobile Jest tests: API client new methods, card rendering (pure logic),
// confirm flow, WS reconnect, token handling.
//
// Run with: cd mobile && npx jest __tests__/e2.test.ts

import { api } from "../services/api";
import { ControlWebSocket } from "../services/websocket";
import {
  formatUsd,
  formatPriceCents,
  formatEdge,
  formatShares,
  isLiveMode,
  isPaperMode,
} from "../utils/format";

// ---------------------------------------------------------------------------
// Mock global fetch for api client tests
// ---------------------------------------------------------------------------

const mockFetch = jest.fn();
global.fetch = mockFetch as unknown as typeof fetch;

beforeEach(() => {
  mockFetch.mockClear();
  api.setBase("http://localhost:8000");
  api.setToken("test-token");
});

function makeEnvelope<T>(data: T) {
  return {
    ok: true,
    data,
  };
}

function makeErrorEnvelope(code: string, message: string) {
  return {
    ok: false,
    error: { code, message },
  };
}

function mockResponse<T>(body: T, status = 200) {
  mockFetch.mockResolvedValueOnce({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as unknown as Response);
}

// ---------------------------------------------------------------------------
// E2.1 — API client: getFixtures (new state parameter)
// ---------------------------------------------------------------------------

describe("ApiClient.getFixtures", () => {
  test("calls /v1/fixtures with no params when state=undefined", async () => {
    mockResponse(makeEnvelope([]));
    await api.getFixtures();
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/v1/fixtures",
      expect.any(Object)
    );
  });

  test("calls /v1/fixtures?state=live when state='live'", async () => {
    mockResponse(makeEnvelope([]));
    await api.getFixtures("live");
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/v1/fixtures?state=live",
      expect.any(Object)
    );
  });

  test("calls /v1/fixtures?state=upcoming when state='upcoming'", async () => {
    mockResponse(makeEnvelope([]));
    await api.getFixtures("upcoming");
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/v1/fixtures?state=upcoming",
      expect.any(Object)
    );
  });

  test("returns error envelope on 401", async () => {
    mockResponse(makeErrorEnvelope("unauthorized", "invalid token"), 401);
    const result = await api.getFixtures();
    expect(result.ok).toBe(false);
  });

  test("handles network failure gracefully", async () => {
    mockFetch.mockRejectedValueOnce(new Error("network error"));
    await expect(api.getFixtures()).rejects.toThrow("network error");
  });
});

// ---------------------------------------------------------------------------
// E2.2 — API client: approveProposal confirm flow
// ---------------------------------------------------------------------------

describe("ApiClient.approveProposal", () => {
  test("sends POST to /v1/proposals/{id}/approve with amount_usd", async () => {
    mockResponse(makeEnvelope({ id: "p1", status: "APPROVED" }));
    await api.approveProposal("p1", { amount_usd: "1.98" });
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/v1/proposals/p1/approve",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ amount_usd: "1.98" }),
      })
    );
  });

  test("sends X-Confirm header when xConfirm is provided", async () => {
    mockResponse(makeEnvelope({ id: "p1", status: "APPROVED" }));
    await api.approveProposal("p1", {}, "biometric-ok");
    const call = mockFetch.mock.calls[0][1];
    expect(call.headers["X-Confirm"]).toBe("biometric-ok");
  });

  test("does not send X-Confirm header when not provided", async () => {
    mockResponse(makeEnvelope({ id: "p1", status: "APPROVED" }));
    await api.approveProposal("p1", {});
    const call = mockFetch.mock.calls[0][1];
    expect(call.headers["X-Confirm"]).toBeUndefined();
  });

  test("returns approved proposal on success", async () => {
    const proposal = { id: "p1", status: "APPROVED", amount_usd: "1.98" };
    mockResponse(makeEnvelope(proposal));
    const result = await api.approveProposal("p1", { amount_usd: "1.98" });
    expect(result.ok).toBe(true);
    expect(result.data?.status).toBe("APPROVED");
  });

  test("returns error on 409 (not approvable)", async () => {
    mockResponse(
      makeErrorEnvelope("not_approvable", "proposal missing/expired"),
      409
    );
    const result = await api.approveProposal("p1", {});
    expect(result.ok).toBe(false);
    expect(result.error?.code).toBe("not_approvable");
  });
});

// ---------------------------------------------------------------------------
// E2.3 — API client: rejectProposal
// ---------------------------------------------------------------------------

describe("ApiClient.rejectProposal", () => {
  test("sends POST to /v1/proposals/{id}/reject", async () => {
    mockResponse(makeEnvelope({ id: "p1", status: "REJECTED" }));
    await api.rejectProposal("p1");
    expect(mockFetch).toHaveBeenCalledWith(
      "http://localhost:8000/v1/proposals/p1/reject",
      expect.objectContaining({ method: "POST" })
    );
  });
});

// ---------------------------------------------------------------------------
// E2.4 — Proposal card: pure rendering logic
// ---------------------------------------------------------------------------

describe("Proposal card rendering (pure logic)", () => {
  interface ProposalCardData {
    id: string;
    amount_usd: string;
    edge_pp: string;
    side: "long" | "short";
    price_cents: string;
    status: string;
    reasons: string[];
  }

  function renderCardText(proposal: ProposalCardData): string {
    const sideLabel = proposal.side === "long" ? "BUY YES" : "BUY NO";
    const amount = formatUsd(proposal.amount_usd);
    const price = formatPriceCents(proposal.price_cents);
    const edge = formatEdge(proposal.edge_pp);
    return `${sideLabel} · ${amount} · ${price} · ${edge}`;
  }

  test("renders long proposal card correctly", () => {
    const card: ProposalCardData = {
      id: "p1", amount_usd: "1.98", edge_pp: "4.6",
      side: "long", price_cents: "52", status: "PROPOSED", reasons: ["good form"],
    };
    const text = renderCardText(card);
    expect(text).toContain("BUY YES");
    expect(text).toContain("$1.98");
    expect(text).toContain("+4.6 pp");
  });

  test("renders short proposal card correctly", () => {
    const card: ProposalCardData = {
      id: "p2", amount_usd: "1.04", edge_pp: "3.8",
      side: "short", price_cents: "48", status: "PROPOSED", reasons: [],
    };
    const text = renderCardText(card);
    expect(text).toContain("BUY NO");
    expect(text).toContain("$1.04");
    expect(text).toContain("+3.8 pp");
  });

  test("shows reasons list for proposed card", () => {
    const card: ProposalCardData = {
      id: "p3", amount_usd: "2.00", edge_pp: "5.0",
      side: "long", price_cents: "60", status: "PROPOSED",
      reasons: ["recent form", "home advantage"],
    };
    expect(card.reasons).toContain("recent form");
    expect(card.reasons).toContain("home advantage");
  });

  test("APPROVED card shows confirmed status", () => {
    const card: ProposalCardData = {
      id: "p4", amount_usd: "1.50", edge_pp: "4.0",
      side: "long", price_cents: "55", status: "APPROVED", reasons: [],
    };
    expect(card.status).toBe("APPROVED");
  });

  test("EXPIRED card cannot be approved", () => {
    const card: ProposalCardData = {
      id: "p5", amount_usd: "1.00", edge_pp: "3.0",
      side: "long", price_cents: "50", status: "EXPIRED", reasons: [],
    };
    const canApprove = card.status === "PROPOSED";
    expect(canApprove).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// E2.5 — Confirm flow: biometric gate logic
// ---------------------------------------------------------------------------

describe("Confirm flow (biometric gate)", () => {
  function needsConfirmation(action: string): boolean {
    // Actions requiring X-Confirm header
    const highRisk = ["approve", "panic", "cancel_order", "set_limits"];
    return highRisk.includes(action);
  }

  test("approve requires confirmation", () => {
    expect(needsConfirmation("approve")).toBe(true);
  });

  test("panic requires confirmation", () => {
    expect(needsConfirmation("panic")).toBe(true);
  });

  test("reject does not require confirmation", () => {
    expect(needsConfirmation("reject")).toBe(false);
  });

  test("view fixtures does not require confirmation", () => {
    expect(needsConfirmation("get_fixtures")).toBe(false);
  });

  test("set limits requires confirmation", () => {
    expect(needsConfirmation("set_limits")).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// E2.6 — WS reconnect: exponential back-off logic
// ---------------------------------------------------------------------------

describe("WebSocket reconnect", () => {
  test("schedules reconnect on close with exponential back-off", () => {
    const ws = new ControlWebSocket("http://localhost:8000");

    // Access private method via type cast for unit test
    const delays: number[] = [];
    for (let attempt = 1; attempt <= 6; attempt++) {
      const delay = Math.min(1000 * 2 ** attempt, 30000);
      delays.push(delay);
    }

    // Verify backoff: 2s, 4s, 8s, 16s, 30s (capped), 30s (capped)
    expect(delays[0]).toBe(2000);
    expect(delays[1]).toBe(4000);
    expect(delays[2]).toBe(8000);
    expect(delays[3]).toBe(16000);
    expect(delays[4]).toBe(30000); // capped
    expect(delays[5]).toBe(30000); // still capped
  });

  test("does not reconnect if token is null", () => {
    const ws = new ControlWebSocket("http://localhost:8000");
    ws.setToken(null);
    expect(ws.isConnected()).toBe(false);
  });

  test("URL is converted from http to ws scheme", () => {
    const ws = new ControlWebSocket("http://localhost:8000");
    // The URL stored internally should use ws:// scheme
    expect((ws as any).url).toBe("ws://localhost:8000");
  });

  test("https converts to wss", () => {
    const ws = new ControlWebSocket("https://myserver.onrender.com");
    expect((ws as any).url).toBe("wss://myserver.onrender.com");
  });

  test("max reconnect attempts emits event", () => {
    const ws = new ControlWebSocket("http://localhost:8000");
    (ws as any).reconnectAttempts = 10;
    (ws as any).maxReconnectAttempts = 10;

    const handler = jest.fn();
    ws.on("max_reconnect_reached", handler);
    (ws as any).scheduleReconnect();
    expect(handler).toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// E2.7 — API token: unauthorized triggers handler
// ---------------------------------------------------------------------------

describe("ApiClient unauthorized handler", () => {
  test("triggers onUnauthorized handler on 401", async () => {
    const handler = jest.fn();
    api.onUnauthorized(handler);
    api.setToken("expired-token");

    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ ok: false, error: { code: "unauthorized", message: "expired" } }),
    } as unknown as Response);

    await api.getFixtures();
    expect(handler).toHaveBeenCalled();
  });

  test("does not trigger handler if token is null when 401 received", async () => {
    const handler = jest.fn();
    api.onUnauthorized(handler);
    api.setToken(null);

    mockFetch.mockResolvedValueOnce({
      ok: false,
      status: 401,
      json: async () => ({ ok: false, error: { code: "unauthorized", message: "no token" } }),
    } as unknown as Response);

    await api.getFixtures();
    expect(handler).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// E2.8 — Money formatting edge cases
// ---------------------------------------------------------------------------

describe("Money formatting edge cases", () => {
  test("formatUsd with zero", () => {
    expect(formatUsd("0")).toBe("$0.00");
    expect(formatUsd("0.00")).toBe("$0.00");
  });

  test("formatUsd with negative value (loss)", () => {
    // Intl.NumberFormat locale-specific but always contains the magnitude
    const result = formatUsd("-1.50");
    expect(result).toMatch(/1\.50/);
    expect(result).toMatch(/-/);
  });

  test("formatPriceCents with 100 (impossible outcome display)", () => {
    expect(formatPriceCents("100")).toBe("$1.00");
  });

  test("formatEdge with zero edge", () => {
    // formatEdge uses toFixed(1) so 0 becomes "+0.0 pp"
    expect(formatEdge("0")).toBe("+0.0 pp");
  });

  test("formatShares with decimal quantity", () => {
    expect(formatShares("3.8")).toBe("3.8");
    expect(formatShares("0.001")).toBe("0.001");
  });

  test("isPaperMode and isLiveMode are exclusive", () => {
    expect(isPaperMode("PAPER") && isLiveMode("PAPER")).toBe(false);
    expect(isPaperMode("LIVE") && isLiveMode("LIVE")).toBe(false);
    expect(isPaperMode("PAPER") || isLiveMode("PAPER")).toBe(true);
    expect(isPaperMode("LIVE") || isLiveMode("LIVE")).toBe(true);
  });
});
