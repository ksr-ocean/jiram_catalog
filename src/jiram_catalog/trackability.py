"""Trackability of JIRAM passes: repeat-view geometry and cloud-displacement
resolvability at the frame pixel scale.

Pure numpy/pandas -- no SPICE, no I/O. Callers pass in a ``frames_with_geo``
-like DataFrame (see :func:`jiram_catalog.geo.frames_with_geo`) and get back
the per-frame and per-pair tables the report is built from.

A "candidate pair" is an overlap *proxy*: two frames of the same orbit and
band half, taken 90 s to 6 h apart, whose boresights fall within a
generously wide separation threshold set by the smaller of their two pixel
scales (``0.5 * 432 * min(pixel_km)``, the spec's literal constant -- 432 is
JIRAM's across-track sample count). It is not a footprint intersection -- a
frame that scans quickly across many longitudes near a pole can have a
boresight far from another frame's while their footprints still overlap,
and conversely a boresight match does not guarantee the *same ground
feature* is visible in both.

JUDGMENT CALL (nearest-partner-per-sequence reduction): applied to the real
mirror, that threshold alone is wide enough that, in PJ4's tightly packed
polar mosaics, one frame's boresight falls within range of *several*
neighbouring raster positions in the next repeat sequence, not only its
true, same-position repeat -- because at these polar latitudes the
boresight-separation ranges of "same raster position, next sequence" and
"adjacent raster position, next sequence" overlap (verified numerically
against the paper's own PJ4 fixture: the closest wrong-position pair is
nearer than the farthest correct-position pair, so no fixed threshold can
cleanly separate the two cases while still keeping every correct-position
pair). :func:`build_pairs` therefore keeps, for each frame and each other
sequence within the window, only the single closest match -- its true
nearest repeat view in that sequence -- rather than every raster position
within the generous proxy radius. This is an addition to the spec's literal
"threshold only" wording, not a substitute for it (the threshold still
gates whether a sequence contributes a partner at all); it is what makes
`best_dt_s` land on the correct repeat interval instead of an adjacent
raster step, and is flagged in the report as a judgment call. Every count
should still be read as "at least this many candidate views", not a
verified re-detection count -- the reduction removes double-counting of one
real repeat view, it does not verify the view is of the same cloud feature.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

__all__ = [
    "FRAME_COLUMNS",
    "LAT_BAND_EDGES",
    "LAT_BAND_NAMES",
    "PLANET_RADIUS_KM",
    "DT_MIN_S",
    "DT_MAX_S",
    "SEPARATION_FACTOR",
    "REFERENCE_SPEEDS_MS",
    "DPX_MIN",
    "DPX_MAX",
    "great_circle_km",
    "lat_band",
    "select_unit_of_analysis",
    "build_pairs",
    "add_per_frame_stats",
    "build_sequence_pairs",
]

#: Jupiter's radius used for the boresight separation proxy, km.
PLANET_RADIUS_KM = 69_911.0
#: Candidate-pair time window, seconds.
DT_MIN_S = 90.0
DT_MAX_S = 6.0 * 3600.0
#: Separation threshold factor: threshold_km = SEPARATION_FACTOR * min(pixel_km).
#: This is the spec's literal constant (432 = JIRAM's across-track sample
#: count). See the nearest-partner-per-sequence reduction below and the
#: JUDGMENT CALL note in the module docstring for why the threshold alone
#: is not enough to identify "the" repeat view of a given other sequence.
SEPARATION_FACTOR = 0.5 * 432.0
#: Reference cloud speeds the report quantifies trackability at, m/s.
REFERENCE_SPEEDS_MS = (10.0, 30.0, 100.0)
#: A pair is "trackable" at a given speed when its expected displacement, in
#: pixels, falls in this range: enough to measure, not so much it aliases.
DPX_MIN = 0.5
DPX_MAX = 40.0

#: Latitude band edges, degrees. Bands are half-open [lo, hi) except the
#: last, which is closed at +90.
LAT_BAND_EDGES: tuple[float, ...] = (-90.0, -60.0, -30.0, -10.0, 10.0, 30.0, 60.0, 90.0)
LAT_BAND_NAMES: tuple[str, ...] = (
    "S polar",
    "S mid",
    "S low",
    "equator",
    "N low",
    "N mid",
    "N polar",
)

#: Columns carried from frames_with_geo into the per-frame output, in order.
FRAME_COLUMNS: tuple[str, ...] = (
    "product_id",
    "orbit_dir",
    "half",
    "seq_id",
    "start_time",
    "bore_lat",
    "bore_lon_east",
    "median_pixel_km",
    "on_planet_frac",
    "bore_emission",
    "dayside_frac",
)


def great_circle_km(
    lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray
) -> np.ndarray:
    """Great-circle separation in km on a sphere of radius PLANET_RADIUS_KM."""
    lat1r = np.radians(np.asarray(lat1, dtype=np.float64))
    lat2r = np.radians(np.asarray(lat2, dtype=np.float64))
    dlon = np.radians(np.asarray(lon1, dtype=np.float64) - np.asarray(lon2, dtype=np.float64))
    cosine = np.sin(lat1r) * np.sin(lat2r) + np.cos(lat1r) * np.cos(lat2r) * np.cos(dlon)
    return PLANET_RADIUS_KM * np.arccos(np.clip(cosine, -1.0, 1.0))


def lat_band(lat: np.ndarray) -> np.ndarray:
    """Latitude band name per value of ``lat``; ``None`` where ``lat`` is NaN.

    Bands follow LAT_BAND_NAMES/LAT_BAND_EDGES: half-open [lo, hi), except
    the last band (N polar) which also includes +90.
    """
    values = np.asarray(lat, dtype=np.float64)
    interior_edges = np.asarray(LAT_BAND_EDGES[1:-1], dtype=np.float64)
    idx = np.digitize(values, interior_edges, right=False)
    names = np.asarray(LAT_BAND_NAMES, dtype=object)
    labels = names[np.clip(idx, 0, len(LAT_BAND_NAMES) - 1)]
    return np.where(np.isnan(values), None, labels)


def select_unit_of_analysis(frames: pd.DataFrame) -> pd.DataFrame:
    """Rows of ``frames_with_geo`` usable for trackability, plus lat_band.

    Filter: geo_ok, on_planet_frac >= 0.3, bore_emission <= 70.
    """
    mask = (
        frames["geo_ok"].fillna(False).to_numpy(dtype=bool)
        & (frames["on_planet_frac"].to_numpy(dtype=np.float64) >= 0.3)
        & (frames["bore_emission"].to_numpy(dtype=np.float64) <= 70.0)
    )
    out = frames.loc[mask, list(FRAME_COLUMNS)].reset_index(drop=True).copy()
    out["lat_band"] = lat_band(out["bore_lat"].to_numpy())
    return out


def _d_px(dt_s: np.ndarray, pixel_km: np.ndarray, speed_ms: float) -> np.ndarray:
    return speed_ms * dt_s / (1000.0 * pixel_km)


def _group_pairs(group: pd.DataFrame) -> pd.DataFrame | None:
    """Candidate pairs within one (orbit_dir, half) group, time-sorted.

    Uses a sliding window (searchsorted on sorted times) instead of an
    all-pairs product: for each frame i we only ever look at the slice of
    later frames whose dt falls in [DT_MIN_S, DT_MAX_S], then filter that
    slice by seq_id and separation.
    """
    g = group.sort_values("start_time", kind="stable").reset_index(drop=True)
    n = len(g)
    if n < 2:
        return None
    times = g["start_time"].to_numpy("datetime64[ns]").astype(np.int64) // 1_000_000_000
    lat = g["bore_lat"].to_numpy(np.float64)
    lon = g["bore_lon_east"].to_numpy(np.float64)
    pix = g["median_pixel_km"].to_numpy(np.float64)
    seq = g["seq_id"].to_numpy()
    pid = g["product_id"].to_numpy()

    chunks: list[pd.DataFrame] = []
    for i in range(n - 1):
        lo = max(i + 1, int(np.searchsorted(times, times[i] + DT_MIN_S, side="left")))
        hi = int(np.searchsorted(times, times[i] + DT_MAX_S, side="right"))
        if lo >= hi:
            continue
        js = np.arange(lo, hi)
        keep_seq = seq[js] != seq[i]
        if not keep_seq.any():
            continue
        js = js[keep_seq]
        dt = (times[js] - times[i]).astype(np.float64)
        sep = great_circle_km(lat[i], lon[i], lat[js], lon[js])
        # The separation threshold uses the *smaller* of the two pixel
        # scales (the finer-resolution frame sets how close is "close");
        # the displacement-in-pixels formula (see build_pairs) uses the
        # *larger* one instead (a cloud crossing the coarser frame's pixel
        # is the one that limits what is actually measurable). These are
        # two different normative quantities from the spec, not the same
        # number reused.
        pixel_min = np.minimum(pix[i], pix[js])
        pixel_max = np.maximum(pix[i], pix[js])
        keep_sep = sep <= SEPARATION_FACTOR * pixel_min
        if not keep_sep.any():
            continue
        js = js[keep_sep]
        dt = dt[keep_sep]
        sep = sep[keep_sep]
        pixel_min = pixel_min[keep_sep]
        pixel_max = pixel_max[keep_sep]

        # Nearest-partner-per-sequence reduction (see JUDGMENT CALL in the
        # module docstring): the separation threshold alone typically admits
        # several frames of the *same* other sequence (adjacent raster
        # positions, since a repeat mosaic's footprint half-width is large
        # compared to the raster step). Keep only the closest one per
        # (frame i, target sequence) so a frame's declared partner in a
        # given repeat sequence is its true nearest match, not every
        # position within the generous proxy radius.
        if js.size > 1:
            target_seq = seq[js]
            order = np.argsort(sep, kind="stable")
            _, first_positions = np.unique(target_seq[order], return_index=True)
            keep_idx = order[first_positions]
            keep_idx.sort()
            js = js[keep_idx]
            dt = dt[keep_idx]
            sep = sep[keep_idx]
            pixel_min = pixel_min[keep_idx]
            pixel_max = pixel_max[keep_idx]

        chunks.append(
            pd.DataFrame(
                {
                    "product_id_a": pid[i],
                    "product_id_b": pid[js],
                    "orbit_dir": g["orbit_dir"].iloc[0],
                    "half": g["half"].iloc[0],
                    "seq_id_a": seq[i],
                    "seq_id_b": seq[js],
                    "dt_s": dt,
                    "sep_km": sep,
                    "pixel_km": pixel_min,
                    "pixel_km_max": pixel_max,
                }
            )
        )
    if not chunks:
        return None
    return pd.concat(chunks, ignore_index=True)


def build_pairs(frames: pd.DataFrame) -> pd.DataFrame:
    """Candidate pairs across every (orbit_dir, half) group.

    ``frames`` must already be the unit-of-analysis table (see
    :func:`select_unit_of_analysis`).
    """
    columns = [
        "product_id_a",
        "product_id_b",
        "orbit_dir",
        "half",
        "seq_id_a",
        "seq_id_b",
        "dt_s",
        "sep_km",
        "pixel_km",
        "d_px_10",
        "d_px_30",
        "d_px_100",
    ]
    parts = [
        result
        for _, group in frames.groupby(["orbit_dir", "half"], sort=False)
        if (result := _group_pairs(group)) is not None
    ]
    if not parts:
        return pd.DataFrame(columns=columns)
    pairs = pd.concat(parts, ignore_index=True)
    # d_px uses the *larger* of the two frames' pixel scales (see the note
    # in _group_pairs); "pixel_km" in the output is the smaller one used for
    # the separation threshold, per the spec's single listed pairs column.
    for speed in REFERENCE_SPEEDS_MS:
        pairs[f"d_px_{int(speed)}"] = _d_px(
            pairs["dt_s"].to_numpy(np.float64), pairs["pixel_km_max"].to_numpy(np.float64), speed
        )
    return pairs[columns]


def add_per_frame_stats(frames: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    """Attach has_partner, n_partners, best_dt_s, trackable_10/30/100.

    ``best_dt_s`` (REVISED 2026-09-05, see the spec) is the *smallest* dt
    among a frame's partners (from either side of the pairs table) that are
    trackable at U=30 m/s (``0.5 <= d_px_30 <= 40``); NaN when the frame has
    no trackable-30 partner at all, even if it has other (untrackable)
    partners. This replaced an earlier "d_px_30 closest to 5" rule, which
    was literally what the original spec asked for but was not robust on
    PJ4's long, densely repeated north-pole campaign (~30 sequences spaced
    ~487 s apart over ~1.5 h): a sequence several repeat-intervals away
    could coincidentally land closer to the d_px=5 target than the true
    nearest repeat, purely from dt/pixel arithmetic, and win the
    comparison. Smallest-trackable-dt does not have that failure mode: it
    always prefers the nearest-in-time usable baseline.
    """
    frames = frames.copy()
    if pairs.empty:
        frames["n_partners"] = 0
        frames["has_partner"] = False
        for speed in REFERENCE_SPEEDS_MS:
            frames[f"trackable_{int(speed)}"] = False
        frames["best_dt_s"] = np.nan
        return frames

    side_a = pairs.rename(columns={"product_id_a": "product_id"})[
        ["product_id", "dt_s", "d_px_10", "d_px_30", "d_px_100"]
    ]
    side_b = pairs.rename(columns={"product_id_b": "product_id"})[
        ["product_id", "dt_s", "d_px_10", "d_px_30", "d_px_100"]
    ]
    long = pd.concat([side_a, side_b], ignore_index=True)

    counts = long.groupby("product_id").size()

    trackable = {}
    for speed in REFERENCE_SPEEDS_MS:
        col = f"d_px_{int(speed)}"
        ok = (long[col] >= DPX_MIN) & (long[col] <= DPX_MAX)
        trackable[int(speed)] = long.assign(ok=ok).groupby("product_id")["ok"].any()

    trackable_30_mask = (long["d_px_30"] >= DPX_MIN) & (long["d_px_30"] <= DPX_MAX)
    best_dt = (
        long.loc[trackable_30_mask].groupby("product_id")["dt_s"].min()
    )

    indexed = frames.set_index("product_id")
    indexed["n_partners"] = counts.reindex(indexed.index).fillna(0).astype(int)
    indexed["has_partner"] = indexed["n_partners"] > 0
    for speed in REFERENCE_SPEEDS_MS:
        indexed[f"trackable_{int(speed)}"] = (
            trackable[int(speed)].reindex(indexed.index).fillna(False).astype(bool)
        )
    indexed["best_dt_s"] = best_dt.reindex(indexed.index)
    return indexed.reset_index()


def build_sequence_pairs(frames: pd.DataFrame, pairs: pd.DataFrame) -> pd.DataFrame:
    """Ordered-sequence-pair aggregates (A earlier than B, same orbit/half).

    Restricted to sequence pairs with at least 3 candidate frame pairs.
    Columns: orbit_dir, half, seq_id_a, seq_id_b, dt_start_s, n_pairs,
    frac_a (fraction of A's frames with a partner in B), median_d_px_30.
    """
    columns = [
        "orbit_dir",
        "half",
        "seq_id_a",
        "seq_id_b",
        "dt_start_s",
        "n_pairs",
        "frac_a",
        "median_d_px_30",
    ]
    if pairs.empty:
        return pd.DataFrame(columns=columns)

    first_time = frames.groupby(["orbit_dir", "half", "seq_id"])["start_time"].min()
    frame_count = frames.groupby(["orbit_dir", "half", "seq_id"]).size()

    work = pairs.copy()
    a_first = work["seq_id_a"].to_numpy() <= work["seq_id_b"].to_numpy()
    work["_seq_lo"] = np.where(a_first, work["seq_id_a"], work["seq_id_b"])
    work["_seq_hi"] = np.where(a_first, work["seq_id_b"], work["seq_id_a"])

    records = []
    for (orbit_dir, half, seq_lo, seq_hi), g in work.groupby(
        ["orbit_dir", "half", "_seq_lo", "_seq_hi"], sort=False
    ):
        if len(g) < 3:
            continue
        t_lo = first_time.get((orbit_dir, half, seq_lo))
        t_hi = first_time.get((orbit_dir, half, seq_hi))
        if t_lo is None or t_hi is None:
            continue
        if t_lo <= t_hi:
            seq_a, seq_b, t_a, t_b = seq_lo, seq_hi, t_lo, t_hi
        else:
            seq_a, seq_b, t_a, t_b = seq_hi, seq_lo, t_hi, t_lo
        n_a = int(frame_count.get((orbit_dir, half, seq_a), 0))
        a_products = set(g.loc[g["seq_id_a"] == seq_a, "product_id_a"]) | set(
            g.loc[g["seq_id_b"] == seq_a, "product_id_b"]
        )
        frac_a = len(a_products) / n_a if n_a else np.nan
        records.append(
            {
                "orbit_dir": orbit_dir,
                "half": half,
                "seq_id_a": seq_a,
                "seq_id_b": seq_b,
                "dt_start_s": abs((t_b - t_a).total_seconds()),
                "n_pairs": len(g),
                "frac_a": frac_a,
                "median_d_px_30": float(g["d_px_30"].median()),
            }
        )
    if not records:
        return pd.DataFrame(columns=columns)
    return pd.DataFrame.from_records(records)[columns]
