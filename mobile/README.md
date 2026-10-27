# Mobile app — how to run

## Prerequisites

- Node.js 18+
- Expo CLI: `npm install -g expo-cli` or use `npx expo`

## Install

```bash
cd mobile
npm install
```

## Run (development)

```bash
npx expo start
```

Then scan the QR code with the Expo Go app on your phone, or press `a` for Android
emulator / `i` for iOS simulator.

## Login

Open the app, enter your server URL (e.g. `https://pred-agent.onrender.com`),
and the username/password that are set as `ADMIN_USERNAME` / `ADMIN_PASSWORD`
in the server environment (Render env vars). The session token is stored in
`expo-secure-store` (Keychain/Keystore). Optionally prefill the URL with
`EXPO_PUBLIC_API_BASE`. Log out from Limits & settings.

## Screens

- **Login** — server URL, username, password
- **Home** — status strip (PAPER/LIVE + paused), balance, exposure, daily PnL
- **Proposals** — card stack, Approve (biometric), Change amount, Reject
- **Panic** — always reachable, two-step confirm, cancel results
- **Limits & settings** — view/edit limits within bounds, pause/resume, devices

## Tests

```bash
npm test
```

Tests cover money formatting, banner states (PAPER/LIVE/PAUSED), and authentication flow.

## Notes

- No optimistic updates for trading actions — waits for server state.
- Offline/disconnected: read-only with "stale" badge.
- Approvals re-fetch the proposal right before confirm.
- Biometric confirmation (Face ID/Touch ID) for sensitive actions.
