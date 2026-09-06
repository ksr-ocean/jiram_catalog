"""Gate for region stacks, composites, movie, and goflow export (READ-ONLY)."""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from jiram_catalog.config import mirror_root, paper_data_root

FIXTURE = Path(__file__).parent / "fixtures" / "pj4_ingersoll2022_map_labels.csv"
PAPER = paper_data_root()


def _region_dir() -> Path:
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    return mirror_root() / "regions" / "north_pole_paper"


def test_frame_stack_contains_paper_frames_and_matches_n01a():
    import xarray as xr

    from jiram_catalog.vicar import read_vicar

    ds = xr.open_dataset(_region_dir() / "M_orbits4_frame.nc")
    fx = pd.read_csv(FIXTURE)
    pids = set(np.asarray(ds["product_id"]).astype(str))
    missing = [p for p in fx["PRODUCT_ID"] if p not in pids]
    assert not missing, missing[:5]
    assert ds.attrs["level"] == "frame" and ds.attrs["band"] == "M"
    assert set(ds["image"].dims) == {"time", "y", "x"}
    paper, _ = read_vicar(PAPER / "n01_15km" / "n01a.map")
    i = int(np.where(np.asarray(ds["product_id"]).astype(str) == "JIR_IMG_RDR_2017033T114006_V02")[0][0])
    img = np.asarray(ds["image"][i].values)
    r0, c0 = int(ds.attrs.get("row0", 0)), int(ds.attrs.get("col0", 0))
    sub = paper[r0 : r0 + img.shape[0], c0 : c0 + img.shape[1]]
    both = np.isfinite(img) & (sub != 0)
    assert both.sum() > 150_000, both.sum()
    ncc = np.corrcoef(img[both], sub[both])[0, 1]
    assert ncc >= 0.98, ncc
    lat = np.asarray(ds["lat"].values)
    assert np.nanmax(lat) > 89.0


def test_sequence_stack_has_four_paper_sequences():
    import xarray as xr

    fr = xr.open_dataset(_region_dir() / "M_orbits4_frame.nc")
    seq = xr.open_dataset(_region_dir() / "M_orbits4_sequence.nc")
    assert seq.attrs["level"] == "sequence"
    fx = pd.read_csv(FIXTURE)
    fpid = np.asarray(fr["product_id"]).astype(str)
    fseq = np.asarray(fr["seq_id"]).astype(str)
    paper_seqs = sorted({fseq[list(fpid).index(p)] for p in fx["PRODUCT_ID"]})
    assert len(paper_seqs) == 4
    sseq = list(np.asarray(seq["seq_id"]).astype(str))
    for s in paper_seqs:
        assert s in sseq, s
        k = sseq.index(s)
        assert int(seq["n_frames"][k]) == 12
        n_valid_seq = int(np.asarray(seq["valid"][k]).sum())
        members = np.where(fseq == s)[0]
        n_valid_frames = max(int(np.asarray(fr["valid"][j]).sum()) for j in members)
        assert n_valid_seq > n_valid_frames


def test_goflow_export_realizations():
    import xarray as xr

    root = _region_dir() / "goflow_M_orbits4"
    assert (root / "dataset_manifest.json").exists() and (root / "spec.json").exists()
    man = json.loads((root / "dataset_manifest.json").read_text())
    reals = [r for r in root.iterdir() if r.is_dir() and r.name.startswith("r")]
    assert reals and man
    ok = False
    for r in reals:
        ds = xr.open_dataset(r / "realization.nc")
        assert ds["image"].dims == ("frame", "y_img", "x_img")
        assert ds["valid"].dtype == bool
        assert float(ds.attrs["dx_img_m"]) == 15000.0
        dt = float(ds.attrs["dt_img_s"])
        v = np.asarray(ds["valid"].values)
        lg = np.asarray(ds["loggrad"].values)
        assert np.isfinite(lg[v]).all()
        assert (np.asarray(ds["image"].values)[~v] == 0).all()
        if ds.sizes["frame"] >= 3 and abs(dt - 487.0) / 487.0 <= 0.05:
            ok = True
        assert (r / "manifest.json").exists()
    assert ok, "no realization with >=3 frames at ~487 s cadence"


def test_movie_has_one_frame_per_time_step():
    import imageio.v3 as iio
    import xarray as xr

    seq = xr.open_dataset(_region_dir() / "M_orbits4_sequence.nc")
    mp4 = _region_dir() / "M_orbits4_sequence.mp4"
    assert mp4.exists() and mp4.stat().st_size > 0
    n = sum(1 for _ in iio.imiter(mp4))
    assert n == seq.sizes["time"], (n, seq.sizes["time"])
