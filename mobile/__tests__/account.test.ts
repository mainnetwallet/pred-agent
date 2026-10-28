// Account summary: cash vs positions, and failures never shown as $0.00.
import { summarizeAccount } from "../utils/format";
import type { AccountView } from "../types/api";

const base: AccountView = {
  state: "ok",
  auth_mode: "jwt",
  code: null,
  message: null,
  wallet_address: "0xEOA",
  proxy_address: "0xSAFE",
  trading_enabled: true,
  cash_available_usd: "12.34",
  positions_cost_usd: "5.00",
  positions_payout_usd: "9.50",
  notes: "",
  updated_at: null,
};

describe("summarizeAccount", () => {
  test("ok: shows cash, Safe + wallet, positions separately", () => {
    const s = summarizeAccount(base, "12.34");
    expect(s).toMatchObject({
      cash: "$12.34",
      positionsCost: "$5.00",
      positionsPayout: "$9.50",
      safeAddress: "0xSAFE",
      walletAddress: "0xEOA",
      headline: null,
      ok: true,
    });
  });

  test("a real zero balance is shown as $0.00", () => {
    expect(summarizeAccount({ ...base, cash_available_usd: "0" }, "0").cash).toBe("$0.00");
  });

  test.each(["unauthenticated", "session_expired", "error", "not_configured"] as const)(
    "%s never shows $0.00 and explains why",
    (state) => {
      const s = summarizeAccount(
        { ...base, state, cash_available_usd: null, message: "reason text" },
        null
      );
      expect(s.cash).toBe("—");
      expect(s.headline).toBeTruthy();
      expect(s.detail).toBe("reason text");
      expect(s.ok).toBe(false);
    }
  );

  test("loading before the first response", () => {
    const s = summarizeAccount(null, null);
    expect(s.cash).toBe("—");
    expect(s.headline).toBe("Loading account…");
  });

  test("trading disabled still shows the balance with a warning", () => {
    const s = summarizeAccount(
      { ...base, trading_enabled: false, code: "trading_disabled", message: "Complete Safe approval" },
      "12.34"
    );
    expect(s.cash).toBe("$12.34");
    expect(s.headline).toBe("Trading disabled");
    expect(s.detail).toBe("Complete Safe approval");
  });

  test("older server without account block falls back to legacy balance", () => {
    expect(summarizeAccount(null, "7.00").cash).toBe("$7.00");
  });
});
