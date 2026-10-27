// API types — mirrors Control API (Phase 8: Chat + Rules + Fixtures with state)
// Money is always a decimal string; prices in cents as strings.

export type Mode = "PAPER" | "LIVE";

export interface ApiEnvelope<T = unknown> {
  ok: boolean;
  data?: T;
  error?: { code: string; message: string };
  audit_id?: string;
}

export interface StatusResponse {
  mode: Mode;
  auth_mode: string;
  feeds_ok: boolean;
  balance: string | null;
  exposure: string;
  daily_pnl: string;
  paused: boolean;
  pending_proposals: number;
  active_rules: number;
}

// ── Fixtures (Phase 8 — with state, kickoff_dhaka, markets) ──

export type FixtureState = "live" | "upcoming" | "other" | "all";

export interface FixtureMarket {
  market_id: string;
  status: string | null;
  best_bid_cents: number | null;
  best_ask_cents: number | null;
}

export interface FixtureSummary {
  id: string;
  parent_market_id: string;
  home_team: string | null;
  away_team: string | null;
  league: string | null;
  kickoff: string | null;           // UTC ISO string
  kickoff_dhaka: string | null;     // UTC+6 ISO string
  state: FixtureState;
  game_status: string | null;
  home_score: number | null;
  away_score: number | null;
  minute: number | null;
  markets: FixtureMarket[];
}

export interface MarketOutcome {
  market_id: string;
  name: string;
  team_id: string | null;
  status: string;
  tick_size: string | null;
  ltp: string | null;
  mid_price: string | null;
}

export interface FixtureDetail extends FixtureSummary {
  type_reference_id?: string | null;
  p_fair_home?: number | null;
  p_fair_draw?: number | null;
  p_fair_away?: number | null;
  research_summary?: string | null;
  research_sources?: string[];
  max_suggested_usd?: string;
}

export interface OrderBookLevel {
  price: string; // cents
  quantity: string; // shares
  order_count?: number;
  total_usd?: string;
}

export interface OrderBookResponse {
  market_id: string;
  bids: OrderBookLevel[];
  asks: OrderBookLevel[];
  ltp: string | null;
  metadata?: { spread: string; mid_price: string; last_calculated: string };
  last_updated_at: string | null;
  sequence_number: number | null;
  synthetic: boolean;
}

export interface Proposal {
  id: string;
  mode: Mode;
  advisory: boolean;
  fixture_id: string;
  market_id: string;
  side: "long" | "short";
  order_type: "limit" | "ioc";
  price_cents: string;
  quantity: string;
  amount_usd: string;
  est_fee_usd: string;
  edge_pp: string;
  p_fair: string;
  max_suggested_usd: string;
  binding_cap: string | null;
  reasons: string[];
  risks: string[];
  status: "PROPOSED" | "APPROVED" | "REJECTED" | "EXPIRED" | "ABORTED";
  created_at: string;
  expires_at: string | null;
}

export interface Order {
  id: string;
  pred_order_id: string | null;
  proposal_id: string | null;
  state: string;
  side: string;
  price_cents: string;
  quantity: string;
  filled_qty: string;
  remaining_qty: string | null;
  reduce_only: boolean;
  created_at: string;
}

export interface Position {
  market_id: string;
  parent_id: string;
  side: "long" | "short";
  quantity: string;
  avg_price_cents: string;
  cost_usd: string;
  status: string;
  opened_at: string;
}

export interface Limit {
  name: string;
  value: number;
  hard_max: number;
}

export interface Device {
  id: string;
  name: string;
  created_at: number;
  last_seen: number | null;
  revoked_at: number | null;
}

// ── Rules (Phase 8 — conditional exit rules) ──

export type RuleStatus =
  | "PENDING"
  | "ACTIVE"
  | "TRIGGERED"
  | "EXECUTING"
  | "DONE"
  | "CANCELLED"
  | "EXPIRED"
  | "BLOCKED"
  | "FAILED";

export type TriggerType = "profit_usd" | "loss_usd" | "price_cents" | "profit_pct";

export interface Rule {
  id: string;
  market_id: string;
  parent_market_id: string;
  position_ref: string | null;
  kind: "take_profit" | "stop_loss";
  trigger_type: TriggerType;
  trigger_value: string;           // Decimal string
  size: string;                    // "all" | "fixed_usd:X" | "fixed_shares:X"
  status: RuleStatus;
  created_via: "chat" | "app" | "telegram";
  mode: Mode;
  expires_at: string | null;
  confirmed_at: string | null;
  triggered_at: string | null;
  last_eval_at: string | null;
  last_eval_note: string | null;
  order_id: string | null;
}

// ── Chat (Phase 8) ──

export interface ChatCard {
  type: "fixture_list" | "fixture_detail" | "proposal" | "rule" | "rule_list" | "position_list" | "status";
  data: Record<string, unknown>;
}

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

export interface ChatTurn {
  session_id: string;
  reply: string;
  cards: ChatCard[];
}

export interface ChatHistory {
  session_id: string;
  messages: ChatMessage[];
}
