# Architecture — pred-agent

Human-in-the-loop trading agent for PRED (sports prediction market on Base chain),
with a Telegram bot, a private Control API, and an Expo mobile app.

---

## 1. Big picture

```
 +-------------------+            +------------------------+
 |  Phone (Expo app) |<--HTTPS/WS-|  Control API (FastAPI) |  private, via Tailscale
 +-------------------+            +-----------+------------+
 +-------------------+                         |
 |  Telegram         |<--bot API-------------+ |
 +-------------------+                       | |
                                              v v
                      +-----------------------------------------------+
                      |                 AGENT CORE (one process)       |
                      |  Orchestrator + event bus + state machines     |
                      +----+------------+-------------+-------------+--+
                           |            |             |             |
                    +------v-----+ +----v------+ +----v-------+ +---v--------+
                    | PRED       | | Research  | | Odds       | | Store      |
                    | Transport  | | providers | | provider   | | SQLite ->  |
                    | JWT | CLOB | | (LLM web, | | (optional) | | Postgres   |
                    | + Ably     | | sports API)| |            | |            |
                    +------+-----+ +-----------+ +------------+ +------------+
                           |
                    +------v-------+       +-------------------+
                    | Signer       |<------| PRIVATE_KEY (env) |
                    | (isolated)   |       +-------------------+
                    +--------------+
```

- The phone and Telegram **never** talk to PRED. They only talk to the agent.
- The phone never holds the wallet key.
- `PRIVATE_KEY` lives only in `.env` on the server; it is read **only** by the `signer` module.

---

## 2. Auth mode selection

```
 PRED_API_KEY present? --yes--> JWT mode  (Ably realtime, per-parent open orders, earnings)
         | no
 existing eligible account and primary wallet == bot EOA? --yes--> CLOB mode (L1 -> L2, polling)
         | no
 READ-ONLY + PAPER only (public discovery and books)
```

`PredTransport` is a Protocol with two implementations (`JwtTransport`, `ClobTransport`) so the rest of the code never branches on auth mode, except for capability flags (`has_realtime`, `has_parent_open_orders`, `has_earnings`).

---

## 3. Decision pipeline (one trade)

```
scan -> map fixture -> research -> book analysis -> p_fair -> edge after fees
  -> risk veto + sizing -> PROPOSAL (Telegram + app, TTL)
  -> [I approve] -> re-validate (status, book, odds, edge, slippage)
  -> build order -> sign -> place -> confirm (events or polling) -> report -> monitor
```

---

## 4. Order state machine

```
PROPOSED --> EXPIRED
          --> REJECTED
          --> APPROVED --> REVALIDATED --> ABORTED
                                   --> SUBMITTED --> DELAYED? --> OPEN --> PARTIAL --> FILLED
FILLED --> CLOSING --> CLOSED --> SETTLED
any uncertain outcome --> UNKNOWN  (reconcile only; never resend)
any rejection         --> FAILED
```

Rules:
- Transitions are persisted before side effects.
- `UNKNOWN` blocks new orders on that market until resolved.
- `order_updated` and `order_matched_on_chain` update the same fill row (key `match_id` / `fill_id`).
- Never resend with a new salt after a timeout or 5xx.

---

## 5. Modules

| Module | Responsibility |
|---|---|
| `config` | Load `.env` + `config.yaml`, validate, freeze (`Settings`) |
| `transport` | PRED REST access, both auth modes, rate limit, 429, raw audit log |
| `auth` | JWT session (login, rotating refresh, relogin) and CLOB credentials (L1 create or derive, L2 HMAC) |
| `signer` | **Only** module that reads `PRIVATE_KEY`. Signs login, ClobAuth, Order, SafeTx digest |
| `enablement` | One-time Safe approval flow with digest check, MultiSend decode, on-chain verify |
| `scanner` | Discovery paging, eligibility filter, fixture state, `game_status` logger |
| `realtime` | Ably client (JWT) or poller (CLOB). Local order book cache with sequence guard |
| `research` | Providers that return a structured `ResearchReport` with sources and timestamps |
| `odds` | Optional bookmaker odds, vig removal |
| `probability` | Baseline `p_fair`, capped LLM adjustment, uncertainty range |
| `book` | Spread, depth, slippage walk, synthetic flag |
| `strategy` | Edge after fee and slippage, Long or Short choice |
| `risk` | Hard vetoes, Kelly sizing, caps, daily loss |
| `proposals` | Create, expire, approve, reject, change amount |
| `execution` | `PaperExecutor` (paper) / `Executor` (live, only if `LIVE_TRADING=true`) |
| `positions` | Fills, positions, PnL, exit proposals, resolution tracking |
| `reports` | Daily summary, calibration |
| `telegram` | Bot, cards, callbacks, commands |
| `control_api` | FastAPI app per `07_CONTROL_API.md`: pairing, REST, WebSocket events |
| `store` | SQLAlchemy models and Alembic migrations |
| `events` | In-process `asyncio.Queue` event bus |

---

## 6. Concurrency model

- One asyncio process. Tasks: scanner loop, book feed, research workers (bounded concurrency), proposal expiry sweeper, position monitor, Telegram polling, Control API server, daily reports.
- In-process **event bus** (`asyncio.Queue` per subscriber). The Control API WebSocket and Telegram subscribe to it.
- Persist first, then publish events. A crash must never lose an order state transition.
- Per-market asyncio lock around: propose, execute, cancel, close. At most one live action per child market at a time.

