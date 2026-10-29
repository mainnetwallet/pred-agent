import { api } from "../services/api";

function mockFetchOnce(status: number, body: object) {
  const fn = jest.fn().mockResolvedValue({ status, ok: status >= 200 && status < 300, json: async () => body });
  (global as any).fetch = fn;
  return fn;
}

describe("trade / rule creation by tap", () => {
  beforeEach(() => {
    api.setBase("https://example.test");
    api.setToken("tok");
  });

  it("createProposal POSTs market, side and amount to /v1/proposals", async () => {
    const fetchMock = mockFetchOnce(200, { ok: true, data: { id: "p1", status: "PROPOSED" } });
    const r = await api.createProposal({ market_id: "mx1", side: "long", amount_usd: "2" });
    expect(r.ok).toBe(true);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://example.test/v1/proposals");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ market_id: "mx1", side: "long", amount_usd: "2" });
    expect(init.headers.Authorization).toBe("Bearer tok");
  });

  it("createProposal surfaces the server's error message", async () => {
    mockFetchOnce(409, { ok: false, error: { code: "not_proposable", message: "no live price" } });
    const r = await api.createProposal({ market_id: "mx1", side: "short", amount_usd: "2" });
    expect(r.ok).toBe(false);
    expect(r.error?.message).toBe("no live price");
  });

  it("createRule POSTs the rule to /v1/rules", async () => {
    const fetchMock = mockFetchOnce(200, { ok: true, data: { id: "r1", status: "PENDING" } });
    await api.createRule({ market_id: "mx1", kind: "stop_loss", trigger_type: "loss_usd", trigger_value: "2" });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://example.test/v1/rules");
    expect(JSON.parse(init.body)).toMatchObject({ kind: "stop_loss", trigger_type: "loss_usd", trigger_value: "2" });
  });
});
