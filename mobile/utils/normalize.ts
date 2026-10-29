// Defensive normalisers for server payloads.
// The paper-mode executor returns orders with `status` (not `state`) and without some
// optional fields; a missing string used to throw inside render (`undefined.toUpperCase()`)
// and blank the whole screen. Always go through these before storing / rendering.

import { Order } from "../types/api";

export function normalizeOrder(raw: any): Order {
  const r = raw ?? {};
  const state = typeof r.state === "string" && r.state ? r.state : typeof r.status === "string" && r.status ? r.status : "UNKNOWN";
  return {
    ...r,
    id: String(r.id ?? ""),
    pred_order_id: r.pred_order_id ?? null,
    proposal_id: r.proposal_id ?? null,
    state: String(state),
    side: typeof r.side === "string" ? r.side : "long",
    price_cents: String(r.price_cents ?? "0"),
    quantity: String(r.quantity ?? "0"),
    filled_qty: String(r.filled_qty ?? "0"),
    remaining_qty: r.remaining_qty ?? null,
    reduce_only: !!r.reduce_only,
    created_at: typeof r.created_at === "string" ? r.created_at : "",
  };
}

export function normalizeOrders(raw: unknown): Order[] {
  return Array.isArray(raw) ? raw.map(normalizeOrder) : [];
}