---

## 7. Data model (tables)

- `fixtures` (pred fixture id, parent id, type_reference_id, league, home, away, kickoff, status, score, minute, last_seen)
- `markets` (market_id, parent_id, outcome name, tick_size, status, family, contract_address, fee_target_bps)
- `research_snapshots` (fixture_id, provider, json, sources, fetched_at)
- `odds_snapshots` (fixture_id, provider, json, fetched_at)
- `probabilities` (fixture_id, market_id, p_baseline, p_adjust, p_fair, low, high, confidence, model_inputs_json, created_at)
- `proposals` (id, mode, fixture_id, market_id, side, type, price_cents, quantity, amount_usd, est_fee_usd, edge_pct, p_fair, max_suggested_usd, binding_cap, reasons_json, risks_json, status, created_at, expires_at, decided_at, decided_via)
- `orders` (id, pred_order_id, proposal_id, state, side, price_cents, quantity, filled_qty, remaining_qty, salt, signed_fee_bps, is_delayed, delay_until, reduce_only, raw_request_redacted, raw_response, created_at, updated_at)
- `fills` (id, order_id, match_id, fill_id, price_cents, quantity, matched_on_chain, fee_usd, ts)
- `positions` (market_id, parent_id, side, quantity, avg_price_cents, cost_usd, status, opened_at, closed_at)
- `pnl_ledger` (ts, kind: realized|unrealized|fee, amount_usd, ref)
- `decisions` (ts, fixture_id, market_id, outcome: proposed|skipped|vetoed, reason_codes_json, summary)
- `audit_log` (ts, actor: agent|telegram|app:<device>, action, payload_redacted)
- `devices` (id, name, public_info, created_at, last_seen, revoked_at, secret_hash)
- `kv` (settings, last cursors, session tokens encrypted)

---

## 8. Tech stack

Backend: Python 3.11+, FastAPI + uvicorn, httpx, pydantic v2, SQLAlchemy 2 + Alembic, `eth-account`, `web3`, `ably`, `python-telegram-bot` v21+, structlog, pytest + pytest-asyncio, ruff, mypy.

LLM access through an `LLMClient` interface (provider and model come from config; no hardcoded model names).

Mobile: Expo (React Native) + TypeScript. Details in `mobile/DESIGN.md`.

---

## 9. Repository layout

```
pred-agent/
  kit/                      (read-only reference — do not edit)
  prompts/                  (read-only reference — do not edit)
  backend/
    pyproject.toml  alembic.ini  Dockerfile
    src/pred_agent/
      main.py  config.py  events.py
      transport/ auth/ signer/ enablement/ scanner/ realtime/ research/ odds/
      probability/ book/ strategy/ risk/ proposals/ execution/ positions/
      telegram/ control_api/ store/ reports/ llm/ util/
      tests/  scripts/
    alembic/  alembic.ini
  mobile/                   (Expo app: Pairing, Home, Fixtures, detail, Proposals, Orders, Positions, Panic, Limits/Settings)
  deploy/  docker-compose.yml  tailscale.md  backup.sh  restore.sh
  DOCS_FINDINGS.md  PLAN.md  README.md  DEPLOY_REPORT.md  .env.example
```

`kit/` is read-only. Put code under `backend/`, `mobile/`, `deploy/`.

---

## 10. Observability

- Structured JSON logs with central redaction filter (secrets never logged).
- `/health` (process alive) and `/ready` (feeds ok, DB ok, auth ok).
- Heartbeat message to Telegram every 6 hours.
- Every exception that affects trading raises an alert and pauses new proposals until acknowledged.
- 6-hourly Telegram heartbeat; alert on feed down, auth failure, `UNKNOWN` order, daily loss cap.

---

## 11. Security rules

1. `signer` is the only module reading `PRIVATE_KEY`; it exposes `sign_*` methods that take typed payloads.
2. Run the agent as a non-root user; `.env` mode 600; disk encryption on the VPS where possible.
3. Dedicated trading wallet with limited funds; the main wallet never touches this system.
4. The LLM receives only structured research data, never keys, headers, or raw API responses containing auth.
5. Central log redaction (keys, JWT, refresh token, L2 secret and passphrase, signatures, `Authorization`, `PRED_*` headers, Telegram token).
6. The login signature (CreateProxy) is constant per EOA: treat it as a credential; store only encrypted or recompute.
7. Persisted session tokens and L2 credentials are encrypted at rest with a key derived from a server secret.
8. Safe approval signing is a manual, interactive CLI step with decoded calls printed and typed confirmation.
9. `LIVE_TRADING` cannot be changed from Telegram or the app.
10. Dependencies pinned; `pip-audit` in CI.
11. Control API only reachable via Tailscale; firewall denies everything else except SSH (key only, or Tailscale SSH).
12. Backups: nightly encrypted DB dump, off-box; restore tested.
13. If compromise is suspected: `/panic`, revoke L2 credential, move funds manually, rotate everything, new wallet.

---

## 12. Go-live switch

1. Complete the checklist in `kit/04_BUILD_PLAN.md` (live checklist).
2. Edit `.env` on the server: `LIVE_TRADING=true`, restart.
3. Startup banner (Telegram + app) confirms LIVE and current limits.
4. First order: $1 to $2, approved by you, watched end to end.

Do not enable `LIVE_TRADING` for anyone else. You do that manually after reading the final report.
