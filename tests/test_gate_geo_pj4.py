"""Gate for the geometry augmentation table (READ-ONLY for executors).
Requires <mirror>/index/frames_geo.parquet built for orbit 4.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from jiram_catalog.config import mirror_root

FIXTURE = Path(__file__).parent / "fixtures" / "pj4_ingersoll2022_map_labels.csv"


def _mirror() -> Path:
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    return mirror_root()


def gc_deg(lat1, lon1, lat2, lon2):
    p1, p2 = np.radians(lat1), np.radians(lat2)
    c = np.sin(p1) * np.sin(p2) + np.cos(p1) * np.cos(p2) * np.cos(np.radians(lon1 - lon2))
    return np.degrees(np.arccos(np.clip(c, -1.0, 1.0)))


def test_geo_rows_for_48_paper_frames():
    from jiram_catalog.geo import load_geo

    geo = load_geo(_mirror(), [4])
    fx = pd.read_csv(FIXTURE)
    g = geo[geo["half"] == "M"].set_index("product_id")
    missing = [p for p in fx["PRODUCT_ID"] if p not in g.index]
    assert not missing, missing[:5]
    rows = g.loc[fx["PRODUCT_ID"].tolist()]
    assert rows["geo_ok"].all()
    assert (rows["on_planet_frac"] == 1.0).all()
    d = gc_deg(rows["bore_lat"].to_numpy(), rows["bore_lon_east"].to_numpy(), fx["CENLAT"].to_numpy(), fx["CENLON"].to_numpy())
    assert d.max() <= 0.02, d.max()
    assert np.abs(rows["min_lat"].to_numpy() - fx["MINLAT"].to_numpy()).max() <= 0.03
    assert np.abs(rows["bore_emission"].to_numpy() - fx["EM_ANGLE"].to_numpy()).max() <= 0.02
    polar = fx["MAXLAT"].to_numpy() >= 89.5
    assert rows["pole_inside"].to_numpy()[polar].all()
    assert (rows["lon_span_deg"].to_numpy()[polar] == 360).all()
    assert (rows["lon_span_deg"].to_numpy()[~polar] < 180).all()
    assert rows["median_pixel_km"].between(10, 40).all()
    assert rows["dayside_frac"].between(0, 1).all()


def test_geo_consistency_on_well_conditioned_frames():
    """Boresight vs label centre for orbit-4 M-band frames fully on the planet
    with boresight emission < 40 deg (the label centre is a visible-area
    centroid for partial frames, so those are excluded by construction)."""
    from jiram_catalog.geo import frames_with_geo

    report = _mirror() / "index" / "geo_report.md"
    assert report.exists()
    m = frames_with_geo(_mirror(), [4])
    m = m[(m["half"] == "M") & (m["geom_band"] == "M") & m["geo_ok"] & (m["on_planet_frac"] == 1.0) & (m["bore_emission"] < 40.0)]
    m = m[m["center_lat"].notna()]
    assert len(m) >= 200, len(m)
    d = gc_deg(m["bore_lat"].to_numpy(), m["bore_lon_east"].to_numpy(), m["center_lat"].to_numpy(), m["center_lon"].to_numpy())
    de = np.abs(m["bore_emission"].to_numpy() - m["emission_angle"].to_numpy())
    assert np.median(d) < 0.01, np.median(d)
    assert np.median(de) < 0.02, np.median(de)


def test_frames_with_geo_merge_shape():
    from jiram_catalog.geo import frames_with_geo, load_geo

    m = frames_with_geo(_mirror(), [4])
    geo = load_geo(_mirror(), [4])
    assert len(m) == len(geo)
    assert {"band", "half", "bore_lat", "center_lat", "seq_id"} <= set(m.columns)
