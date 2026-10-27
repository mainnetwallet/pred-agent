# Phase 7 completion report — Deploy and harden

## 1. Docker

- `backend/Dockerfile` — slim Python 3.11, multi-stage build, non-root user (`predagent`), healthcheck, no secrets baked in.
- `docker-compose.yml` — agent + Postgres, `restart: unless-stopped`, named volumes for DB, secrets file for DB password, Control API NOT exposed publicly (bound to Tailscale IP via `CONTROL_API_BIND`).

## 2. Tailscale

- `deploy/tailscale.md` — install on VPS and phone, bind Control API to tailnet IP, UFW firewall rules (deny all except SSH key-only or Tailscale SSH), pair phone via `scripts/pair_device.py`.

## 3. Server hardening

- `deploy/hardening.md` — non-root user, `.env` mode 600, UTC + NTP, unattended security updates, disk encryption, SSH hardening, firewall, fail2ban, pip-audit, no secrets in image.

## 4. Backups

- `deploy/backup.sh` — nightly encrypted DB dump (AES-256, PBKDF2, key file), 7-day local retention, optional off-box upload (S3/rclone).
- `deploy/restore.sh` — decrypt and restore with confirmation prompt.
- `deploy/secrets/README.txt` — how to create `db_password.txt` and `backup.key` locally (never commit).

## 5. Logging

- JSON structured logs with central redaction filter (already implemented in `pred_agent/util/redaction.py` with canary tests).
- Log rotation handled by Docker daemon (`docker-compose.yml` can add `logging` options).

## 6. Health / readiness

- `/health` and `/ready` endpoints implemented in Control API.
- Docker `HEALTHCHECK` polls `/health`.

## 7. Runbook

- `README.md` — start, stop, update, rollback, restore, rotate secrets, UNKNOWN orders, PAPER→LIVE switch, compromise response.

## 8. Dependency pinning + pip-audit

- `backend/pyproject.toml` — all dependencies pinned.
- `.github/workflows/ci.yml` — ruff, mypy, pytest, pip-audit on every push/PR.
- `deploy/ci.md` — monthly update routine.

## Remaining manual steps (you perform)

- Build and push the Docker image to your VPS.
- Set `CONTROL_API_BIND` to your Tailscale IP in `.env`.
- Set `LIVE_TRADING` only when you are ready (manual, after live checklist).
- Test backup restore.
- Run 7-day unattended paper run.

## Status

Phase 7 code and documentation complete. Project code complete.
Final deployment and `LIVE_TRADING=true` switch are your manual decision.
