# Render deployment guide — PRED Agent

Ekta Render web service e **website + mobile app API + Telegram bot** tinta-i cholbe.

## Deploy (Blueprint diye, sobcheye sohoj)

1. Render Dashboard → **New → Blueprint** → GitHub repo `mainnetwallet/pred-agent` select koro (`render.yaml` auto-detect hobe).
2. Blueprint automatically ekta **Postgres database** (`pred-agent-db`) toiri kore ar `DATABASE_URL` service e inject kore — tumi alada kore set korte hobe na.
3. Environment variables dashboard e boshao (secret, kothao commit korbe na):

| Variable | Ki |
|---|---|
| `ADMIN_USERNAME` | website/app login username (default `admin`) |
| `ADMIN_PASSWORD` | website/app login password (**lomba, unique** dao) |
| `SESSION_SECRET` | login token sign korar secret (Blueprint auto generate kore) |
| `TELEGRAM_BOT_TOKEN` | @BotFather theke paoa bot token |
| `TELEGRAM_ALLOWED_USER_ID` | tomar numeric Telegram ID (@userinfobot theke) |
| `LIVE_TRADING` | `false` rakho (PAPER mode) |
| `LLM_API_KEY` | LLM provider er API key |
| `LLM_MODEL` | model name (jemon `gpt-4o-mini`, `deepseek-chat`) — **tool-calling support dorkar** |
| `LLM_BASE_URL` | provider er API root, jemon `https://api.openai.com/v1` ba `https://openrouter.ai/api/v1` |
| `PRIVATE_KEY`, `BASE_RPC_URL` | agent er jonno. `PRIVATE_KEY` LIVE er jonno lagbe (PAPER e na); `BASE_RPC_URL` optional (on-chain approval check) |
| `PRED_AUTH_MODE` | optional, default `clob`. LIVE order shudhu `clob` mode e hoy. `jwt` shudhu enablement CLI er jonno |
| `PRED_API_KEY` | **optional**, shudhu `PRED_AUTH_MODE=jwt` hole lagbe. CLOB e lagbe na; set thakleo mode bodlay na |

> **Note:** `DATABASE_URL` is injected automatically by the Blueprint via `fromDatabase`. Do NOT set it manually.

4. Deploy shesh hole `https://<service>.onrender.com/health` → `{"ok":true,"status":"healthy"}`.

**Starter (paid) plan dao.** Free plan idle hole ghumay, tokhon Telegram bot bondho hoye jay.

---

## Postgres (new in Phase 8)

`render.yaml` e `databases:` section add kora hoyeche. Blueprint deploy korle:
- Render automatically `pred-agent-db` Postgres database toiri kore (free plan: 256 MB, 90 days retention).
- `DATABASE_URL` service e env var hisebe inject hoy.
- Alembic migration startup e cholbe: `alembic upgrade head`.
- Proposals, positions, rules **restart er por persist kore** (DB e save, in-memory e na).

Upgrade kora uchhit: **Render Postgres Starter ($7/mo)** — unlimited retention, daily backups.

---

## LLM thik ache kina check

Login kore `GET /v1/llm` (configured? model?) ar `POST /v1/llm/test` (ekta chhoto call) chalao. Key kokhono response e ashe na.

**Model selection:** Je provider OpenAI-compatible `/chat/completions` bole, shob cholbe. LLM shudhu research/probability calculation e use hoy — approve/execute/trade korar **kono tool nei**. Model na dile agent LLM chhara (adjustment 0) cholbe.

Recommended models (tool-calling support confirmed): `gpt-4o-mini`, `gpt-4o`, `deepseek-chat`, `claude-3-5-haiku-20241022`.

---

## Chat usage (English / Bengali / Banglish)

Control API e POST `/v1/chat` (or Telegram) e natural language pathao:

**Fixtures:**
- "ekhon live match ki ki ache?" → current live fixtures list (Dhaka time e)
- "upcoming match gulo dekhao" → next upcoming fixtures with kickoff in Asia/Dhaka
- "আজকের লাইভ ম্যাচ দেখাও" → same in Bengali
- "koto ta match chole?" → total active fixture count

