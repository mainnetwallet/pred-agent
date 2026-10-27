import { api } from "../services/api";

function mockFetchOnce(status: number, body: object) {
  (global as any).fetch = jest.fn().mockResolvedValue({
    status,
    ok: status >= 200 && status < 300,
    json: async () => body,
  });
}

describe("401 handling", () => {
  beforeEach(() => {
    api.setBase("https://example.test");
    api.setToken(null);
  });

  it("logs out when a request sent with the current token gets 401", async () => {
    const handler = jest.fn();
    api.onUnauthorized(handler);
    api.setToken("tok");
    mockFetchOnce(401, { ok: false, error: { code: "unauthorized", message: "x" } });
    await api.getStatus();
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("does not log out when the token was set while the (token-less) request was in flight", async () => {
    const handler = jest.fn();
    api.onUnauthorized(handler);
    (global as any).fetch = jest.fn().mockImplementation(async () => {
      api.setToken("fresh-login-token"); // App's effect syncing the token mid-request
      return { status: 401, ok: false, json: async () => ({ ok: false, error: { code: "unauthorized", message: "x" } }) };
    });
    await api.getStatus();
    expect(handler).not.toHaveBeenCalled();
  });

  it("does not log out on a wrong-password login 401", async () => {
    const handler = jest.fn();
    api.onUnauthorized(handler);
    mockFetchOnce(401, { ok: false, error: { code: "invalid_credentials", message: "bad" } });
    await api.login("admin", "nope");
    expect(handler).not.toHaveBeenCalled();
  });
});
