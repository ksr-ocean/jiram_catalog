"""Gate for the empirical fit of the paper's polar grid (READ-ONLY for executors)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pytest

from jiram_catalog.config import mirror_root, paper_data_root

REPO = Path(__file__).resolve().parents[1]
FIT = REPO / "docs" / "reports" / "paper_projection_fit.json"
PAPER = paper_data_root()


def _skip_if_offline():
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")


def test_fit_report_meets_registration_thresholds():
    _skip_if_offline()
    assert FIT.exists(), "run scripts/fit_paper_projection.py"
    fit = json.loads(FIT.read_text())
    frames = fit["frames"]
    assert len(frames) == 48
    shifts = np.array([[f["dline"], f["dsample"]] for f in frames], dtype=float)
    ncc = np.array([f["ncc"] for f in frames], dtype=float)
    assert np.abs(shifts).max() <= 1.0, np.abs(shifts).max()
    assert ncc.min() >= 0.90, ncc.min()
    s = np.array(fit["singular_values"], dtype=float)
    assert abs(s[0] / s[1] - 1.0) <= 0.01, s
    assert fit["hemisphere"] == "N"
    assert 14.0 <= fit["km_per_px"] <= 16.0, fit["km_per_px"]


def test_paper_grid_constant_matches_fit():
    _skip_if_offline()
    from jiram_catalog.reproject import PAPER_GRID

    fit = json.loads(FIT.read_text())
    for key in ["pole_line", "pole_sample", "km_per_px", "lon0_deg", "radius_km"]:
        assert abs(getattr(PAPER_GRID, key) - fit[key]) <= 1e-3, key
    assert PAPER_GRID.clockwise == fit["clockwise"]
    assert PAPER_GRID.hemisphere == fit["hemisphere"]


def test_live_reprojection_of_n01a_matches_paper_map():
    _skip_if_offline()
    from jiram_catalog.geometry import KernelSet, frame_geometry
    from jiram_catalog.reproject import PAPER_GRID, reproject_frame
    from jiram_catalog.vicar import read_vicar

    mirror = mirror_root()
    paper, lab = read_vicar(PAPER / "n01_15km" / "n01a.map")
    img = np.fromfile(mirror / "pds4/juno_jiram_bundle/data_calibrated/orbit04" / f"{lab['PRODUCT_ID']}.IMG", dtype="<f4").reshape(128, 432)  # archive labels say MSB, data are little-endian (verified 2026-09-05)
    ks = KernelSet.for_orbits(mirror, [4])
    try:
        geo = frame_geometry(str(lab["IMAGE_TIME"]), "M", ks)
        out, w = reproject_frame(img, geo, PAPER_GRID, paper.shape)
    finally:
        ks.unload()
    both = (w > 0) & (paper != 0)
    assert both.sum() > 150_000, both.sum()
    a, b = out[both], paper[both]
    ncc = np.corrcoef(a, b)[0, 1]
    assert ncc >= 0.90, ncc
    ca = np.argwhere(w > 0).mean(0)
    cb = np.argwhere(paper != 0).mean(0)
    assert np.abs(ca - cb).max() <= 1.0, (ca, cb)
