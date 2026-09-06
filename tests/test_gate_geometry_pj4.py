"""Gate for the SPICE geometry engine (READ-ONLY for executors).

Requires the mirror: orbit04 index (frames.parquet) and the PJ4 kernel set under
<mirror>/spice. Set JIRAM_SKIP_GATES=1 to skip in offline runs.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from jiram_catalog.config import mirror_root

FIXTURE = Path(__file__).parent / "fixtures" / "pj4_ingersoll2022_map_labels.csv"
SAMPLE_PIXELS = [(1, 1), (1, 216), (1, 432), (64, 1), (64, 216), (64, 432), (128, 1), (128, 216), (128, 432)]


def _mirror() -> Path:
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    return mirror_root()


def gc_deg(lat1, lon1, lat2, lon2):
    """Great-circle separation in degrees (inputs in degrees)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dl = np.radians(lon1 - lon2)
    c = np.sin(p1) * np.sin(p2) + np.cos(p1) * np.cos(p2) * np.cos(dl)
    return np.degrees(np.arccos(np.clip(c, -1.0, 1.0)))


@pytest.fixture(scope="module")
def kernels():
    from jiram_catalog.geometry import KernelSet

    ks = KernelSet.for_orbits(_mirror(), [4])
    yield ks
    ks.unload()


def test_boresight_geometry_matches_48_paper_frames(kernels):
    from jiram_catalog.geometry import frame_geometry

    fx = pd.read_csv(FIXTURE)
    worst = {"gc": 0.0, "em": 0.0, "inc": 0.0, "alt": 0.0, "minlat": 0.0}
    for _, r in fx.iterrows():
        g = frame_geometry(r["IMAGE_TIME"], "M", kernels)
        b = g.boresight
        worst["gc"] = max(worst["gc"], gc_deg(b["lat"], b["lon_east"], r["CENLAT"], r["CENLON"]))
        worst["em"] = max(worst["em"], abs(b["emission"] - r["EM_ANGLE"]))
        worst["inc"] = max(worst["inc"], abs(b["incidence"] - r["IN_ANGLE"]))
        worst["alt"] = max(worst["alt"], abs(g.sc_altitude_km - r["SPACECRAFT_ALTITUDE"]))
        worst["minlat"] = max(worst["minlat"], abs(np.nanmin(g.lat) - r["MINLAT"]))
        assert g.on_planet.all(), f"{r['file']}: paper frames are fully on-planet"
        assert np.isfinite(g.lat).all() and np.isfinite(g.emission).all()
        assert 0 <= np.nanmin(g.lon_east) and np.nanmax(g.lon_east) < 360
    assert worst["gc"] <= 0.02, worst
    assert worst["em"] <= 0.02, worst
    assert worst["inc"] <= 0.02, worst
    assert worst["alt"] <= 50.0, worst
    assert worst["minlat"] <= 0.03, worst


def _oracle(g, d):
    import spiceypy as sp

    spoint, trgepc, srfvec = sp.sincpt("ELLIPSOID", "JUPITER", g.et, "IAU_JUPITER", "LT+S", "JUNO", g.frame, d)
    _, lon, lat = sp.reclat(spoint)
    _, _, phase, inc, em, *_ = sp.illumf("ELLIPSOID", "JUPITER", "SUN", g.et, "IAU_JUPITER", "LT+S", "JUNO", spoint)
    return (
        np.degrees(lat), np.degrees(lon) % 360.0, float(np.linalg.norm(srfvec)),
        np.degrees(em), np.degrees(inc), np.degrees(phase),
    )


@pytest.mark.parametrize("epoch", ["2017-02-02T11:40:03.870", "2017-02-02T11:42:05.928", "2017-02-02T11:45:39.521"])
def test_vectorised_grid_matches_spice_oracle_at_sample_pixels(kernels, epoch):
    import spiceypy as sp

    from jiram_catalog.geometry import frame_geometry, pixel_directions

    g = frame_geometry(epoch, "M", kernels)
    dirs = pixel_directions(128, 432, g.ifov_rad)
    for line, sample in SAMPLE_PIXELS:
        d = dirs[line - 1, sample - 1]
        assert abs(np.linalg.norm(d) - 1.0) < 1e-12
        try:
            lat, lon, rng, em, inc, ph = _oracle(g, d)
        except sp.utils.exceptions.NotFoundError:
            assert not g.on_planet[line - 1, sample - 1]
            continue
        i, j = line - 1, sample - 1
        assert g.on_planet[i, j]
        assert gc_deg(g.lat[i, j], g.lon_east[i, j], lat, lon) <= 0.002, (line, sample)
        assert abs(g.range_km[i, j] - rng) <= 0.5, (line, sample)
        assert abs(g.emission[i, j] - em) <= 0.01, (line, sample)
        assert abs(g.incidence[i, j] - inc) <= 0.01, (line, sample)
        assert abs(g.phase[i, j] - ph) <= 0.01, (line, sample)


def test_partial_planet_frame_has_nan_off_planet(kernels):
    from jiram_catalog.geometry import frame_geometry

    df = pd.read_parquet(_mirror() / "index" / "frames.parquet")
    cand = df[(df["orbit_dir"] == 4) & df["target_pixels"].between(5000, 50000)].sort_values("start_time")
    assert len(cand) > 0
    r = cand.iloc[0]
    g = frame_geometry(str(r["start_time"]), r["band"], kernels)
    n_on = int(g.on_planet.sum())
    assert 0 < n_on < 128 * 432, n_on
    assert np.isnan(g.lat[~g.on_planet]).all() and np.isnan(g.emission[~g.on_planet]).all()
    assert np.isfinite(g.lat[g.on_planet]).all()
    # planet-pixel count from the label is a coarse check on the same quantity
    assert abs(n_on - r["target_pixels"]) / r["target_pixels"] < 0.15, (n_on, r["target_pixels"])


def test_full_frame_geometry_is_fast(kernels):
    from jiram_catalog.geometry import frame_geometry

    best = 1e9
    for _ in range(3):
        t0 = time.perf_counter()
        frame_geometry("2017-02-02T11:40:03.870", "M", kernels)
        best = min(best, time.perf_counter() - t0)
    assert best < 1.0, f"frame_geometry took {best:.2f} s"
