# PRED Agent branding

| File | Use |
|------|-----|
| `pred-agent-icon.svg` | Rounded badge — README, docs, social preview source |
| `pred-agent-mark.svg` | Bare mark on a transparent background |
| `build_assets.py` | Generates **all** icons below from one definition |

The mark is a geometric **P** (stem + ring) with a green "fair probability" dot in the counter, on
the app's near-black navy (`#0B0F14`). Colours and usage rules: [`mobile/DESIGN.md`](../../mobile/DESIGN.md#logo).

## Regenerate

```bash
pip install cairosvg pillow
python assets/branding/build_assets.py
```

## Where the generated files go

| Output | Used by |
|--------|---------|
| `backend/src/pred_agent/control_api/web/icon.svg`, `favicon.ico` | Website favicon, login/chat/sidebar logo |
| `…/web/icon-192.png`, `icon-512.png`, `icon-maskable-512.png` | PWA manifest (install to home screen) |
| `…/web/apple-touch-icon.png` | iOS "Add to Home Screen" (iOS ignores SVG touch icons) |
| `mobile/assets/icon.png` | iOS app icon + legacy Android icon (1024², opaque) |
| `mobile/assets/adaptive-icon.png` | Android adaptive icon foreground (background `#0B0F14`) |
| `mobile/assets/splash-icon.png` | Splash screen |
| `mobile/assets/notification-icon.png` | Android notification small icon (white silhouette) |
| `mobile/assets/favicon.png` | Expo web favicon |

In-app, the logo is drawn from vector by `mobile/components/Logo.tsx`.

## Not in the repo (set by hand)

- **Telegram bot avatar** — BotFather → `/setuserpic` → upload `mobile/assets/icon.png`.
- **GitHub social preview** — repo Settings → Social preview → upload `mobile/assets/icon.png`.
