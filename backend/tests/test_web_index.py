"""Guard against a broken website: the inline <script> must parse."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

INDEX = Path(__file__).parents[1] / "src" / "pred_agent" / "control_api" / "web" / "index.html"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_inline_script_has_valid_syntax():
    html = INDEX.read_text(encoding="utf-8")
    m = re.search(r"<script>(.*?)</script>", html, re.S)
    assert m, "no inline <script> found"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".js", delete=False) as f:
        f.write(m.group(1))
    node = shutil.which("node")
    res = subprocess.run(  # noqa: S603
        [node, "--check", f.name], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, res.stderr


def test_send_chat_message_defined():
    assert "async function sendChatMessage(" in INDEX.read_text(encoding="utf-8")


def test_index_links_pwa_assets():
    html = INDEX.read_text(encoding="utf-8")
    assert 'rel="manifest"' in html and "/manifest.webmanifest" in html
    # the app-style shell: bottom tabs mirror the mobile app
    for tab in ("Home", "Live", "Chat", "Position", "Settings"):
        assert f'"{tab}"' in html

