# DOCS_FINDINGS.md — PRED docs recon (Phase 0)

Source: https://pred-1.gitbook.io/pred-docs (llms.txt + each API Reference page as `.md`).
Fetched pages: Overview, Market Discovery, Authentication, Builder and Aggregator Onboarding,
Trading Enablement, Orders, Portfolio, Cashflow, WebSocket (Ably), Models, Cashout,
How Are Markets Resolve, Withdraw.
Kit reference: `kit/02_PRED_API_SPEC.md` (built ~Oct 2026, docs last updated ~19 days before).

**Rule applied:** `kit/` is read-only. Where live docs disagree with the kit, the docs win and the
difference is recorded here. Kit files are NOT edited.

---

## 1. Confirmed (kit matches live docs)

| Area | Kit statement | Docs confirmation |
|---|---|---|
| Environment | Host `https://www.pred.app`, Base chainId 8453 | Confirmed (Overview + OpenAPI server URL) |
| USDC | Native USDC on Base, `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913`, 6 decimals | Confirmed |
| CreateProxy factory | `0xA077e7aaBF2e3c0155F83789e95f46602D03F7f0` | Confirmed |
| Order contract | Per-parent `parent_market_data.contract_address`, never hardcode | Confirmed |
| Auth modes | JWT (`X-API-Key`, Ably) vs CLOB (L1→L2 HMAC, polling); three distinct non-interchangeable keys | Confirmed |
| JWT login | `POST /api/v1/auth/login-with-signature` + `X-API-Key`; body fields; `access_token`/`refresh_token`/`proxy_wallet_addr`/`wallet_address`/`data.{...}`; `X-Access-Token` header; refresh in `Set-Cookie` | Confirmed |
| CreateProxy signature | Domain `{name:"Pred Contract Proxy Factory", chainId, verifyingContract}` (NO version); type `CreateProxy(address,uint256,address)`; message all zero; `0x<r><v>` 132 hex; low-s `s <= curveOrderHalf`; constant per EOA = treat as credential | Confirmed |
| Refresh | Tokens rotate; new refresh only in `Set-Cookie`; old dies `401 REFRESH_TOKEN_ALREADY_USED`; single-flight; refresh keys differ (`proxy_wallet_address`); 5-minute default threshold | Confirmed |
| CLOB L1 | `GET /clob/time`; ClobAuth domain `{name:"ClobAuthDomain", version:"1", chainId:8453}` (NO verifyingContract); type `ClobAuth(address,string,uint256,string)`; message fixed; nonce 0 first; `POST /clob/auth/api-key` → `{apiKey,secret,passphrase}`; new EOA needs `X-Aggregator-API-Key`; `derive-api-key`; `api-keys` list; `DELETE /clob/auth/api-key` revoke (≤30s, no order cancel, revoked nonce can't reuse) | Confirmed |
| CLOB L2 | Headers `PRED_ADDRESS/API_KEY/PASSPHRASE/TIMESTAMP/SIGNATURE`; `PRED_SIGNATURE = base64url(HMAC_SHA256(base64url_decode(secret), timestamp + METHOD + canonicalPath + rawBody))`; canonicalPath excludes `/clob` + query; `rawBody` exact (`""` vs `"{}"`); base64url secret with padding; fresh timestamp per request | Confirmed |
| CLOB endpoints | account-status, activate-user, create-proxy-wallet, safe-approval prepare/execute, portfolio balance/positions/open-orders/trade-history, order place/cancel/all/bulk | Confirmed |
| CLOB not available | Ably token, real-time per-parent open orders, earnings, grouped trade history, withdrawals | Confirmed |
| 429 | Honour `Retry-After`, else exponential backoff + jitter; limits otherwise undocumented | Confirmed |
| Account changes | Up to 30s to take effect | Confirmed |
| Safe approval | `prepare` with `{}`; `data.success` true; object at `data.data` (no top-level success); field list; `chainId==8453` + safeAddress checks; SafeTx domain `EIP712Domain(uint256 chainId,address verifyingContract=safeAddress)` (no name/version); type `SafeTx(...)`; MultiSend decode (ERC20 `approve`, ERC1155 `setApprovalForAll`); spenderAddress may be MultiSend; explicit terminal confirmation; raw signing no EIP-191, v in {27,28}, low-s; `execute` with entire `data.data` unmodified; response `data.data` may be null, no reliable tx hash, has `is_enabled_trading`; verify on-chain `allowance` + `isApprovedForAll`; up to 30s; no Safe: JWT `prepare` optional `proxy_creation_signature`, CLOB `create-proxy-wallet` (prefer separate Safe then `{}`); never repeat execute blindly | Confirmed |
| Discovery | `GET /api/v1/market-discovery/discover?verbose=true&limit=50&offset=0`; page `offset += limit` until `data.total`; verbose mandatory; ~10 req/s; shape `data.parent_markets_list[]`; no active-only filter; eligible = parent active AND child active; keep paging on empty pages; filters `parent_market_type`,`family`,`league_id`(with type),`start_time`/`end_time`,`order`,`omit_inactive_with_redeemed_on_before` fixed cutoff; don't use `start_time=now`/`default=true` as active substitute; `parents/{id}` and `parents?market_id=<child>`; field mapping (type_reference_id = Ably key, contract_address = verifyingContract); game_status undocumented → log values | Confirmed |
| Orderbook | `GET /api/v1/order/{parent}/orderbook/{marketID}?depth=1..100&include_metadata=true`; prices cents, sizes shares; fields `bids/asks {price,quantity,order_count,total_usd}`, `ltp`, `metadata {spread,mid_price,last_calculated}`; synthetic levels from sibling cross-matching; self-match across sibling markets allowed | Confirmed |
| Orderbook auth | Kit said "try without auth, fall back". Docs: Builder page — read **without authentication** (public). | Confirmed (no-auth works) |
| Ably | `POST /api/v1/auth/ably` raw TokenDetails; capabilities `private:user:<id>` (presence+subscribe), `market:*` (subscribe); channel `market:{type_reference_id}`, event `orderbook` full snapshots ~100ms debounce; route by `market_id`; drop without it; drop `sequence_number <= last applied`; legacy `market:orderbook:*` / `market:ltp:*` discontinued 20 July 2026; unit warning WS dollar (`"0.62"`) vs REST cents; private channel `private:user:{user_id}` requires presence first; events `order-created/cancelled/updated/adjusted/failed`; `order_updated` early `matched_on_chain=false`, later `order_matched_on_chain` same match — key on `match_id`/`fill_id`, never count both; alternate keys `order_side/order_price/order_quantity` / nested `order`; reconnect backoff 1s–30s then REST re-snapshot; CLOB polls REST books | Confirmed |
| Orders place | Body: `salt`, `market_id`, `side`, `price` (cents 0–100, tick multiple), `quantity` (shares, max 3 decimals), `amount` (USD, required exact for non-reduce), `signature`, `type` (limit|market|ioc explicit), `timestamp` (unix s, >30s old rejected), `expiration` (0=GTC), `fee_rate_bps` (signed), `reduce_only`, `expire_on_match_start` (needs expiration 0), `max_match_amount` (market/ioc), `is_low_priority` (false). `user_id` = optional legacy, does NOT select user (auth determines user). | Confirmed |
| amount formula | long `price*qty/100`; short `(100-price)*qty/100`; reduce_only from reduction | Confirmed |
| Minimums | Opening notional ≥ $0.90; reduce-only qty ≥ 0.001; qty max 3 decimals | Confirmed |
| EIP-712 Order | Domain `{name:"Pred CTF Exchange", version:"1", chainId:8453, verifyingContract:<parent contract>}`; type field order exact; maker=Safe, signer=EOA, taker=0x0, price=cents*10_000 (55c→550000, NOT 55*1e6), quantity=shares*1e6, expiration as sent, nonce=0, questionId=child market_id full 32 bytes, feeRateBps=signed, intent long 0/short 1, signatureType=2, canonical low-s | Confirmed |
| Fees | `signedBps = floor(targetBps * max(s, 1_000_000 - s) / 1_000_000)`, `s = round(priceCents * 1_000_000 / 100)`, integer math only; `targetBps = fee_rate_bps`; missing → stop and ask (don't assume 0); explicit 0/negative fee-free; test vectors target 250 → 68c=170, 17c=207, 50c=125 | Confirmed |
| Place response | 202 `{status, order_id, message, filled_quantity, is_delayed, delay_ms_effective, delay_until}`; 422 insufficient liquidity / self-match-only; acceptance is not a fill | Confirmed |
| Delay queue | `is_delayed` orders held; cancel returns `400 cancel_noop` and does NOT cancel | Confirmed |
| Retries | No confirmed idempotency key; timeout/5xx/network → mark UNKNOWN, reconcile, never new salt; `409 idempotency_conflict|placement_in_progress`, `503 idempotency_unavailable|placement_outcome_unknown` may appear but don't guarantee safe automated retry — confirm with PRED | Confirmed |
| Cancel | `DELETE .../cancel` body `{order_id, market_id}`; `cancel/all` no body; `cancel/bulk` `{orders:[1..50]}` dedupes by order_id; HTTP 200 may contain failures → inspect every result + `total_failed`; filled qty can't cancel; no new wallet signature | Confirmed |
| Close position | `reduce_only` = only reduce opposite; closing LONG = SHORT reduce_only; no native TP/SL; Cashout not a separate API (see New facts) | Confirmed |
| Cross-matching | Long orders summing to 100c across children of cross-match-enabled parent = valid match; synthetic levels in books; 422 can mean self-match-only liquidity | Confirmed |
| Portfolio | `usdc_balance` (USDC decimal, net of reservations, NOT base units), `position_balance` (signed shares); positions fields `quantity/average_price/amount/status`, `total_invested`, `total_to_win` (gross payout); Earnings JWT only `{realized,unrealized,total}_pnl`; trade history fields + pagination `{total,limit,offset,has_next}`; open orders real-time per parent (JWT only) vs portfolio open-orders lag; portfolio data lags | Confirmed |
| Funding | Manual; native USDC on Base to Safe (never EOA); ERC20 `transfer(safe, amount*1_000_000)`; needs ETH for gas; no deposit API; don't send second transfer if not visible | Confirmed |
| Errors | 401 `invalid_l1|invalid_l2|invalid_auth|aggregator_key_required`; 403 `aggregator_account_limit_reached`; 400 `nonce_already_used|unsupported_content_encoding`; 413 `body_too_large`; 429 `Retry-After`; 503 `placement_disabled|auth_unavailable`; 502 `upstream_uncertain`; 422 failed-order body; 400 `cancel_noop`; preserve status + raw body | Confirmed |

---

## 2. Differences (kit says X, live docs say Y)

| # | Kit says | Live docs say | Page |
|---|---|---|---|
| 1 | Refresh token comes **only** in `Set-Cookie`, not the body. | Login response body **includes** `refresh_token` field **and** sets `Set-Cookie` cookie; body value can be sent as `X-Refresh-Token` header. | Authentication |
| 2 | Orderbook: try without auth first, fall back to auth, record which works. | Docs explicitly state orderbook is readable **without authentication** (public). Kit's fallback approach is safe but docs confirm no-auth works. | Overview / Orders |
| 3 | Fee `test vectors`: target 250 → 68c=170, 17c=207, 50c=125. | Same vectors confirmed; docs add clarification that EIP-712 price is `(priceCents/100)*1e6` (55c → 550000), **not** `55*1e6` — same numeric result as kit, clearer wording. | Orders → Fees / Units |
| 4 | `user_id` in place body marked `TODO(verify)`. | Resolved: `user_id` is an optional legacy field; it does **not** select the acting user — authentication determines the user. Not needed. | Orders → PlaceOrderRequest |
| 5 | Expiration / salt details absent. | `expiration` maximum `4102444800`; salt is full uint256 range `0 … 2^256-1` as decimal string. | Orders → PlaceOrderRequest |
| 6 | `max_match_amount` semantics sparse. | Nonzero `max_match_amount` is **rejected for limit orders** (allowed for market/ioc only). | Orders → PlaceOrderRequest |
| 7 | Discovery filters list incomplete. | Docs add `default=true` (implies verbose), `scope=leagues` (returns `leagues_list`), `cname`, `top` (alias for limit), `limt` (deprecated typo alias), `omitempty` (scope=leagues only), and `redeemed_on` concept. | Market Discovery |
| 8 | Missing `fee_rate_bps`: "stop and ask (do not assume 0)". | Docs agree but add: legacy SDK defaults missing target to `0`; **new integrations should confirm missing configuration with PRED** rather than assume fee-free. | Market Discovery → Fees |

None of these require code changes in Phase 0; they are recorded here and Phase 1/2 will implement per the docs.

---

## 3. New facts (not in the kit)

| Area | New fact |
|---|---|
| Cashout | **No separate Cashout API exists.** Cashout = selling your shares early at the current market bid (set by other traders, minus a small fee). On thin markets the bid can be below the screen price. Implication: close positions via `reduce_only` opposite-side orders (already the plan). | `trading/order-types/cashout` |
| Cashflow (withdraw) | `POST /api/v1/cashflow/withdraw/relay` (JWT only, **not** CLOB). User signs a Safe USDC withdrawal; backend relays on-chain and pays gas. `prepared_data` includes `safeAddress/tokenAddress/recipientAddress/transferAmount/transactionHash/chainId/nonce/to/value/data/operation/safeTxGas/baseGas/gasPrice/gasToken/refundReceiver`. Response: `transaction_hash` + `transaction_status` (`confirmed`/`pending`). HTTP 409 = Safe nonce changed → update nonce + new signature. | `documentation/api-reference/cashflow` |
| Withdraw | Page says "simple, instant, and free" (no separate API detail beyond Cashflow). | `getting-started/deposit-and-withdraw/withdraw` |
| How markets resolve | PRED oracles fetch verified results; winning outcome pays **1.00 USDC/share**, losing settles to **0.00**; resolves in **minutes not days**; postponed → market stays open until completed; canceled with no makeup → resolves "No"; refers to first 90 min + stoppage time; score revisions after resolution don't count. | `markets/how-are-markets-resolve` |
| Discovery | `redeemed_on` field exists on parents; `status == "redeemed"` is terminal at parent level. | Market Discovery |
| WS | Price example `"0.62"` is dollar scale (62¢); `metadata {spread, mid_price, last_calculated}`; `ltp` omitted when no recent trade in window; `size` is alias for `quantity`. | WebSocket (Ably) |
| CLOB | `next_step` enum: `activate_user | create_proxy_wallet | safe_approval | ready`; `proxy_wallet_address` at top level (no `data` wrapper) in account-status. | Builder / Aggregator Onboarding |

---

## 4. Resolved `TODO(verify)` items from kit

| Kit TODO | Resolution |
|---|---|
| `user_id` needed in place body (JWT + CLOB)? | **No.** Optional legacy field; auth determines user. |
| Cashout — different close path than `reduce_only`? | **No separate API.** Cashout is market-based sell; use `reduce_only` opposite-side orders. |
| Cashflow / How Are Markets Resolve / Withdraw pages | **Read.** Cashflow = withdraw relay (JWT only); do not implement (kit non-goal: no withdrawals from bot). Resolution = oracles, 1.00/0.00 payout, minutes. Withdraw = simple/instant/free, no extra API. |
| Orderbook REST needs auth? | **No.** Public, no authentication. |

---

## 5. Still open — need an answer from PRED

| # | Question | Why it matters |
|---|---|---|
| 1 | `game_status` values: which values mean live / halftime / finished / postponed? | Scanner must derive "live" correctly; kit says never hardcode a guess. |
| 2 | Rate limits per route / per key (only `429` + `Retry-After` documented)? | Scanner/realtime must respect limits to avoid 429s. |
| 3 | Is placement idempotency actually supported? Are automated retries of `409`/`503` safe? | Docs say confirm with PRED before any automated placement retry. |
| 4 | How to obtain an `X-API-Key`? | Needed for JWT mode (Ably realtime, per-parent open orders, earnings). |
| 5 | Actual JWT access-token lifetime (docs give 5-min refresh threshold, not lifetime)? | Token-refresh scheduler. |

### Draft message to send to PRED

> Hi PRED team,
>
> We are building a non-custodial trading agent on top of the PRED API (Base chain) and would like to confirm a few items before going live:
>
> 1. How do we request an `X-API-Key` for JWT authentication?
> 2. Are placement retries idempotent? Is it safe to automatically retry on `409 idempotency_conflict / placement_in_progress` and `503 idempotency_unavailable / placement_outcome_unknown`, or must every uncertain placement be reconciled manually?
> 3. What are the rate limits per route and per API key (only `429` + `Retry-After` is documented)?
> 4. Can you document the `game_status` values (which mean live, halftime, finished, postponed)?
>
> Thanks.

---

## 6. Repo skeleton & dependency proposal (Phase 0 deliverable)

```
pred-agent/
  kit/                 (read-only reference — do not edit)
  prompts/             (read-only reference — do not edit)
  backend/
    pyproject.toml
    alembic.ini
    Dockerfile
    .env.example
    .gitignore
    src/pred_agent/
      main.py
      config.py
      events.py
      transport/        (PredTransport protocol, JwtTransport, ClobTransport)
      auth/             (JWT session, CLOB L1/L2 credentials)
      signer/           (EIP-712: CreateProxy, ClobAuth, Order, SafeTx; low-s)
      enablement/       (Safe approval CLI: prepare, verify, sign, execute, on-chain check)
      scanner/          (discovery paging, eligibility filter)
      realtime/         (Ably feed JWT / poller CLOB, book cache, sequence guard, unit normalization)
      book/             (spread, depth, slippage walk, synthetic flag)
      research/         (LLM web-search + optional sports API)
      odds/             (optional vig removal)
      probability/      (p_fair, capped LLM adjust, range)
      strategy/         (edge after fee/slippage, side choice)
      risk/             (vetoes, Kelly sizing, caps)
      proposals/        (create, TTL, approve, reject, change amount)
      execution/        (PaperExecutor now; live Executor in Phase 4)
      positions/        (paper positions, PnL ledger)
      reports/          (daily summary, calibration)
      telegram/         (bot cards, commands)
      control_api/      (FastAPI app, pairing, WS)
      store/            (SQLAlchemy models + Alembic)
      llm/              (LLMClient interface)
      util/             (Decimal, units, time, redaction)
      tests/
    scripts/
      whoami.py
      pair_device.py
      watch.py
      enablement CLI
  mobile/              (Expo app, TypeScript)
  deploy/              (tailscale.md)
  docker-compose.yml
  PLAN.md
  DOCS_FINDINGS.md
  README.md
```

**Pinned dependencies (backend, Python 3.11+):**

- `fastapi==0.115.x`, `uvicorn[standard]==0.34.x`
- `httpx==0.27.x`
- `pydantic==2.10.x`, `pydantic-settings==2.6.x`
- `SQLAlchemy==2.0.x`, `alembic==1.13.x`
- `eth-account==0.13.x`, `web3==7.10.x`
- `ably-python==1.2.x` (or latest 1.x)
- `python-telegram-bot==21.x`
- `structlog==24.x`
- `pytest==8.3.x`, `pytest-asyncio==0.26.x`
- `ruff` (lint/format), `mypy` (strict where practical)
- `pip-audit` (CI)

Mobile: Expo SDK 52 (managed), React Native, TypeScript strict, Expo Router, TanStack Query, Zustand, expo-secure-store, expo-local-authentication, expo-notifications, react-native-svg.

---

## 7. Open questions carried into `kit/11_OPEN_QUESTIONS.md`

Answered now (can be removed/marked): `user_id` in place body; Cashout vs `reduce_only`; Cashflow/Withdraw/Resolution page facts; orderbook auth. Remaining: the 5 PRED questions above plus product decisions (leagues, bankroll, proposal TTL, odds provider, LLM provider, auto stop-loss, pre-match vs in-play).

---

**Phase 0 status:** docs recon complete; `DOCS_FINDINGS.md` written; no application code written; no keys requested. Stopping here — waiting for `GO` before Phase 1.

---

## 4. Code-vs-docs audit (Oct 28, 2026) — bugs found and fixed

Checked every API call site against the official docs (`pred-docs-text.zip`). Endpoint paths, `PRED_*` L1/L2
headers, HMAC canonical path, place/cancel bodies and response handling all match. Differences fixed:

| # | Code did | Docs / standard require | Fix |
|---|---|---|---|
| 1 | EIP-712 `address` fields encoded as 20 bytes | Static types are 32-byte words; addresses left-padded | `signer/eip712.py::_encode_value` pads addresses, right-pads `bytesN` |
| 2 | `_eip712_digest_with_prefix` hashed `0x1901 ‖ keccak(domain‖struct)` | `keccak(0x1901 ‖ domainSeparator ‖ structHash)` | Digest rebuilt per EIP-712 (ClobAuth, Order, CreateProxy, SafeTx) |
| 3 | CreateProxy / SafeTx signed a digest **without** `0x1901` | "No EIP-191 prefix" = no `personal_sign` wrapper; SafeTx digest equals PRED's `transactionHash` | Both use the standard EIP-712 digest |
| 4 | `reconcile_startup` read only the first open-orders page (default 50) | Paginate with `limit`≤100/`offset` until `pagination.has_more` is false | `_fetch_all_open_orders` |
| 5 | Empty open-orders reply (`orders` omitted) raised `ReconcileError` | Empty `orders` are omitted from the portfolio envelope | `success=true` + no `orders` ⇒ `[]`; `success=false` fails closed |
| 6 | `sync_fills` treated every trade-history row as a fill | Rows may be `Redeemed` / `Lost` settlements | Those rows are skipped |
| 7 | `get_book` chat tool cache-only; no spread/mid; fetch errors silent | Orderbook is a public REST endpoint | REST fallback, analyzer, logging |
| 8 | JWT refresh "single-flight" event was set at creation, so waiters never waited | Refresh tokens rotate; old one dies after use | Lock + re-check before rotating |

Tests compare all four signatures to `eth_account`'s independent EIP-712 implementation and recover the signer.
Not covered: `enablement/cli.py` Safe-approval steps are still TODO placeholders (verify/decode/sign/execute).
