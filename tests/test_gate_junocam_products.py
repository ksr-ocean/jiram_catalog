"""Gate for JunoCam products through the catalog, stacks, strips, and API (READ-ONLY)."""
from __future__ import annotations

import io
import os

import numpy as np
import pandas as pd
import pyarrow.ipc as ipc
import pytest

from jiram_catalog.config import mirror_root


def _root():
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    return mirror_root()


def test_junocam_geo_rows():
    g = pd.read_parquet(_root() / "junocam" / "index" / "junocam_geo.parquet")
    g4 = g[(g["orbit"] == 4) & g["geo_ok"]]
    assert len(g4) >= 100, len(g4)
    for c in ["bore_lat", "bore_lon_east", "min_lat", "max_lat", "lon_span_deg", "median_pixel_km", "on_planet_frac"]:
        assert np.isfinite(g4[c].to_numpy(dtype=float)).all(), c
    assert (g4["fp_lon"].apply(len) >= 8).all()


def test_junocam_rgb_stack_and_strips():
    import xarray as xr

    from jiram_catalog.stats2d import strip_statistics
    from jiram_catalog.strips import load_strips, read_strip

    root = _root()
    files = sorted((root / "regions" / "north_pole_paper").glob("junocam_*orbits4*frame.nc"))
    assert files, "no JunoCam stack for the paper region"
    ds = xr.open_dataset(files[0])
    assert ds["image"].dims == ("time", "band", "y", "x") and ds.sizes["time"] >= 15
    assert list(ds["band"].values) == ["RED", "GREEN", "BLUE"] and ds.attrs["instrument"] == "JunoCam"
    assert np.isfinite(np.asarray(ds["image"].isel(time=0, band=0).values)).any()
    dt = np.asarray(ds["dt_refined_s"].values, dtype=float)
    assert np.isfinite(dt).mean() >= 0.5
    idx = load_strips(root, orbits=[4], instrument="JunoCam")
    assert len(idx) >= 50 and (idx["bands"] == "RED;GREEN;BLUE").all()
    sd = read_strip(root, idx.iloc[0]["strip_id"])
    assert sd["image"].dims == ("band", "y", "x")
    st = strip_statistics(sd, band="RED")
    assert "k" in st.coords


def test_api_serves_instruments_bands_footprints():
    from fastapi.testclient import TestClient

    from jiram_catalog.api.app import create_app

    root = _root()
    with TestClient(create_app(root)) as c:
        cfg = c.get("/api/config").json()
        assert cfg["counts"]["junocam_images"] >= 100
        t = ipc.open_stream(io.BytesIO(c.get("/api/catalog/frames.arrow").content)).read_all().to_pandas()
        jc = t[t["instrument"] == "JunoCam"]
        assert len(jc) >= 100 and (jc["quality_tier"].isin(["A", "B", "C"])).all()
        assert (jc["fp_lon"].apply(len) >= 8).all()
        blists = jc["bands"].apply(lambda b: list(b) if not isinstance(b, str) else b.split(";"))
        assert (blists.apply(len) >= 1).all()
        assert blists.apply(lambda b: "RED" in b).mean() >= 0.5  # methane-only products also exist
        assert (t["instrument"] == "JIRAM").sum() >= 40_000
        s = c.get("/api/catalog/summary", params={"instrument": "JunoCam"}).json()
        assert s["n"] >= 100
        stacks = c.get("/api/stacks").json()
        st = next(x for x in stacks if x["instrument"] == "JunoCam" and x["region"] == "north_pole_paper")
        assert set(st["bands"]) >= {"RED", "GREEN", "BLUE"}
        assert c.get(f"/api/stacks/{st['id']}/frame/0.png", params={"band": "RED"}).status_code == 200
        r = c.get(f"/api/stacks/{st['id']}/frame/0/rgb.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
        strips = ipc.open_stream(io.BytesIO(c.get("/api/strips.arrow").content)).read_all().to_pandas()
        sid = strips[strips["instrument"] == "JunoCam"].iloc[0]["strip_id"]
        stats = c.get(f"/api/strips/{sid}/stats", params={"band": "GREEN"}).json()
        assert len(stats["k"]) > 5
