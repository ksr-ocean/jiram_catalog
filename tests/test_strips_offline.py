"""Offline tests for the strip library: chunking, quantisation, naming, queries.

Nothing here touches SPICE or the mirror.  The parts of :mod:`strips` that
decide *what a strip is* -- where one pass ends and the next begins, which
resolution class it is filed under, what it is called, and which strips a
survey question returns -- are pure functions of a table, and they are the
parts a silent change would corrupt hardest to notice.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from jiram_catalog.strips import (
    RESOLUTION_CLASSES,
    INDEX_COLUMNS,
    boresight_centroid,
    chunk_table,
    load_strips,
    parse_lat_band,
    resolution_class,
    seq_time,
    strip_id,
)


def _rows(
    seq: str,
    times: list[float],
    lats: list[float],
    lons: list[float],
    *,
    start: str = "2017-02-02T11:40:00",
) -> pd.DataFrame:
    """A synthetic unit-row table: seconds after ``start`` and boresights."""
    origin = pd.Timestamp(start)
    return pd.DataFrame(
        {
            "product_id": [f"{seq}_{i:03d}" for i in range(len(times))],
            "orbit_dir": 4,
            "seq_id": seq,
            "start_time": [origin + pd.Timedelta(seconds=t) for t in times],
            "bore_lat": lats,
            "bore_lon_east": lons,
            "median_pixel_km": 27.0,
        }
    )


# --------------------------------------------------------------------------
# chunking
# --------------------------------------------------------------------------
def test_chunk_splits_when_the_boresight_walks_too_far():
    # Eleven frames marching north one degree at a time: the walk from the
    # chunk's first row passes 12 degrees at the thirteenth degree, so the
    # first chunk holds the rows up to and including the 12-degree one.
    n = 20
    table = _rows(
        "04_M_A", [30.0 * i for i in range(n)], [0.0 + i for i in range(n)], [10.0] * n
    )
    chunks, skipped = chunk_table(table, min_frames=2)
    assert skipped == 0
    sizes = [len(part) for _, part in chunks]
    assert sizes == [13, 7]
    assert [index for index, _ in chunks] == [0, 1]
    # The split is on the walk from the chunk's *first* row, not the previous
    # one: every consecutive step here is only one degree.
    first = chunks[0][1]
    assert first["bore_lat"].tolist() == [float(i) for i in range(13)]


def test_chunk_splits_on_a_cadence_break():
    table = _rows("04_M_B", [0.0, 30.0, 60.0, 300.0, 330.0], [0.0] * 5, [10.0] * 5)
    chunks, skipped = chunk_table(table, min_frames=2)
    assert skipped == 0
    assert [len(part) for _, part in chunks] == [3, 2]
    assert [index for index, _ in chunks] == [0, 1]


def test_chunk_skips_short_chunks_but_keeps_their_index():
    # A lone frame between two cadence breaks is dropped, and the chunk that
    # follows keeps index 2 so that a strip's name does not move when
    # --min-frames changes.
    table = _rows(
        "04_M_C",
        [0.0, 30.0, 400.0, 800.0, 830.0, 860.0],
        [0.0] * 6,
        [10.0] * 6,
    )
    chunks, skipped = chunk_table(table, min_frames=2)
    assert skipped == 1
    assert [(index, len(part)) for index, part in chunks] == [(0, 2), (2, 3)]
    relaxed, relaxed_skipped = chunk_table(table, min_frames=1)
    assert relaxed_skipped == 0
    assert [index for index, _ in relaxed] == [0, 1, 2]


def test_chunk_rule_is_applied_within_each_sequence():
    table = pd.concat(
        [
            _rows("04_M_A", [0.0, 30.0], [0.0, 0.1], [10.0, 10.1]),
            _rows("04_M_B", [0.0, 30.0, 60.0], [40.0, 40.1, 40.2], [80.0] * 3),
        ],
        ignore_index=True,
    )
    chunks, skipped = chunk_table(table, min_frames=2)
    assert skipped == 0
    assert [(part["seq_id"].iloc[0], len(part)) for _, part in chunks] == [
        ("04_M_A", 2),
        ("04_M_B", 3),
    ]


def test_chunk_table_of_an_empty_table():
    assert chunk_table(pd.DataFrame(columns=["seq_id"]), min_frames=2) == ([], 0)


# --------------------------------------------------------------------------
# resolution classes
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("scale", "expected"),
    [
        (27.6, 30.0),  # log-nearest is 30, arithmetic-nearest would be 30 too
        (24.0, 20.0),  # arithmetic-nearest is 20; log-nearest is also 20
        (25.0, 30.0),  # arithmetic ties; log puts it above sqrt(600) = 24.49
        (1.0, 2.0),
        (10000.0, 300.0),
        (15.0, 15.0),
    ],
)
def test_resolution_class_is_log_nearest(scale, expected):
    assert resolution_class(scale) == expected


def test_resolution_class_ties_go_to_the_smaller_class():
    # An exact tie in binary floating point: log(2) - log(1) is bit for bit
    # log(4) - log(2), so the rule and not the rounding decides.
    assert resolution_class(2.0, classes=(1.0, 4.0)) == 1.0
    # On the real ladder the geometric mean is not exactly representable, so
    # only assert that the answer is one of the two neighbours it sits between
    # and that either side of it resolves the way it should.
    for low, high in zip(RESOLUTION_CLASSES[:-1], RESOLUTION_CLASSES[1:], strict=True):
        middle = float(np.sqrt(low * high))
        assert resolution_class(middle) in (low, high)
        assert resolution_class(middle * 0.999) == low
        assert resolution_class(middle * 1.001) == high


def test_resolution_class_rejects_nonsense():
    for bad in (0.0, -3.0, float("nan")):
        with pytest.raises(ValueError):
            resolution_class(bad)


# --------------------------------------------------------------------------
# boresight centroid
# --------------------------------------------------------------------------
def _lon_gap(first: float, second: float) -> float:
    return abs(((first - second + 180.0) % 360.0) - 180.0)


def test_centroid_across_the_antimeridian():
    latitude, longitude = boresight_centroid([0.0, 0.0, 0.0], [355.0, 0.0, 5.0])
    assert latitude == pytest.approx(0.0, abs=1e-9)
    assert _lon_gap(longitude, 0.0) < 1e-9
    # The naive arithmetic mean would land on the far side of the planet.
    assert _lon_gap(longitude, 120.0) > 100.0


def test_centroid_across_the_prime_meridian_is_not_a_mean_of_angles():
    latitude, longitude = boresight_centroid([10.0, 20.0], [350.0, 30.0])
    assert _lon_gap(longitude, 10.0) < 2.0
    assert 14.0 < latitude < 16.0


def test_centroid_ignores_missing_boresights():
    latitude, longitude = boresight_centroid(
        [np.nan, 45.0, 45.0], [np.nan, 100.0, 100.0]
    )
    assert latitude == pytest.approx(45.0)
    assert longitude == pytest.approx(100.0)
    with pytest.raises(ValueError):
        boresight_centroid([np.nan], [np.nan])


# --------------------------------------------------------------------------
# naming
# --------------------------------------------------------------------------
def test_strip_id_formatting():
    assert strip_id(4, "M", "04_M_2017033T114003", 0) == "04_M_2017033T114003_00"
    assert strip_id(24, "l", "24_L_2019364T031415", 11) == "24_L_2019364T031415_11"
    assert seq_time("04_M_2017033T114003") == "2017033T114003"


def test_parse_lat_band():
    assert parse_lat_band("-45:45") == (-45.0, 45.0)
    for bad in ("45", "45:-45", "a:b"):
        with pytest.raises(ValueError):
            parse_lat_band(bad)


# --------------------------------------------------------------------------
# library queries
# --------------------------------------------------------------------------
def _index(tmp_path):
    """A synthetic library index of four strips, written where load_strips looks."""
    records = [
        dict(
            strip_id="04_M_2017033T114003_00", orbit=4, band="M",
            time_start="2017-02-02T11:40:00", time_end="2017-02-02T11:44:00",
            lat_min=70.0, lat_max=89.0, km_per_px=30.0, valid_frac=0.5,
            dayside_frac=0.9,
        ),
        dict(
            strip_id="04_M_2017033T120000_00", orbit=4, band="M",
            time_start="2017-02-02T12:00:00", time_end="2017-02-02T12:05:00",
            lat_min=-50.0, lat_max=-40.0, km_per_px=10.0, valid_frac=0.2,
            dayside_frac=0.1,
        ),
        dict(
            strip_id="24_L_2019364T031415_00", orbit=24, band="L",
            time_start="2019-12-30T03:14:00", time_end="2019-12-30T03:18:00",
            lat_min=-10.0, lat_max=10.0, km_per_px=100.0, valid_frac=0.8,
            dayside_frac=0.5,
        ),
        dict(
            strip_id="24_M_2019364T040000_00", orbit=24, band="M",
            time_start="2019-12-30T04:00:00", time_end="2019-12-30T04:04:00",
            lat_min=46.0, lat_max=60.0, km_per_px=15.0, valid_frac=0.6,
            dayside_frac=0.0,
        ),
    ]
    table = pd.DataFrame(records)
    for column in INDEX_COLUMNS:
        if column not in table.columns:
            table[column] = 0
    table["path"] = [f"strips/orbit{r['orbit']:02d}/{r['strip_id']}.nc" for r in records]
    table["chunk_index"] = 0
    table["n_frames"] = 3
    table = table.loc[:, list(INDEX_COLUMNS)]
    path = tmp_path / "strips" / "strips.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    table.to_parquet(path, index=False)
    return tmp_path


def _ids(frame):
    return sorted(frame["strip_id"].tolist())


def test_load_strips_latitude_overlap_semantics(tmp_path):
    root = _index(tmp_path)
    # [-45, 45] overlaps the equatorial strip only: the polar strip starts at
    # 70, the southern one ends at -40 -- which *does* overlap -- and the
    # 46-to-60 strip misses by a degree.
    query = load_strips(root, lat_min=-45, lat_max=45)
    assert _ids(query) == ["04_M_2017033T120000_00", "24_L_2019364T031415_00"]
    assert ((query["lat_max"] >= -45) & (query["lat_min"] <= 45)).all()
    # A one-sided bound keeps everything that reaches it.
    assert _ids(load_strips(root, lat_min=45)) == [
        "04_M_2017033T114003_00",
        "24_M_2019364T040000_00",
    ]
    assert _ids(load_strips(root, lat_max=-45)) == ["04_M_2017033T120000_00"]
    # A band that touches nothing returns an empty frame, not an error.
    assert load_strips(root, lat_min=61, lat_max=69).empty


def test_load_strips_time_window_is_an_overlap(tmp_path):
    root = _index(tmp_path)
    inside = load_strips(root, time_min="2017-02-02T11:42:00", time_max="2017-02-02T11:43:00")
    assert _ids(inside) == ["04_M_2017033T114003_00"]
    assert load_strips(root, time_min="2017-02-02T11:44:30", time_max="2017-02-02T11:59:00").empty
    assert len(load_strips(root, time_min="2019-01-01")) == 2
    assert len(load_strips(root, time_max="2018-01-01")) == 2


def test_load_strips_scalar_thresholds_and_keys(tmp_path):
    root = _index(tmp_path)
    assert _ids(load_strips(root, resolution_max_km=15.0)) == [
        "04_M_2017033T120000_00",
        "24_M_2019364T040000_00",
    ]
    assert _ids(load_strips(root, min_valid_frac=0.55)) == [
        "24_L_2019364T031415_00",
        "24_M_2019364T040000_00",
    ]
    assert _ids(load_strips(root, dayside_min=0.4)) == [
        "04_M_2017033T114003_00",
        "24_L_2019364T031415_00",
    ]
    assert _ids(load_strips(root, orbits=[24], band="m")) == ["24_M_2019364T040000_00"]
    assert len(load_strips(root)) == 4


def test_load_strips_combines_filters(tmp_path):
    root = _index(tmp_path)
    query = load_strips(
        root, lat_min=-90, lat_max=0, resolution_max_km=50.0, min_valid_frac=0.1
    )
    # The equatorial strip overlaps the band but is coarser than 50 km/px.
    assert _ids(query) == ["04_M_2017033T120000_00"]


def test_load_strips_without_an_index(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_strips(tmp_path)