**Rules / Exit triggers:**
- "$5 profit hole sell koro" → creates a rule proposal: if PnL ≥ $5, auto-exit
- "loss $2 gele stop koro" → stop-loss rule at -$2
- "60 cents hole close koro" → price-based exit rule at 60¢
- "profit 10% gele becho" → profit_pct based exit

**Status:**
- "/status" → mode, balance, open positions, daily PnL
- "/pause" → halt new proposals (exit rules still run)
- "/resume" → resume normal operation
- "/panic" → pause + reject all pending proposals + cancel all open orders

---

## Control korar 3 ta way

### 1. Website
`https://<service>.onrender.com/` kholo → `ADMIN_USERNAME` / `ADMIN_PASSWORD` diye login.
Status, Pause/Resume, **PANIC**, proposals Approve/Reject, positions, orders, limits.

### 2. Mobile app
App kholo → Server URL te `https://<service>.onrender.com`, ar same username/password. Token secure storage e thake.
Phone e build: `mobile/README.md` ar `.github/workflows/mobile-build.yml`.

### 3. Telegram
Bot ke message pathao (sudhu `TELEGRAM_ALLOWED_USER_ID` er user kaj korbe, baki sobai ignored):
`/status` `/pause` `/resume` `/panic` `/proposals` `/approve <id> [amount]` `/reject <id>` `/limits`

---

## Pause / Panic semantics

| Action | Effect |
|---|---|
| **Pause** | No new entry proposals accepted. Exit rules (close positions) still fire. |
| **Resume** | Normal operation resumed. |
| **Panic** | Pause + reject ALL pending proposals + cancel ALL open orders. Use in emergency. |

Rules in BLOCKED state (stale book, UNKNOWN order, veto): never execute until resolved.

---

## Password change korbe kivabe

Render → Environment → `ADMIN_PASSWORD` update → redeploy. Purono sob login token invalid hobe na jodi `SESSION_SECRET` same thake; sob session ekshathe bondho korte `SESSION_SECRET` o bodlao.

---

## Security notes

- Login e 5 bar bhul password dile 5 minute lock.
- Token expire hoy `SESSION_TTL_HOURS` (default 24) ghonta pore.
- Control layer kokhono `LIVE_TRADING` on korte pare na; seta shudhu Render env theke.
- Render URL public https, tai password lomba rakho.
- LLM er কাছে কোনো approve/execute tool nei — শুধু research JSON return করে।

---

## Ekhono baki (manual steps)

1. `LLM_MODEL` set koro — tool-calling support ache emon model choose koro.
2. `PRED_API_KEY` dorkar nei (CLOB mode e use hoy na). Shudhu `PRED_AUTH_MODE=jwt` e enablement CLI chalate hole lagbe.
3. `PRIVATE_KEY` set koro LIVE er jonno (PAPER mode e dorkar nei).
4. Alembic migration confirm koro: `alembic upgrade head` startup e automatically cholbe.
5. PAPER → LIVE checklist complete koro (README.md section 8 dekho).
6. `docker-compose.yml`/Tailscale option private VPS deploy er jonno ache.

### CLOB credential issuance

`ensure_credentials()` first recovers already-issued credentials (`derive-api-key`); only if
PRED has never issued any for this EOA does it issue them (`api-key`, no partner key, no
account created, no quota consumed — docs: existing eligible users can do this without a
partner key). This runs automatically on live-trading startup and in the enablement CLI, so no
manual step is required for an eligible account the first time it uses CLOB.

If PRED refuses BOTH calls, the EOA is not an eligible existing account's primary wallet —
PRED's decision, not something this agent can work around. Run
`PYTHONPATH=src PRIVATE_KEY=0x... python backend/scripts/diagnose_clob_auth.py` (never places
an order) and send the redacted report to PRED support, asking them to enable CLOB access for
this EOA or to confirm the account's primary wallet. `backend/scripts/issue_clob_credentials.py`
remains available if you'd rather issue credentials by hand, with an explicit
`--confirm-existing-account`, before running anything else — it is optional, not required.
