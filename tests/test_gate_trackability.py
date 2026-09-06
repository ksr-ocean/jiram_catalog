"""Gate for the trackability report (READ-ONLY)."""
from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from jiram_catalog.config import mirror_root

FIXTURE = Path(__file__).parent / "fixtures" / "pj4_ingersoll2022_map_labels.csv"
REPO = Path(__file__).resolve().parents[1]


def _mirror() -> Path:
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    return mirror_root()


def test_paper_frames_have_partners_at_expected_baselines():
    fr = pd.read_parquet(_mirror() / "index" / "trackability_frames.parquet").set_index("product_id")
    fx = pd.read_csv(FIXTURE)
    missing = [p for p in fx["PRODUCT_ID"] if p not in fr.index]
    assert not missing, missing[:5]
    rows = fr.loc[fx["PRODUCT_ID"].tolist()]
    assert rows["has_partner"].all()
    dt = rows["best_dt_s"].to_numpy(dtype=float)
    # smallest trackable baseline: one sequence interval (487 s nominal, ~65 s jitter), or up to three
    k = np.rint(dt / 487.0)
    ok = np.isfinite(dt) & (k >= 1) & (k <= 3) & (np.abs(dt - k * 487.0) <= 90)
    assert ok.all(), dt[~ok]


def test_pairs_between_first_and_third_paper_sequences():
    pairs = pd.read_parquet(_mirror() / "index" / "trackability_pairs.parquet")
    fx = pd.read_csv(FIXTURE)
    n01 = set(fx.loc[fx["file"].str.startswith("n01"), "PRODUCT_ID"])
    n03 = set(fx.loc[fx["file"].str.startswith("n03"), "PRODUCT_ID"])
    cols = [c for c in pairs.columns if "product_id" in c]
    assert len(cols) == 2, cols
    a, b = cols
    m = (pairs[a].isin(n01) & pairs[b].isin(n03)) | (pairs[a].isin(n03) & pairs[b].isin(n01))
    sub = pairs[m]
    assert len(sub) >= 6, len(sub)
    assert (np.abs(sub["dt_s"].to_numpy(dtype=float) - 974) <= 30).all()


def test_counts_consistent_and_figure_exists():
    fr = pd.read_parquet(_mirror() / "index" / "trackability_frames.parquet")
    g = fr.groupby("lat_band").agg(n=("product_id", "size"), p=("has_partner", "sum"), t=("trackable_30", "sum"))
    assert (g["t"] <= g["p"]).all() and (g["p"] <= g["n"]).all()
    assert (REPO / "docs" / "reports" / "figures" / "trackability_heatmap.png").stat().st_size > 0
    assert (REPO / "docs" / "reports" / "trackability.md").exists()
