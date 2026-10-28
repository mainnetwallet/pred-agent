// Jest tests for mobile app — money formatting utilities (pure functions, no JSX)

import {
  formatUsd,
  formatPriceCents,
  formatEdge,
  formatShares,
  isLiveMode,
  isPaperMode,
} from "../utils/format";

describe("Money formatting", () => {
  test("formatUsd formats currency correctly", () => {
    expect(formatUsd("1.98")).toBe("$1.98");
    expect(formatUsd("0.04")).toBe("$0.04");
    expect(formatUsd("10.50")).toBe("$10.50");
  });

  test("formatUsd handles invalid input", () => {
    expect(formatUsd("invalid")).toBe("—");
  });

  test("formatPriceCents converts cents to dollars", () => {
    expect(formatPriceCents("5200")).toBe("$52.00");
    expect(formatPriceCents("550000")).toBe("$5500.00");
  });

  test("formatEdge formats with sign", () => {
    expect(formatEdge("4.6")).toBe("+4.6 pp");
    expect(formatEdge("-2.3")).toBe("-2.3 pp");
  });

  test("formatShares returns quantity string", () => {
    expect(formatShares("3.8")).toBe("3.8");
    expect(formatShares("100")).toBe("100");
  });

  test("isLiveMode and isPaperMode", () => {
    expect(isLiveMode("LIVE")).toBe(true);
    expect(isLiveMode("PAPER")).toBe(false);
    expect(isPaperMode("PAPER")).toBe(true);
    expect(isPaperMode("LIVE")).toBe(false);
  });
});
