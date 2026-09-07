"""Gate for JunoCam photometry and display fixes (READ-ONLY)."""
from __future__ import annotations

import glob
import io
import os

import numpy as np
import pytest

from jiram_catalog.config import mirror_root


def _root():
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    return mirror_root()


def _lowpass_var_frac(a: np.ndarray, valid: np.ndarray, sigma: int = 64) -> float:
    x = np.where(valid, a, np.nan)
    m = np.nanmean(x)
    f = np.where(valid, x - m, 0.0)
    w = valid.astype(float)
    k = np.exp(-0.5 * (np.arange(-3 * sigma, 3 * sigma + 1) / sigma) ** 2)
    k /= k.sum()

    def blur(z):
        z = np.apply_along_axis(lambda r: np.convolve(r, k, mode="same"), 1, z)
        return np.apply_along_axis(lambda c: np.convolve(c, k, mode="same"), 0, z)

    low = blur(f) / np.maximum(blur(w), 1e-6)
    # Relative large-scale variance: normalise by the mean squared, not the
    # total variance, because a multiplicative illumination correction removes
    # variance at every scale and leaves the variance *fraction* nearly unchanged.
    return float(np.nanvar(low[valid]) / (m * m)) if m > 0 else 0.0


def test_stack_night_masked_and_stretch_present():
    import xarray as xr

    from fastapi.testclient import TestClient

    from jiram_catalog.api.app import create_app

    root = _root()
    f = sorted(glob.glob(str(root / "regions" / "north_pole_paper" / "junocam_*orbits4*frame.nc")))[0]
    ds = xr.open_dataset(f)
    assert "incidence" in ds and ds.sizes["time"] >= 5
    for t in range(ds.sizes["time"]):
        v = np.asarray(ds["valid"][t].values)
        assert v.mean() < 0.6, (t, v.mean())
        inc = np.asarray(ds["incidence"][t].values)
        assert (np.nanmin(inc, axis=0)[v] < 88.0).all()
    with TestClient(create_app(root)) as c:
        stacks = c.get("/api/stacks").json()
        st = next(x for x in stacks if x["instrument"] == "JunoCam" and x["region"] == "north_pole_paper")
        meta = c.get(f"/api/stacks/{st['id']}/meta").json()
        assert meta["norm_default"] == "lambert"
        for norm in ("none", "lambert"):
            assert set(meta["stretch"][norm]) >= {"RED", "GREEN", "BLUE"}
        r = c.get(f"/api/stacks/{st['id']}/frame/0/rgb.png", params={"norm": "lambert"})
        assert r.status_code == 200
        import imageio.v3 as iio

        img = iio.imread(io.BytesIO(r.content))
        a = img[..., 3] > 0
        for ch in range(3):
            x = img[..., ch][a].astype(float)
            assert (x >= 255).mean() < 0.03, (ch, (x >= 255).mean())
            assert ((x >= 30) & (x <= 225)).mean() > 0.5, (ch, ((x >= 30) & (x <= 225)).mean())


def test_strips_scale_cutoff_and_normalisation_flattens():
    from fastapi.testclient import TestClient

    from jiram_catalog.api.app import create_app
    from jiram_catalog.strips import load_strips, read_strip

    root = _root()
    idx = load_strips(root, orbits=[4], instrument="JunoCam")
    assert len(idx) >= 3 and (idx["km_per_px"] <= 30.0).all(), idx["km_per_px"].describe()
    improved = 0
    for sid in idx["strip_id"].head(3):
        ds = read_strip(root, sid)
        red = list(ds["band"].values).index("RED")
        img = np.asarray(ds["image"][red].values)
        inc = np.asarray(ds["incidence"][red].values)
        v = np.asarray(ds["valid"].values) & np.isfinite(img) & np.isfinite(inc)
        raw = _lowpass_var_frac(img, v)
        lam = _lowpass_var_frac(img / np.maximum(np.cos(np.radians(inc)), 0.05), v)
        if lam <= 0.6 * raw:
            improved += 1
    assert improved >= 3, improved
    with TestClient(create_app(root)) as c:
        sid = idx.iloc[0]["strip_id"]
        s0 = c.get(f"/api/strips/{sid}/stats", params={"band": "RED", "norm": "none"}).json()
        s1 = c.get(f"/api/strips/{sid}/stats", params={"band": "RED", "norm": "lambert"}).json()
        assert not np.allclose(np.asarray(s0["E"], dtype=float), np.asarray(s1["E"], dtype=float))
