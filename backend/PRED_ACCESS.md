# PRED account access (JWT path)

Docs: https://pred-1.gitbook.io/pred-docs/documentation/api-reference/authentication.md and `.../orders.md`.

- **Ordinary JWT** (`PRED_API_KEY` + `PRIVATE_KEY`): `POST /api/v1/auth/login-with-signature` (EIP-712 CreateProxy, `X-API-Key`), refresh at `/api/v1/auth/refresh/token` (rotating), orders at `/api/v1/order/{parent}/place` with `Authorization`, `X-Wallet-Address` (EOA), `X-Proxy-Address` (Safe).
- **CLOB / aggregator** (`/clob/...`, L1/L2, partner key) is a separate program; it is only used when `PRED_AUTH_MODE=clob` or no `PRED_API_KEY` is set.
- Authentication success is not trading permission: login returns `is_enabled_trading`; if false, complete Safe approval and re-login. Live arming fails closed until PRED itself says `true`.
- `PRED_PROXY_ADDRESS` (optional) pins your existing Safe; arming refuses any other.
- Check without trading: `PYTHONPATH=src python scripts/diagnose_pred_access.py --allow-login` (login + balance only).
