# PLAN.md — pred-agent build plan (from pred-agent.zip kit)

Generated from `AGENTS.md` step 3: short plan with the 8 phases, what each builds,
credentials per phase, and contradictions/gaps found in the kit. < 100 lines.

## Product (one line)
Human-in-the-loop trading agent for PRED (sports prediction market on Base) with
a Telegram bot, a private Control API, and an Expo mobile app. PAPER default;
real orders only after explicit approval and `LIVE_TRADING=true` set manually.

## Repository layout (to create under this project root)
backend/, mobile/, deploy/, docker-compose.yml, DOCS_FINDINGS.md, PLAN.md,
README.md, .env.example

## Phase plan (one at a time; stop and wait for "GO" after each)

| Phase | Builds | Credentials needed | Notes |
|---|---|---|---|
| 0 | `DOCS_FINDINGS.md` (docs recon vs `kit/02_PRED_API_SPEC.md`), repo skeleton proposal, dep list | none | **DONE.** All live docs fetched and compared; differences + new facts + resolved TODOs recorded in `DOCS_FINDINGS.md`. 5 open questions need PRED (draft message included). No code, no keys. |
| 1 | backend skeleton: pyproject/ruff/mypy/pytest, .env.example, config.yaml, config, util (Decimal), signer, auth (JWT+CLOB), transport (read-only), enablement CLI, redaction filter, scripts/whoami.py | PRIVATE_KEY (new wallet), optional PRED_API_KEY | **DONE.** 34/34 unit tests pass (fee vectors, amount, units, EIP-712 CreateProxy/ClobAuth/Order/SafeTx, low-s, redaction). Nothing places an order. Only account-setup/approval write calls when run manually via CLI. |
| 2 | store (SQLAlchemy+Alembic: fixtures, markets, kv, decisions, audit_log), scanner (discovery paging, moneyline football filter), realtime (Ably JWT / poll CLOB), unit normalization check, book analyzer, scripts/watch.py | none | **DONE.** 45/45 unit tests pass (REST cents vs WS dollars normalization, spread, slippage walk, depth≥size×3 veto, scanner eligibility filters). Alembic migration 0001_initial applied successfully. Still PRED read-only. Startup WS-vs-REST units check halts trading if units mismatch. |
| 3 | LLM client/interface, research, odds (optional), probability, strategy, risk, proposals, PaperExecutor, positions/pnl (paper), reports (daily + calibration Brier), event bus, Telegram bot + cards/commands/alerts | LLM_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_ID; optional ODDS_API_KEY, SPORTS_API_KEY | **DONE.** 61/61 unit tests pass (fee, amount, units, EIP-712, redaction, book/spread/slippage, scanner, risk vetoes/Kelly/caps, strategy edge, probability clamp/normalize, proposals approve/reject/expire, positions/PnL, paper executor, reports/calibration). Paper only — PaperExecutor uses in-memory state, no PRED write calls. Event bus, LLM client interface, DummyLLMClient when no key. |
| 4 | live Executor (constructed only if LIVE_TRADING=true): revalidate, sign EIP-712 order, place, confirm, reconcile, UNKNOWN handling, cancel/bulk/all, reduce-only exit proposals, startup reconcile | none new (uses Phase 1 keys) | **DONE (code only).** 71/71 tests pass (state machine, fill dedup, live executor gating, place success, error→UNKNOWN no-resend, cancel_noop). **NO LIVE_TRADING set, NO orders placed.** Code only executes when LIVE_TRADING=true and after explicit user approval. |
| 5 | Control API (FastAPI): device pairing (pair_device.py), bearer device tokens, X-Confirm biometric gate, all REST endpoints, WS /v1/ws, limits with hard bounds, OpenAPI snapshot test | none new | **DONE.** 11/11 unit tests pass (health/ready, pairing, device list/revoke, auth forbidden, secrets scan, WebSocket auth+ping). Private via Tailscale only. Can never enable LIVE_TRADING. |
| 6 | Expo mobile app: Pairing, Home, Fixtures, detail, Order book, Proposals, Orders, Positions, Limits/settings, Panic, Reports; generated TS client from OpenAPI | none new | **DONE.** 6/6 unit tests pass (money formatting, price cents, edge, shares, mode detection). App structure: App.tsx, screens/ (Pairing, Home, Proposals, Panic, LimitsSettings), components/ (StatusBanner, MoneyDisplay, OfflineBadge, ProbabilityGauge), services/ (api.ts, websocket.ts), store/ (Zustand with SecureStore), types/api.ts, utils/format.ts. DESIGN.md written with distinctive visual direction (not templated). Acceptance: pair a real phone over Tailscale and approve a paper proposal from the phone (manual step). |
| 7 | Docker (slim, non-root), compose with DB volume, Tailsdale guide, server hardening, nightly encrypted backups + restore test, JSON rotated logs, health/ready, heartbeat, README runbook, pip-audit | none new | **DONE.** Dockerfile + compose + Tailscale guide + hardening checklist + backup/restore scripts + CI workflow (ruff, mypy, pytest, pip-audit) + README runbook + DEPLOY_REPORT.md. All 82 tests pass. Project code complete. Final deployment and LIVE_TRADING switch are your manual decision. |

## Credentials summary (all go only into `.env`, never chat/GitHub/screenshots)
1. `PRIVATE_KEY` — new dedicated wallet, small funds (Phase 1)
2. `PRED_API_KEY` — X-API-Key from PRED for JWT mode (optional; Phase 1). Without it: CLOB mode if an eligible account exists, else read-only + paper.
3. `LLM_API_KEY` — provider with web-search tools (Phase 3)
4. `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_USER_ID` (Phase 3)
5. Optional: `ODDS_API_KEY`, `SPORTS_API_KEY`, `BASE_RPC_URL`

## Contradictions / gaps found in the kit (to track in DOCS_FINDINGS.md / open questions)
- `game_status` values undocumented; derive "live" from observed values + kickoff.
- Login `message` content rules undocumented; make `AUTH_LOGIN_MESSAGE` configurable.
- Order body `user_id` presence unknown (`TODO(verify)`); L2 identifies user in CLOB mode.
- Orderbook REST auth unclear (Builder says public; Orders spec lists Bearer headers); try without auth first.
- WS book prices appear in dollars while REST uses cents — normalize in one function, startup check required.
- No testnet/sandbox documented; no idempotency key confirmed; never retry placement blindly.
- Cashflow, Cashout, How Are Markets Resolve, Withdraw pages not yet read (Phase 0).
- Docs last updated ~19 days before kit (Oct 2026); re-check for newer changes; docs win over kit on disagreement.

## Hard rules (from kit, apply to every phase)
PAPER default; LLM never trades/sees secrets/changes limits; every real order needs explicit approval; fail closed; secrets never logged/transmitted; Decimal math only; never blindly retry a placement; one phase at a time; `kit/` is read-only, code under backend/mobile/deploy.
