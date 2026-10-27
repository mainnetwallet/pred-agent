# PRED Agent — Runbook

Grounded in kit/09_CONFIG_SECURITY_DEPLOY.md (Runbook section)
and kit/04_BUILD_PLAN.md (Live checklist).

This document covers: start, stop, update, rollback, restore, rotate secrets,
handle UNKNOWN orders, PAPER → LIVE switch, and compromise response.

---

## 1. Start the agent

```bash
cd /path/to/pred-agent

# 1. Create .env from template (only you know the secrets)
cp backend/.env.example backend/.env
chmod 600 backend/.env
# Edit backend/.env with your PRIVATE_KEY, PRED_API_KEY (optional),
# LLM_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USER_ID, etc.
# NEVER set LIVE_TRADING=true unless you have completed the live checklist
# in kit/04_BUILD_PLAN.md and you accept real-money risk.

# 2. Start services
docker compose up -d

# 3. Verify
curl http://<tailscale-ip>:8000/health   # from phone on same tailnet
# Should return: {"ok":true,"status":"healthy"}
docker compose logs -f --tail=50 pred-agent
```

## 2. Stop the agent

```bash
docker compose stop pred-agent
# or
docker compose down
```

## 3. Update the agent

```bash
cd /path/to/pred-agent
git pull  # or fetch new release

# Rebuild image
docker compose build --no-cache pred-agent

# Restart
docker compose up -d pred-agent

# Run migrations if any
docker compose exec pred-agent alembic upgrade head
```

## 4. Rollback

```bash
# Roll back to previous image tag
docker compose up -d pred-agent   # with previous image tag
# Or restore from backup (see section 6).
```

## 5. Restore from backup

```bash
# List available backups
ls deploy/backups/

# Restore
./deploy/restore.sh deploy/backups/pred-agent-db-<timestamp>.sql.gz.enc
```

## 6. Rotate secrets

1. Edit `backend/.env` with new values.
2. `chmod 600 backend/.env`
3. Restart: `docker compose restart pred-agent`
4. For Telegram bot token: revoke old token via @BotFather, create new, update `.env`.
5. For PRED API key: contact PRED to revoke/issue new key.

## 7. Handle UNKNOWN orders

An UNKNOWN order means placement outcome is uncertain (timeout, 5xx, network error).
The agent **never resends** with a new salt.

1. Check Telegram/app alert for UNKNOWN order.
2. Reconcile:
   - JWT mode: per-parent open orders + Ably + positions + trade history
   - CLOB mode: portfolio open orders + trade history + positions
3. Check on-chain via Base RPC if needed.
4. Once resolved, clear UNKNOWN state (agent does this automatically on reconcile).
5. **Never** manually resend the same order with a new salt.

## 8. PAPER → LIVE switch (real money)

Complete the live checklist in `kit/04_BUILD_PLAN.md` BEFORE switching:

1. Paper mode ran at least 2 weeks; calibration report read.
2. New dedicated wallet with only small funds; Safe created; approvals verified on-chain.
3. `usdc_balance` visible and matches expectations.
4. Fee function verified against a real market.
5. Units check (REST vs Ably) passed.
6. Max per-trade cap $2; daily loss cap $5.
7. **You** manually set `LIVE_TRADING=true` in `.env` on the server.
8. Telegram approval path tested; panic button tested.

Then:
```bash
# Edit backend/.env: LIVE_TRADING=true
chmod 600 backend/.env
docker compose restart pred-agent

# Telegram + app banner confirms LIVE and current limits.
# First order: $1-$2, approved by you, watched end to end.
```

## 9. Compromise response

If compromise is suspected:

1. `/panic` in Telegram or app — pause all proposals + cancel all open orders.
2. Revoke L2 credential: `DELETE /clob/auth/api-key` (CLOB) or rotate JWT.
3. Move funds manually from the Safe to a new wallet.
4. Rotate everything: PRIVATE_KEY, PRED_API_KEY, Telegram token, LLM key.
5. New wallet, new Safe, new approvals.
6. Review audit log (`GET /v1/audit`) for unauthorized actions.

## 7. Daily / weekly checks

- Telegram heartbeat every 6 hours confirms agent alive.
- Check daily PnL and calibration report.
- Verify feeds are not down (>60 s triggers alert).
- Check disk space and backup success.

## Contact PRED for

- X-API-Key issuance (JWT mode)
- Rate limits per route / per key
- Placement idempotency confirmation
- `game_status` value definitions
