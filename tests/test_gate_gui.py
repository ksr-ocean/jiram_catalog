"""Gate for the GUI first version (READ-ONLY). Needs the real mirror."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pytest

from jiram_catalog.config import mirror_root


def _mirror() -> Path:
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    return mirror_root()


def test_app_builds_with_real_data_and_tabs_save(tmp_path):
    import panel as pn

    from jiram_catalog.gui import app, data

    m = _mirror()
    cat = data.catalog_table(m)
    assert len(cat) >= 40_000, len(cat)
    strips = data.strips_table(m)
    assert len(strips) >= 200, len(strips)
    tabs = app.build(m)
    assert isinstance(tabs, pn.Tabs) and len(tabs) == 3
    for i, tab in enumerate(tabs):
        out = tmp_path / f"tab{i}.html"
        pn.panel(tab).save(str(out))
        assert out.stat().st_size > 1000


def test_poles_view_opens_sequence_stack():
    from jiram_catalog.gui import data

    m = _mirror()
    ds = data.open_stack(m / "regions" / "north_pole_paper" / "M_orbits4_sequence.nc")
    assert ds.sizes["time"] == 25
    frame = np.asarray(ds["image"].isel(time=0).values)
    assert np.isfinite(frame).any()


def test_strip_statistics_available_for_first_strip():
    from jiram_catalog.gui import data

    m = _mirror()
    sid = str(data.strips_table(m)["strip_id"].iloc[0])
    st = data.strip_stats(m, sid)
    assert "k" in st.coords and "r" in st.coords
    assert (m / "gui_cache" / f"stats_{sid}.nc").exists()


def test_panel_serve_answers_http():
    m = _mirror()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = subprocess.Popen(
        [sys.executable, "-m", "jiram_catalog.gui_cmd", "--mirror", str(m), "--port", str(port), "--no-browser"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.time() + 90
        status = None
        while time.time() < deadline:
            try:
                status = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).status
                break
            except Exception:
                time.sleep(2)
        assert status == 200, status
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
