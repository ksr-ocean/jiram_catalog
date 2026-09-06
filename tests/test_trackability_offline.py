"""Offline (no mirror, no SPICE) tests for jiram_catalog.trackability."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from jiram_catalog import trackability as tb

T0 = pd.Timestamp("2020-01-01T00:00:00")


def _row(
    product_id: str,
    orbit_dir: int,
    half: str,
    seq_id: str,
    t_s: float,
    lat: float,
    lon: float,
    pixel_km: float = 10.0,
    geo_ok: bool = True,
    on_planet_frac: float = 1.0,
    bore_emission: float = 10.0,
    dayside_frac: float = 0.5,
) -> dict:
    return dict(
        product_id=product_id,
        orbit_dir=orbit_dir,
        half=half,
        seq_id=seq_id,
        start_time=T0 + pd.Timedelta(seconds=t_s),
        bore_lat=lat,
        bore_lon_east=lon,
        median_pixel_km=pixel_km,
        on_planet_frac=on_planet_frac,
        bore_emission=bore_emission,
        dayside_frac=dayside_frac,
        geo_ok=geo_ok,
    )


# ---------------------------------------------------------------------------
# Primary fixture: 10 frames, 3 sequences (seqA, seqB in orbit 1; seqC in
# orbit 2), all at the equator so separations reduce to a pure-longitude
# great-circle distance.
# ---------------------------------------------------------------------------
def _primary_frames() -> pd.DataFrame:
    rows = []
    # seqA: orbit 1, half M, 4 frames 100 s apart.
    for i, t in enumerate([0, 100, 200, 300]):
        rows.append(_row(f"A{i + 1}", 1, "M", "seqA", t, 0.0, 0.0010 * i))
    # seqB: orbit 1, half M, 3 frames 100 s apart, starting 500 s after seqA.
    # Longitudes are offset so each A frame's *nearest* B frame is the one
    # at (roughly) the same raster position -- A1->B1, A2->B2, A3->B3 -- the
    # way corresponding raster positions across repeat mosaic sequences are
    # nearest each other in the real archive (see the trackability.py
    # module docstring's nearest-partner-per-sequence judgment call).
    for i, t in enumerate([500, 600, 700]):
        rows.append(_row(f"B{i + 1}", 1, "M", "seqB", t, 0.0, 0.0004 + 0.0010 * i))
    # seqC: orbit 2, half M -- same absolute times as seqB, same position as
    # seqA, but a different orbit_dir, so it must never pair with A or B.
    for i, t in enumerate([550, 650, 750]):
        rows.append(_row(f"C{i + 1}", 2, "M", "seqC", t, 0.0, 0.0001 * i))
    return pd.DataFrame(rows)


@pytest.fixture
def primary_pairs() -> pd.DataFrame:
    return tb.build_pairs(_primary_frames())


def test_different_seq_required(primary_pairs):
    # No candidate pair links two frames of the same sequence.
    assert not (primary_pairs["seq_id_a"] == primary_pairs["seq_id_b"]).any()


def test_same_orbit_half_required(primary_pairs):
    # seqC (orbit 2) never appears, despite being time- and space-coincident
    # with seqA/seqB (orbit 1): pairing is strictly within (orbit_dir, half).
    ids = set(primary_pairs["product_id_a"]) | set(primary_pairs["product_id_b"])
    assert not any(pid.startswith("C") for pid in ids)


def test_ab_pair_count_and_dpx_arithmetic(primary_pairs):
    # Nearest-partner-per-sequence reduction: each of A1..A4 keeps only its
    # single closest seqB frame, giving 4 pairs, not the raw 4 x 3 = 12
    # combinations (all of which pass the window/separation tests on their
    # own). By construction A1->B1, A2->B2, A3->B3 (nearest match at the
    # corresponding raster position) and A4->B3 (nearest available, since
    # there is no B4).
    assert len(primary_pairs) == 4
    pairs_by_a = primary_pairs.set_index("product_id_a")["product_id_b"]
    assert pairs_by_a.to_dict() == {"A1": "B1", "A2": "B2", "A3": "B3", "A4": "B3"}

    row = primary_pairs.loc[
        (primary_pairs["product_id_a"] == "A1") & (primary_pairs["product_id_b"] == "B1")
    ].iloc[0]
    assert row["dt_s"] == pytest.approx(500.0)
    assert row["pixel_km"] == pytest.approx(10.0)
    assert row["d_px_10"] == pytest.approx(10.0 * 500.0 / (1000.0 * 10.0))
    assert row["d_px_30"] == pytest.approx(1.5)
    assert row["d_px_100"] == pytest.approx(5.0)


def test_best_dt_s_and_trackable_flags(primary_pairs):
    frames = tb.add_per_frame_stats(_primary_frames(), primary_pairs)
    by_id = frames.set_index("product_id")

    # Each of A1..A3 has exactly one (deduped) partner, at dt = 500 s.
    assert by_id.loc["A1", "best_dt_s"] == pytest.approx(500.0)
    assert by_id.loc["A2", "best_dt_s"] == pytest.approx(500.0)
    # A4's only (deduped) partner is B3, at dt = 400 s.
    assert by_id.loc["A4", "best_dt_s"] == pytest.approx(400.0)
    # B3 has two partners after dedup (A3 at dt=500, A4 at dt=400); both are
    # trackable at 30 m/s (d_px_30 = 1.5 and 1.2), so best_dt_s is the
    # smaller of the two, 400 s.
    assert by_id.loc["B3", "best_dt_s"] == pytest.approx(400.0)

    # A4-B3 (dt=400, pixel=10): d_px_10 = 0.4, below the 0.5 floor.
    assert not bool(by_id.loc["A4", "trackable_10"])
    # ... but d_px_30 = 1.2 and d_px_100 = 4.0 both clear it.
    assert bool(by_id.loc["A4", "trackable_30"])
    assert bool(by_id.loc["A4", "trackable_100"])

    # seqC has no cross-sequence, same-orbit partner at all.
    assert not by_id.loc["C1", "has_partner"]
    assert by_id.loc["C1", "n_partners"] == 0
    assert pd.isna(by_id.loc["C1", "best_dt_s"])


def test_sequence_pair_aggregation(primary_pairs):
    seqs = tb.build_sequence_pairs(_primary_frames(), primary_pairs)
    assert len(seqs) == 1  # only A-B qualifies (seqC never pairs with anything)
    row = seqs.iloc[0]
    assert row["seq_id_a"] == "seqA"  # earlier first frame (t=0 vs t=500)
    assert row["seq_id_b"] == "seqB"
    assert row["n_pairs"] == 4
    assert row["dt_start_s"] == pytest.approx(500.0)
    assert row["frac_a"] == pytest.approx(1.0)  # all 4 A frames have a B partner
    # d_px_30 values: A1-B1=1.5, A2-B2=1.5, A3-B3=1.5, A4-B3=1.2; median 1.5.
    assert row["median_d_px_30"] == pytest.approx(1.5)


def test_nearest_partner_per_sequence_reduction():
    # A source frame with three same-sequence candidates within window and
    # threshold keeps only the closest one.
    frames = pd.DataFrame(
        [
            _row("X1", 1, "M", "seqX", 0, 0.0, 0.0),
            _row("Ynear", 1, "M", "seqY", 200, 0.0, 0.0001),
            _row("Ymid", 1, "M", "seqY", 300, 0.0, 0.0005),
            _row("Yfar", 1, "M", "seqY", 400, 0.0, 0.0020),
        ]
    )
    pairs = tb.build_pairs(frames)
    assert len(pairs) == 1
    assert pairs.iloc[0]["product_id_b"] == "Ynear"


def test_best_dt_s_is_smallest_trackable_dt_not_nearest_in_time():
    # best_dt_s (REVISED 2026-09-05) is the smallest dt among partners that
    # are trackable at U=30, not simply the nearest-in-time partner: a
    # closer-in-time but untrackable (d_px_30 < 0.5) partner must be
    # skipped in favour of a farther, trackable one.
    frames = pd.DataFrame(
        [
            _row("X1", 1, "M", "seqX", 0, 0.0, 0.0, pixel_km=10.0),
            # d_px_30 = 30*90/(1000*10) = 0.27, below the 0.5 floor.
            _row("Ynear", 1, "M", "seqY", 90, 0.0, 0.0001, pixel_km=10.0),
            # d_px_30 = 30*1000/(1000*10) = 3.0, trackable.
            _row("Zfar", 1, "M", "seqZ", 1000, 0.0, 0.0001, pixel_km=10.0),
        ]
    )
    pairs = tb.build_pairs(frames)
    frame_stats = tb.add_per_frame_stats(frames, pairs)
    by_id = frame_stats.set_index("product_id")
    assert by_id.loc["X1", "n_partners"] == 2
    assert bool(by_id.loc["X1", "trackable_30"])  # Zfar qualifies
    assert by_id.loc["X1", "best_dt_s"] == pytest.approx(1000.0)


def test_best_dt_s_nan_when_no_partner_trackable_at_30():
    # A frame whose only partner is untrackable at U=30 gets NaN, not that
    # partner's dt, even though has_partner is True.
    frames = pd.DataFrame(
        [
            _row("X1", 1, "M", "seqX", 0, 0.0, 0.0, pixel_km=10.0),
            # d_px_30 = 30*90/(1000*10) = 0.27, below the 0.5 floor.
            _row("Ynear", 1, "M", "seqY", 90, 0.0, 0.0, pixel_km=10.0),
        ]
    )
    pairs = tb.build_pairs(frames)
    frame_stats = tb.add_per_frame_stats(frames, pairs)
    by_id = frame_stats.set_index("product_id")
    assert bool(by_id.loc["X1", "has_partner"])
    assert not bool(by_id.loc["X1", "trackable_30"])
    assert pd.isna(by_id.loc["X1", "best_dt_s"])


def test_sequence_pair_below_minimum_is_dropped():
    # Two sequences with only 2 candidate pairs must not appear.
    frames = pd.DataFrame(
        [
            _row("X1", 1, "M", "seqX", 0, 0.0, 0.0),
            _row("X2", 1, "M", "seqX", 100, 0.0, 0.0),
            _row("Y1", 1, "M", "seqY", 300, 0.0, 0.0),
            _row("Y2", 1, "M", "seqY", 25000, 0.0, 0.0),  # too far from X1/X2 (> 6 h)
        ]
    )
    pairs = tb.build_pairs(frames)
    seqs = tb.build_sequence_pairs(frames, pairs)
    assert len(pairs) == 2  # X1-Y1 and X2-Y1 only
    assert seqs.empty


# ---------------------------------------------------------------------------
# dt-window edges: 90 s and 6 h are inclusive; 89 s and 6 h + 1 s are not.
# ---------------------------------------------------------------------------
def test_dt_window_edges_inclusive():
    frames = pd.DataFrame(
        [
            _row("P0", 1, "M", "s0", 0, 0.0, 0.0),
            _row("P89", 1, "M", "s89", 89, 0.0, 0.0),
            _row("P90", 1, "M", "s90", 90, 0.0, 0.0),
            _row("P21600", 1, "M", "s21600", 21600, 0.0, 0.0),
            _row("P21601", 1, "M", "s21601", 21601, 0.0, 0.0),
        ]
    )
    pairs = tb.build_pairs(frames)

    def has(a: str, b: str) -> bool:
        return (
            (pairs["product_id_a"] == a) & (pairs["product_id_b"] == b)
        ).any()

    assert not has("P0", "P89")  # dt = 89 s, below the 90 s floor
    assert has("P0", "P90")  # dt = 90 s, inclusive
    assert has("P0", "P21600")  # dt = 21600 s = 6 h, inclusive
    assert not has("P0", "P21601")  # dt = 21601 s, above the 6 h ceiling


# ---------------------------------------------------------------------------
# separation threshold edge: exactly at threshold passes, just past fails.
# ---------------------------------------------------------------------------
def test_separation_threshold_edge():
    pixel_km = 10.0
    threshold_km = tb.SEPARATION_FACTOR * pixel_km
    # Pure along-equator separation: distance = R * radians(dlon).
    dlon_at_threshold = np.degrees(threshold_km / tb.PLANET_RADIUS_KM)
    eps = 1e-6

    frames = pd.DataFrame(
        [
            _row("O1", 1, "M", "seqO", 0, 0.0, 0.0, pixel_km=pixel_km),
            _row("Oin", 1, "M", "seqIn", 200, 0.0, dlon_at_threshold - eps, pixel_km=pixel_km),
            _row("Oout", 1, "M", "seqOut", 400, 0.0, dlon_at_threshold + 5 * eps, pixel_km=pixel_km),
        ]
    )
    pairs = tb.build_pairs(frames)
    partners_of_o1 = set(pairs.loc[pairs["product_id_a"] == "O1", "product_id_b"])
    assert "Oin" in partners_of_o1
    assert "Oout" not in partners_of_o1


# ---------------------------------------------------------------------------
# select_unit_of_analysis: geo_ok / on_planet_frac / bore_emission filter.
# ---------------------------------------------------------------------------
def test_select_unit_of_analysis_filter():
    frames = pd.DataFrame(
        [
            _row("keep", 1, "M", "s1", 0, 10.0, 0.0, geo_ok=True, on_planet_frac=0.5, bore_emission=60.0),
            _row("bad_geo", 1, "M", "s2", 0, 10.0, 0.0, geo_ok=False, on_planet_frac=0.9, bore_emission=10.0),
            _row("low_on_planet", 1, "M", "s3", 0, 10.0, 0.0, geo_ok=True, on_planet_frac=0.2, bore_emission=10.0),
            _row("high_emission", 1, "M", "s4", 0, 10.0, 0.0, geo_ok=True, on_planet_frac=0.9, bore_emission=75.0),
            _row("edge_on_planet", 1, "M", "s5", 0, 10.0, 0.0, geo_ok=True, on_planet_frac=0.3, bore_emission=70.0),
        ]
    )
    out = tb.select_unit_of_analysis(frames)
    assert set(out["product_id"]) == {"keep", "edge_on_planet"}
    assert "lat_band" in out.columns


# ---------------------------------------------------------------------------
# Latitude band edges.
# ---------------------------------------------------------------------------
def test_lat_band_edges():
    lats = np.array([-90.0, -60.0, -59.9, -30.0, -10.0, 0.0, 10.0, 30.0, 60.0, 90.0, np.nan])
    labels = tb.lat_band(lats)
    expected = [
        "S polar",  # -90 -> [-90,-60)
        "S mid",  # -60 -> [-60,-30), boundary belongs to the upper band
        "S mid",  # -59.9 > -60, so it is in [-60,-30) too
        "S low",  # -30 -> [-30,-10)
        "equator",  # -10 -> [-10,10)
        "equator",
        "N low",  # 10 -> [10,30)
        "N mid",  # 30 -> [30,60)
        "N polar",  # 60 -> [60,90], closed band
        "N polar",  # 90, included in the closed top band
        None,  # NaN passes through
    ]
    assert list(labels) == expected
