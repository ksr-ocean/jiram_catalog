"""Gate for the classical tracker vs the paper's TRACKER4 vectors (READ-ONLY)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

REPORT = Path(__file__).resolve().parents[1] / "docs" / "reports" / "tracking_pj4.json"


def _load():
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    assert REPORT.exists(), "run scripts/classical_tracking_pj4.py"
    return json.loads(REPORT.read_text())


def test_index_base_determined():
    r = _load()
    assert r["index_base"] in (0, 1)


def test_tracker_agrees_with_tracker4_on_paper_maps():
    a = _load()["A"]["pooled"]
    assert a["n"] >= 20_000, a
    assert a["median_px"] <= 1.0, a
    assert a["frac_1"] >= 0.5, a


def test_pipeline_reprojections_track_like_paper_maps():
    b = _load()["B"]["pooled"]
    assert b["n"] >= 10_000, b
    assert b["median_px"] <= 1.5, b
