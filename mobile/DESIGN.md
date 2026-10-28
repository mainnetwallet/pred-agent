# Mobile App Design Direction — pred-agent

## Visual philosophy

A calm, high-contrast trading look — not a templated default. The app is a financial tool for
real money (even if tiny), so it must feel serious, legible, and trustworthy. One glance must
tell the user: PAPER or LIVE, paused or active, how much is at risk, and what needs a decision.

## Palette

Dark default, light option (system-follows). One accent per side:

| Role | Dark | Light | Usage |
|------|------|-------|-------|
| Background | `#0B0F14` (near-black navy) | `#F4F6F8` | App background |
| Surface | `#141B23` | `#EFF2F5` | Cards, modals |
| Text primary | `#E8ECF1` | `#1A1F26` | Body copy |
| Text secondary | `#8FA3B8` | `#5B6B7A` | Captions, meta |
| Accent LONG | `#22C55E` (emerald) | `#15803D` | Long / YES exposure |
| Accent SHORT | `#EF4444` (rose) | `#B91C1C` | Short / NO exposure |
| Accent NEUTRAL | `#60A5FA` (blue) | `#2563EB` | Draw / neutral |
| Alert | `#F59E0B` (amber) | `#D97706` | Warnings, spreads |
| Danger | `#DC2626` (red) | `#B91C1C` | Panic, losses |

Color is never used alone: every LONG/SHORT indicator carries a label and icon (▲/▼ or
LONG/SHORT text).

## Typography

- Headings: `Inter` (clean sans) — display use at 24–32px, tight tracking.
- Numbers: `JetBrains Mono` (monospace) — prices, quantities, PnL always in monospace so
  digits align and are scannable.
- Body: `Inter` 14–16px.

## Spacing

8px grid. Touch targets minimum 44px. Cards have 16px padding, 12px radius.

## Components

### Probability gauge (signature component)

A single horizontal bar per fixture outcome. Two markers on one bar:
- Market price (mid) — thin tick
- My p_fair range — thicker band from `low` to `high`, with `p_fair` as a dot

Label shows market price vs my estimate, plus edge badge. Not color-only: band includes
pattern/texture for color-blind users.

### Status banner

Persistent, unmistakable:
- PAPER: calm slate-blue, calm icon (📄), "PAPER — fake funds"
- LIVE: strong red-orange border left, bold text, "LIVE — real funds"
Paused adds a yellow pause icon.

### Money display

All amounts rendered from decimal strings. No float math in UI for values that get sent.
Two-decimal USD strings; prices in cents as strings.

## Navigation

Bottom tabs: Home · Live · Chat · Position · Settings. Every tab has the same header: title,
PAPER/LIVE/PAUSED pill, and an always-visible Panic button.
Pending proposals show as a badge on the Home tab. PnL is always signed and carries ▲/▼.

## Screens (per kit/08_MOBILE_APP.md)

1. Pairing — QR scan or manual code, device naming, token → secure store
2. Home — status strip (PAPER/LIVE + paused), balance, exposure, daily PnL, pending count, live fixtures
3. Fixtures — live & upcoming, score, minute, kickoff, p_fair vs price per outcome, edge badge
4. Fixture detail — Home/Draw/Away cards: mid price, p_fair range, depth mini-bars, research with source links, max suggested
5. Order book — bids/asks in cents, spread, synthetic levels striped, read-only, last update age
6. Proposals — card stack, Approve (biometric), Change amount, Reject, countdown to expiry
7. Orders — open, delayed, UNKNOWN, filled, cancelled; cancel (biometric); raw status
8. Positions — side, qty, avg price, mark, PnL; Close → exit proposal
9. Limits & settings — view/edit within bounds (biometric), pause/resume, devices list, revoke
10. Panic — always reachable (header + floating), two-step confirm, per-order cancel results
11. Reports — daily summary, calibration chart

## Behavior rules

- LIVE vs PAPER banner unmistakable; PAPER is calm, LIVE is bold.
- No optimistic updates for trading — wait for server state.
- Offline/disconnected: read-only, "stale" badge, last-update time, action buttons disabled.
- Approvals show exactly what will be sent (side, price, qty, amount, fee estimate) and re-fetch
  the proposal right before confirm.
- Biometric confirmation for sensitive actions → produces X-Confirm header.
- WebSocket reconnects, re-fetches state over REST on reconnect.
