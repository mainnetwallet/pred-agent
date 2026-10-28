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

// ── PRED account display (pure; unit-tested) ─────────────────────────────────

import type { AccountView } from "../types/api";

const ACCOUNT_HEADLINES: Record<string, string> = {
  loading: "Loading account…",
  not_configured: "Not configured",
  unauthenticated: "Login rejected",
  session_expired: "Session expired",
  error: "Balance unavailable",
};

export interface AccountSummary {
  cash: string; // "—" when unavailable; never "$0.00" for a failed request
  positionsCost: string | null;
  positionsPayout: string | null;
  headline: string | null; // null when everything is fine
  detail: string | null;
  walletAddress: string | null;
  safeAddress: string | null;
  ok: boolean;
}

export function summarizeAccount(account: AccountView | null, legacyBalance: string | null): AccountSummary {
  const cashRaw = account?.cash_available_usd ?? (account ? null : legacyBalance);
  const state = account?.state ?? (legacyBalance != null ? "ok" : "loading");
  const ok = state === "ok";
  const trouble = !ok || account?.trading_enabled === false;
  return {
    cash: cashRaw == null ? "—" : formatUsd(cashRaw),
    positionsCost: account?.positions_cost_usd != null ? formatUsd(account.positions_cost_usd) : null,
    positionsPayout: account?.positions_payout_usd != null ? formatUsd(account.positions_payout_usd) : null,
    headline: !ok ? ACCOUNT_HEADLINES[state] ?? "Balance unavailable" : account?.trading_enabled === false ? "Trading disabled" : null,
    detail: trouble ? account?.message ?? null : null,
    walletAddress: account?.wallet_address ?? null,
    safeAddress: account?.proxy_address ?? null,
    ok,
  };
}
