"""Gate: the PJ4 (orbit04) label index must reproduce the Ingersoll et al. 2022
mosaic frames. READ-ONLY for executors. Requires the real mirror:
  jiram-catalog mirror --orbits 4 --kinds labels && jiram-catalog index --orbits 4
Set JIRAM_SKIP_GATES=1 to skip in offline unit-test runs.
"""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from jiram_catalog.config import mirror_root

FIXTURE = Path(__file__).parent / "fixtures" / "pj4_ingersoll2022_map_labels.csv"


def _frames() -> pd.DataFrame:
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    mirror = mirror_root()
    parquet = mirror / "index" / "frames.parquet"
    assert parquet.exists(), f"index missing: {parquet} (run mirror+index for orbit 4)"
    return pd.read_parquet(parquet)


def _fixture() -> pd.DataFrame:
    fx = pd.read_csv(FIXTURE)
    fx["group"] = fx["file"].str.slice(0, 3)  # n01..n04
    return fx


def test_all_48_paper_frames_present_with_matching_geometry():
    df = _frames().set_index("product_id")
    fx = _fixture()
    missing = [p for p in fx["PRODUCT_ID"] if p not in df.index]
    assert not missing, f"{len(missing)} paper frames missing from index: {missing[:5]}"
    rows = df.loc[fx["PRODUCT_ID"].tolist()]
    assert (rows["orbit"] == 4).all()
    assert (rows["family"] == "IMG").all()
    assert (rows["band"] == "M").all(), rows["band"].value_counts().to_dict()
    assert (rows["lines"] == 128).all() and (rows["samples"] == 432).all()
    t_index = pd.to_datetime(rows["start_time"]).dt.tz_localize(None).to_numpy()
    t_fix = pd.to_datetime(fx["IMAGE_TIME"]).to_numpy()
    dt = np.abs((t_index - t_fix) / np.timedelta64(1, "ms"))
    assert dt.max() <= 1.0, f"start_time mismatch up to {dt.max()} ms"
    for col, fcol, tol in [
        ("center_lat", "CENLAT", 2e-3),
        ("center_lon", "CENLON", 2e-3),
        ("emission_angle", "EM_ANGLE", 2e-3),
        ("incidence_angle", "IN_ANGLE", 2e-3),
        ("min_lat", "MINLAT", 2e-3),
        ("sc_altitude_km", "SPACECRAFT_ALTITUDE", 0.5),
        ("h_pixel_scale_m", "PXL_HSCALE", 0.5),
    ]:
        d = np.abs(rows[col].to_numpy(dtype=float) - fx[fcol].to_numpy(dtype=float))
        assert np.nanmax(d) <= tol, f"{col}: max |diff| = {np.nanmax(d)} > {tol}"
    assert (rows["exposure_s"] > 0).all()
    assert (rows["parse_ok"]).all()


def test_sequence_segmentation_matches_paper_groups():
    df = _frames().set_index("product_id")
    fx = _fixture()
    rows = df.loc[fx["PRODUCT_ID"].tolist()].copy()
    rows["group"] = fx["group"].to_numpy()
    # the 12 frames of each paper group (n01..n04) share one seq_id, and groups differ
    per_group = rows.groupby("group")["seq_id"].nunique()
    assert (per_group == 1).all(), per_group.to_dict()
    assert rows.groupby("group")["seq_id"].first().nunique() == 4
    # archive's own sequence bookkeeping is carried through
    n01a = rows.loc["JIR_IMG_RDR_2017033T114006_V02"]
    assert n01a["sequence_number"] == 2 and n01a["sequence_samples"] == 13
    assert (rows["seq_n"] >= 12).all()
    # consecutive frames within a group are one spin apart (30.5 s), never more than 45 s
    gaps = rows["seq_gap_s"].dropna()
    assert gaps.between(29.0, 32.0).all(), gaps.describe().to_dict()


def test_orbit04_imager_row_count_matches_archive_listing():
    df = _frames()
    n = int(((df["orbit_dir"] == 4) & (df["family"] == "IMG")).sum())
    assert n == 1111, f"orbit04 IMG rows = {n}, archive lists 1111"
    assert df["product_id"].is_unique
