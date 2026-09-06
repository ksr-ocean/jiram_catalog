"""Gate for cumulative sweep stacks (READ-ONLY)."""
from __future__ import annotations

import os

import numpy as np
import pytest

from jiram_catalog.config import mirror_root


def _skip():
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")


def _eq(a, b):
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    return np.allclose(np.nan_to_num(a, nan=-1e30), np.nan_to_num(b, nan=-1e30), rtol=1e-5, atol=1e-6)


def test_cumulative_matches_frames_and_snapshots():
    _skip()
    import xarray as xr

    d = mirror_root() / "regions" / "north_pole_paper"
    fr = xr.open_dataset(d / "M_orbits4_frame.nc")
    cu = xr.open_dataset(d / "M_orbits4_cumulative.nc")
    sq = xr.open_dataset(d / "M_orbits4_sequence.nc")
    assert cu.attrs["level"] == "cumulative"
    assert cu.sizes["time"] == fr.sizes["time"] == 294
    seq = np.asarray(cu["seq_id"]).astype(str)
    si = np.asarray(cu["seq_index"]).astype(int)
    sn = np.asarray(cu["seq_n"]).astype(int)
    sseq = list(np.asarray(sq["seq_id"]).astype(str))
    checked = 0
    for s in np.unique(seq):
        idx = np.where(seq == s)[0]
        assert list(si[idx]) == list(range(len(idx))) and (sn[idx] == len(idx)).all()
        first, last = idx[0], idx[-1]
        assert _eq(cu["image"][first].values, fr["image"][first].values)
        k = sseq.index(s)
        assert _eq(cu["image"][last].values, sq["image"][k].values), s
        assert np.array_equal(np.asarray(cu["valid"][last].values), np.asarray(sq["valid"][k].values))
        assert _eq(cu["emission"][last].values, sq["emission"][k].values)
        checked += 1
        if checked >= 6:
            break


def test_api_lists_three_siblings_with_labels():
    _skip()
    from fastapi.testclient import TestClient

    from jiram_catalog.api.app import create_app

    with TestClient(create_app(mirror_root())) as c:
        items = c.get("/api/stacks").json()
    ids = {it["id"]: it for it in items}
    seq = ids["north_pole_paper/M_orbits4_sequence"]
    assert seq["label"] == "Region snapshots"
    sib = seq["siblings"]
    assert sib.get("frame") == "north_pole_paper/M_orbits4_frame"
    assert sib.get("cumulative") == "north_pole_paper/M_orbits4_cumulative"
    assert ids[sib["cumulative"]]["label"] == "Accumulating sweep"
    assert ids[sib["frame"]]["label"] == "Instrument frames"
