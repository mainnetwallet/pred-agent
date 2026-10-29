#!/usr/bin/env python3
"""Generate every PRED Agent logo/icon asset from one geometric definition.

Usage (from repo root):
    pip install cairosvg pillow
    python assets/branding/build_assets.py

Writes:
  assets/branding/pred-agent-icon.svg   rounded badge (README, docs)
  assets/branding/pred-agent-mark.svg   transparent mark only
  backend/src/pred_agent/control_api/web/   icon.svg + PNG/ICO icons for the website/PWA
  mobile/assets/                            Expo app icon, adaptive icon, splash, notification, favicon

The mark is a geometric "P" (stem + ring). The green dot in the counter is the "fair
probability" marker from the app's probability gauge. Colours come from mobile/DESIGN.md.
"""
from __future__ import annotations

import io
from pathlib import Path

import cairosvg
from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
BRAND = ROOT / "assets" / "branding"
WEB = ROOT / "backend" / "src" / "pred_agent" / "control_api" / "web"
MOBILE = ROOT / "mobile" / "assets"

BG = "#0B0F14"      # near-black navy (app background)
BLUE = "#60A5FA"    # primary / accent
GREEN = "#22C55E"   # fair-probability dot
EDGE = "#2A3644"    # hairline so the badge stays visible on dark surfaces

# Bounding-box centre of the unscaled mark, so any scale stays centred on the canvas.
_CX, _CY = 271, 261


def _mark(scale: float, stem: str = BLUE, ring: str = BLUE, dot: str = GREEN) -> str:
    t = f"translate(256 256) scale({scale}) translate(-{_CX} -{_CY})"
    return (
        f'<g transform="{t}">'
        f'<path d="M172 392V132" fill="none" stroke="{stem}" stroke-width="44" stroke-linecap="round"/>'
        f'<circle cx="286" cy="214" r="84" fill="none" stroke="{ring}" stroke-width="44"/>'
        f'<circle cx="286" cy="214" r="26" fill="{dot}"/>'
        "</g>"
    )


def _svg(inner: str) -> str:
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">{inner}</svg>'


def badge_svg() -> str:
    """Rounded-square badge: README, favicon, in-page logo."""
    return _svg(
        f'<rect width="512" height="512" rx="112" fill="{BG}"/>'
        f'<rect x="4" y="4" width="504" height="504" rx="108" fill="none" stroke="{EDGE}" stroke-width="8"/>'
        + _mark(1.05)
    )


def square_svg(scale: float) -> str:
    """Full-bleed square (no rounding): iOS icon, apple-touch-icon, maskable icon."""
    return _svg(f'<rect width="512" height="512" fill="{BG}"/>' + _mark(scale))


def mark_svg(scale: float = 1.05, mono: str | None = None) -> str:
    """Transparent background. `mono` paints every shape one colour (notification icon)."""
    if mono:
        return _svg(_mark(scale, stem=mono, ring=mono, dot=mono))
    return _svg(_mark(scale))


def png(svg: str, size: int) -> Image.Image:
    data = cairosvg.svg2png(bytestring=svg.encode(), output_width=size, output_height=size)
    return Image.open(io.BytesIO(data)).convert("RGBA")


def save_png(svg: str, size: int, path: Path, opaque: bool = False) -> None:
    img = png(svg, size)
    if opaque:  # iOS rejects icons with an alpha channel
        flat = Image.new("RGB", img.size, BG)
        flat.paste(img, mask=img.split()[3])
        flat.save(path, optimize=True)
    else:
        img.save(path, optimize=True)


def main() -> None:
    BRAND.mkdir(parents=True, exist_ok=True)
    MOBILE.mkdir(parents=True, exist_ok=True)

    badge = badge_svg()
    (BRAND / "pred-agent-icon.svg").write_text(badge, encoding="utf-8")
    (BRAND / "pred-agent-mark.svg").write_text(mark_svg(), encoding="utf-8")

    # ── Website / PWA ────────────────────────────────────────────────
    (WEB / "icon.svg").write_text(badge, encoding="utf-8")
    save_png(badge, 192, WEB / "icon-192.png")
    save_png(badge, 512, WEB / "icon-512.png")
    # Maskable: full-bleed, mark inside the 80% safe-zone circle.
    save_png(square_svg(0.92), 512, WEB / "icon-maskable-512.png", opaque=True)
    save_png(square_svg(1.0), 180, WEB / "apple-touch-icon.png", opaque=True)
    ico = [png(badge, s) for s in (16, 32, 48)]
    ico[-1].save(WEB / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])

    # ── Mobile (Expo) ────────────────────────────────────────────────
    save_png(square_svg(1.05), 1024, MOBILE / "icon.png", opaque=True)       # iOS + legacy Android
    # Adaptive foreground: Android crops to a ~61% circle, so keep the mark well inside it.
    save_png(mark_svg(0.8), 1024, MOBILE / "adaptive-icon.png")
    save_png(mark_svg(1.05), 1024, MOBILE / "splash-icon.png")
    save_png(mark_svg(0.95, mono="#FFFFFF"), 96, MOBILE / "notification-icon.png")  # alpha-only on Android
    save_png(badge, 48, MOBILE / "favicon.png")
    print("assets written")


if __name__ == "__main__":
    main()
