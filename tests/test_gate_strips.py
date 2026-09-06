"""Gate for the strip library (READ-ONLY)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from jiram_catalog.config import mirror_root

FIXTURE = Path(__file__).parent / "fixtures" / "pj4_ingersoll2022_map_labels.csv"
CLASSES = {2, 3, 5, 7, 10, 15, 20, 30, 50, 70, 100, 150, 200, 300}


def _mirror() -> Path:
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    return mirror_root()


def _index() -> pd.DataFrame:
    p = _mirror() / "strips" / "strips.parquet"
    assert p.exists(), p
    return pd.read_parquet(p)


def test_index_has_both_orbits_and_bands_and_files_open():
    from jiram_catalog.strips import read_strip

    idx = _index()
    for orbit in (4, 24):
        for band in ("M", "L"):
            assert ((idx["orbit"] == orbit) & (idx["band"] == band)).any(), (orbit, band)
    assert set(idx["km_per_px"].astype(float).round(6)) <= {float(c) for c in CLASSES}
    for _, r in idx.sample(min(10, len(idx)), random_state=0).iterrows():
        ds = read_strip(_mirror(), r["strip_id"])
        v = np.asarray(ds["valid"].values)
        assert v.any()
        assert np.isfinite(np.asarray(ds["lat"].values)[v]).all()
        assert np.isfinite(np.asarray(ds["lon_east"].values)[v]).all()
        assert ds.attrs["strip_id"] == r["strip_id"]


def test_paper_first_sequence_covered_and_frame_maps_onto_strip():
    from jiram_catalog.geometry import KernelSet, frame_geometry
    from jiram_catalog.regions import RegionGrid
    from jiram_catalog.strips import read_strip

    idx = _index()
    fx = pd.read_csv(FIXTURE)
    n01 = set(fx.loc[fx["file"].str.startswith("n01"), "PRODUCT_ID"])
    hits = []
    for _, r in idx[(idx["orbit"] == 4) & (idx["band"] == "M")].iterrows():
        ds = read_strip(_mirror(), r["strip_id"])
        pids = set(np.asarray(ds["product_ids"]).astype(str))
        if pids & n01:
            hits.append((r["strip_id"], pids & n01, ds))
    assert 1 <= len(hits) <= 3, [h[0] for h in hits]
    assert set().union(*[h[1] for h in hits]) == n01
    target = "JIR_IMG_RDR_2017033T114006_V02"
    sid, _, ds = next(h for h in hits if target in h[1])
    grid = RegionGrid.from_dict(__import__("json").loads(ds.attrs["projection"]))
    ks = KernelSet.for_orbits(_mirror(), [4])
    try:
        g = frame_geometry(str(fx.loc[fx["PRODUCT_ID"] == target, "IMAGE_TIME"].iloc[0]), "M", ks)
    finally:
        ks.unload()
    row, col, vis = grid.latlon_to_pixel(g.lat[g.on_planet], g.lon_east[g.on_planet])
    valid = np.asarray(ds["valid"].values)
    ri, ci = np.rint(row).astype(int), np.rint(col).astype(int)
    inside = vis & (ri >= 0) & (ci >= 0) & (ri < valid.shape[0]) & (ci < valid.shape[1])
    ok = np.zeros(len(ri), bool)
    ok[inside] = valid[ri[inside], ci[inside]]
    assert ok.mean() >= 0.95, ok.mean()


def test_latitude_query_and_midlatitude_presence():
    from jiram_catalog.geo import frames_with_geo
    from jiram_catalog.strips import load_strips

    idx = _index()
    q = load_strips(_mirror(), lat_min=-45, lat_max=45)
    assert ((q["lat_max"] >= -45) & (q["lat_min"] <= 45)).all()
    m = frames_with_geo(_mirror(), [4, 24])
    units = m[m["geo_ok"] & (m["on_planet_frac"] >= 0.3) & (m["bore_emission"] <= 75) & (m["bore_lat"].abs() < 45)]
    if len(units) >= 10:
        assert (idx["center_lat"].abs() < 45).any()
