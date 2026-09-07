"""Gate for the JunoCam per-framelet geometry engine (READ-ONLY)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from jiram_catalog.config import mirror_root

REPORT = Path(__file__).resolve().parents[1] / "docs" / "reports" / "junocam_pj4_geometry.json"


def _load():
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    assert REPORT.exists(), "run scripts/junocam_pj4_geometry_check.py"
    return json.loads(REPORT.read_text())


def test_limb_refinement_statistics():
    r = _load()
    imgs = [i for i in r["images"] if i.get("refined")]
    assert len(imgs) >= 20, len(imgs)
    dt = np.abs(np.array([i["dt_refined_s"] for i in imgs], dtype=float))
    assert np.median(dt) < 0.1 and dt.max() < 0.25, (np.median(dt), dt.max())
    after = np.array([i["limb_residual_px_after"] for i in imgs], dtype=float)
    assert np.median(after) < 1.0 and np.percentile(after, 90) < 2.0, (np.median(after), np.percentile(after, 90))
    before = np.array([i["limb_residual_px_before"] for i in imgs], dtype=float)
    assert np.median(after) <= np.median(before)
    t = np.array([i["geometry_seconds"] for i in imgs], dtype=float)
    assert t.max() < 60.0, t.max()


def test_band_registration_after_reprojection():
    r = _load()
    reg = r["registration"]
    assert len(reg) >= 5
    for item in reg:
        assert abs(item["rg_shift_px"][0]) <= 1.0 and abs(item["rg_shift_px"][1]) <= 1.0, item
        assert abs(item["rb_shift_px"][0]) <= 1.0 and abs(item["rb_shift_px"][1]) <= 1.0, item
        assert item["n_overlap"] > 5000


def test_live_geometry_on_one_image():
    _load()
    from jiram_catalog.geometry import KernelSet
    from jiram_catalog.junocam.geometry import image_geometry, load_junocam_ik

    root = mirror_root()
    ix = pd.read_parquet(root / "junocam" / "index" / "junocam_images.parquet")
    row = ix[(ix["orbit"] == 4) & (ix["level"] == "RDR") & ix["product_id"].str.contains("_2017033_") & ix["img_present"]].iloc[0]
    ks = KernelSet.for_orbits(root, [4])
    load_junocam_ik(root)
    try:
        g = image_geometry(row, ks, mirror=root, refine=False)
    finally:
        ks.unload()
    assert g.lat.shape[2:] == (128, 1648) and g.lat.shape[0] == int(row["n_framelets"])
    assert np.isfinite(g.lat[g.on_planet]).all() and g.on_planet.any()
    assert np.nanmax(g.emission) <= 90.0 + 1e-3
