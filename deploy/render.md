# Render deployment guide — PRED Agent

Ekta Render web service e **website + mobile app API + Telegram bot** tinta-i cholbe.

## Deploy (Blueprint diye, sobcheye sohoj)

1. Render Dashboard -> **New -> Blueprint** -> GitHub repo `mainnetwallet/pred-agent` select koro (`render.yaml` auto-detect hobe).
2. Environment variables dashboard e boshao (secret, kothao commit korbe na):

| Variable | Ki |
|---|---|
| `ADMIN_USERNAME` | website/app login username (default `admin`) |
| `ADMIN_PASSWORD` | website/app login password (**lomba, unique** dao) |
| `SESSION_SECRET` | login token sign korar secret (Blueprint auto generate kore) |
| `TELEGRAM_BOT_TOKEN` | @BotFather theke paoa bot token |
| `TELEGRAM_ALLOWED_USER_ID` | tomar numeric Telegram ID (@userinfobot theke) |
| `LIVE_TRADING` | `false` rakho (PAPER mode) |
| `PRIVATE_KEY`, `PRED_API_KEY`, `LLM_API_KEY`, `BASE_RPC_URL` | agent er jonno (optional ekhon) |

3. Deploy shesh hole `https://<service>.onrender.com/health` -> `{"ok":true,"status":"healthy"}`.

**Starter (paid) plan dao.** Free plan idle hole ghumay, tokhon Telegram bot bondho hoye jay.

## Control korar 3 ta way

### 1. Website
`https://<service>.onrender.com/` kholo -> `ADMIN_USERNAME` / `ADMIN_PASSWORD` diye login.
Status, Pause/Resume, **PANIC**, proposals Approve/Reject, positions, orders, limits.

### 2. Mobile app
App kholo -> Server URL te `https://<service>.onrender.com`, ar same username/password. Token secure storage e thake.
Phone e build: `mobile/README.md` ar `.github/workflows/mobile-build.yml`.

### 3. Telegram
Bot ke message pathao (sudhu `TELEGRAM_ALLOWED_USER_ID` er user kaj korbe, baki sobai ignored):
`/status` `/pause` `/resume` `/panic` `/proposals` `/approve <id> [amount]` `/reject <id>` `/limits`

## Password change korbe kivabe
Render -> Environment -> `ADMIN_PASSWORD` update -> redeploy. Purono sob login token invalid hobe na jodi `SESSION_SECRET` same thake; sob session ekshathe bondho korte `SESSION_SECRET` o bodlao.

## Security notes
- Login e 5 bar bhul password dile 5 minute lock.
- Token expire hoy `SESSION_TTL_HOURS` (default 24) ghonta pore.
- Control layer kokhono `LIVE_TRADING` on korte pare na; seta shudhu Render env theke.
- Render URL public https, tai password lomba rakho.

## Ekhono baki (jene rakho)
- Proposals/orders/positions ekhon **memory te**; redeploy/restart hole clear hoy. DB persist pore.
- Scanner/realtime/LLM strategy ekhono `main.py` e wire kora na, tai nijer theke notun proposal toiri hoy na; control layer (login, pause, panic, approve/reject, Telegram) ready.
- `/panic` ekhon paper order cancel kore; LIVE cancel-all live executor wire korar por.
- `docker-compose.yml`/Tailscale option ager moto private deploy er jonno ache.
