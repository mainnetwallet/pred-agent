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

## Connect to Control API

The app connects to `EXPO_PUBLIC_API_BASE` (default `http://127.0.0.1:8000`).
For Tailscale, set this to your VPS tailnet address:

```bash
EXPO_PUBLIC_API_BASE=http://<tailscale-ip>:8000 npx expo start
```

## Pairing

1. On the server: `python scripts/pair_device.py --name "my-phone"`
2. In the app: enter the pairing code and name the device
3. Token is stored in `expo-secure-store` (Keychain/Keystore)

## Screens

- **Pairing** — enter code, name device
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
