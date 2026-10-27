# Render deployment guide — PRED Agent

Render e deploy korar guide. Tailscale (private) ar Render URL (public) dui-i rakha hobe.

## Age jene rakho (repo er current obostha)

1. **`backend/src/pred_agent/main.py` ekhono stub.** `TODO: wire scanner, realtime, execution, telegram, control_api` lekha ache. Kono port e listen kore na, tai Render "no open port detected" bole deploy fail dekhabe. Uvicorn diye Control API `0.0.0.0:$PORT` e start korte hobe.
2. **`docker-compose.yml` Render e kaj kore na.** Render compose chalay na. Postgres alada Render Postgres hishebe nite hobe. `CONTROL_API_BIND` (Tailscale IP bind) Render e onno rokom.
3. **Device pairing in-memory** (`control_api/auth.py` er `DeviceAuth`). Restart/redeploy hole phone abar pair korte hobe. Persist korte DB te rakhte hobe.

## Dui system kivabe rakhbe

- **Render URL (public, https):** shudhu `/health` ar device-token auth diye protected endpoint. Control API public e dewa uchit na, karon agent er `PRIVATE_KEY` (wallet) thake.
- **Tailscale (private):** admin, pairing, phone app er jonno. Render e kernel TUN nai, tai container er bhitore `tailscaled --tun=userspace-networking` chalate hobe, ephemeral auth key diye. Deploy e ekbar verify koro.

## Setup steps

1. **Postgres:** Render Dashboard -> New -> PostgreSQL. **Internal Database URL** copy koro.
2. **Web Service:** New -> Web Service -> GitHub repo `mainnetwallet/pred-agent`.
   - Runtime: **Docker**
   - Dockerfile Path: `backend/Dockerfile`
   - Docker Build Context Directory: `.` (repo root)
3. **Plan:** Agent 24/7 scan kore ar Telegram poll kore. Free web service idle hole ghumay, tai **paid (Starter)** plan nao. Free Postgres er limit Render pricing page e dekhe nio.
4. **Environment variables** (secret hishebe, `.env` commit korbe na):
   - `PRIVATE_KEY`, `PRED_API_KEY`, `LLM_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_USER_ID`, `BASE_RPC_URL`
   - `DATABASE_URL` = Render Postgres URL
   - `LIVE_TRADING=false` (age paper mode, live checklist shesh na hole `true` koro na)
   - `TS_AUTHKEY` = Tailscale reusable + ephemeral auth key
5. **Health Check Path:** `/health`
6. **Migration:** Deploy er por Render Shell e `alembic upgrade head` (ba pre-deploy command).
7. **Verify:** `https://<service>.onrender.com/health` -> `{"ok":true,"status":"healthy"}`. Phone e Tailscale on kore tailnet address diyeo check koro.
8. Phone app e `EXPO_PUBLIC_API_BASE` set koro (Tailscale address ba Render URL).

## Baki kaj (code)

- `main.py` te uvicorn wire kora
- `DATABASE_URL` support
- `render.yaml` (Blueprint)
- Tailscale userspace start kora entrypoint script
- Device pairing DB te persist kora
