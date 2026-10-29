import { normalizeOrder, normalizeOrders } from "../utils/normalize";

describe("normalizeOrder", () => {
  it("maps paper `status` to an uppercase-able `state` (was crashing the Position screen)", () => {
    const o = normalizeOrder({ id: "o1", side: "long", price_cents: "55", quantity: "3", status: "open" });
    expect(o.state).toBe("open");
    expect(() => o.state.toUpperCase()).not.toThrow();
  });

  it("prefers `state` when the server sends it", () => {
    expect(normalizeOrder({ id: "o1", state: "FILLED", status: "open" }).state).toBe("FILLED");
  });

  it("never leaves string fields undefined", () => {
    const o = normalizeOrder({});
    expect(o.state).toBe("UNKNOWN");
    expect(o.side).toBe("long");
    expect(o.price_cents).toBe("0");
    expect(o.quantity).toBe("0");
    expect(o.filled_qty).toBe("0");
    expect(o.reduce_only).toBe(false);
  });

  it("tolerates null / non-array payloads", () => {
    expect(normalizeOrders(null)).toEqual([]);
    expect(normalizeOrders(undefined)).toEqual([]);
    expect(normalizeOrder(null).state).toBe("UNKNOWN");
  });
});
