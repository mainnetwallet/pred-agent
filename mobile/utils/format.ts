// Money formatting utilities — decimal strings, no float math for values that get sent

import { format } from "date-fns";

export function formatUsd(amount: string | number): string {
  const num = typeof amount === "string" ? parseFloat(amount) : amount;
  if (isNaN(num)) return "—"; // never fake a zero for an unparseable amount
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(num);
}

export function formatPriceCents(priceCents: string | number): string {
  const num = typeof priceCents === "string" ? parseFloat(priceCents) : priceCents;
  if (isNaN(num)) return "0.00";
  return `$${(num / 100).toFixed(2)}`;
}

export function formatShares(quantity: string | number): string {
  const num = typeof quantity === "string" ? parseFloat(quantity) : quantity;
  if (isNaN(num)) return "0";
  return num.toString();
}

export function formatPercent(value: number | string, decimals = 1): string {
  const num = typeof value === "string" ? parseFloat(value) : value;
  if (isNaN(num)) return "0%";
  return `${num.toFixed(decimals)}%`;
}

export function formatEdge(edgePp: string | number): string {
  const num = typeof edgePp === "string" ? parseFloat(edgePp) : edgePp;
  if (isNaN(num)) return "0.0 pp";
  const sign = num >= 0 ? "+" : "";
  return `${sign}${num.toFixed(1)} pp`;
}

export function formatDateTime(isoString: string | null | undefined): string {
  if (!isoString) return "—";
  try {
    return format(new Date(isoString), "MMM dd, HH:mm");
  } catch {
    return isoString;
  }
}

export function isLiveMode(mode: string | null): boolean {
  return mode === "LIVE";
}

export function isPaperMode(mode: string | null): boolean {
  return mode === "PAPER";
}
