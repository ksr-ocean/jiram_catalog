"""Gate for the GUI v2 front end (READ-ONLY). Runs the Playwright e2e suite against a live backend."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DIST = REPO / "src" / "jiram_catalog" / "webapp" / "dist"


def _skip():
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")


def test_bundle_present_and_small():
    _skip()
    index = DIST / "index.html"
    assert index.exists(), "run `npm run build` in frontend/"
    total = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    assert total < 8 * 1024 * 1024, total
    html = index.read_text()
    assert "/assets/" in html and (".js" in html)


def test_e2e_suite_passes_against_live_backend():
    _skip()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "jiram_catalog.gui_cmd", "--port", str(port), "--no-browser"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 120
        ok = False
        while time.time() < deadline:
            try:
                if urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5).status == 200:
                    ok = True
                    break
            except Exception:
                time.sleep(2)
        assert ok, "backend did not come up"
        env = dict(os.environ, E2E_BASE_URL=f"http://127.0.0.1:{port}")
        r = subprocess.run(["npx", "playwright", "test", "--reporter=line"], cwd=REPO / "frontend", env=env,
                           capture_output=True, text=True, timeout=1200)
        assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-2000:]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
