"""Logo / icon assets: files exist with the right sizes, are served, and are wired into the web UI."""
from __future__ import annotations

import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from pred_agent.control_api import app as app_module
from pred_agent.control_api.app import create_app

WEB = Path(app_module.__file__).parent / "web"
INDEX = (WEB / "index.html").read_text(encoding="utf-8")
PNG_SIG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def client():
    return TestClient(create_app())


PNG_SIZES = {
    "icon-192.png": (192, 192),
    "icon-512.png": (512, 512),
    "icon-maskable-512.png": (512, 512),
    "apple-touch-icon.png": (180, 180),
}


def _png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == PNG_SIG, f"{path.name} is not a PNG"
    return struct.unpack(">II", data[16:24])  # IHDR width, height


@pytest.mark.parametrize(("name", "size"), PNG_SIZES.items())
def test_png_icons_have_expected_size(name, size):
    assert _png_size(WEB / name) == size


def test_opaque_icons_have_no_alpha_channel():
    # iOS rejects apple-touch / maskable icons with transparency (colour type 6 = RGBA)
    for name in ("apple-touch-icon.png", "icon-maskable-512.png"):
        assert (WEB / name).read_bytes()[25] != 6, f"{name} must not have an alpha channel"


def test_favicon_ico_has_standard_sizes():
    data = (WEB / "favicon.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    sizes = {(data[6 + 16 * i] or 256, data[7 + 16 * i] or 256) for i in range(count)}
    assert {(16, 16), (32, 32), (48, 48)} <= sizes


def test_icon_svg_is_valid_brand_mark():
    svg = (WEB / "icon.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg") and 'viewBox="0 0 512 512"' in svg
    assert "#0B0F14" in svg and "#60A5FA" in svg and "#22C55E" in svg  # DESIGN.md palette


@pytest.mark.parametrize("name", sorted(app_module._WEB_ICONS))
def test_every_icon_route_is_served(client, name):
    r = client.get(f"/{name}")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(app_module._WEB_ICONS[name])
    assert "max-age" in r.headers["cache-control"]
    assert client.head(f"/{name}").status_code == 200


def test_every_manifest_icon_is_served(client):
    icons = client.get("/manifest.webmanifest").json()["icons"]
    assert any(i["purpose"] == "maskable" for i in icons)
    sizes = {i["sizes"] for i in icons}
    assert {"192x192", "512x512"} <= sizes  # Chrome's installability requirement
    for icon in icons:
        assert client.get(icon["src"]).status_code == 200


def test_icon_routes_stay_out_of_openapi(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert not any(p.lstrip("/") in app_module._WEB_ICONS for p in paths)


def test_missing_icon_file_is_404_not_a_crash(client, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "_WEB_DIR", tmp_path)  # no icon files here
    assert client.get("/icon.svg").status_code == 404
    assert client.get("/ready").status_code == 200  # the API itself keeps working


def test_index_wires_icons_and_login_logo():
    assert 'href="/favicon.ico"' in INDEX
    assert 'href="/apple-touch-icon.png"' in INDEX  # iOS ignores SVG touch icons
    assert 'rel="icon" href="/icon.svg"' in INDEX
    login = INDEX.split('<section id="login"')[1].split("</section>")[0]
    assert 'src="/icon.svg"' in login and 'alt="PRED Agent logo"' in login
    assert 'id="hdrLogo"' in INDEX  # Home header brand mark


def test_pyproject_ships_icon_assets():
    toml = (Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    for pattern in ("*.svg", "*.png", "*.ico"):
        assert f"control_api/web/{pattern}" in toml
