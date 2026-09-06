"""Offline tests for the geometry augmentation table (no SPICE kernels)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from jiram_catalog import geo, geometry
from jiram_catalog.geo import GEO_COLUMNS, geo_row, longitude_arc
from jiram_catalog.geometry import FrameGeometry

IFOV = 0.000237767


def make_geometry(
    *,
    lat: np.ndarray,
    lon_east: np.ndarray,
    on_planet: np.ndarray | None = None,
    incidence: np.ndarray | None = None,
    emission: np.ndarray | None = None,
    range_km: np.ndarray | None = None,
    boresight: dict | None = None,
    band: str = "M",
) -> FrameGeometry:
    """A FrameGeometry with hand-chosen arrays; nothing here touches SPICE."""
    lat = np.asarray(lat, dtype=np.float64)
    lon_east = np.asarray(lon_east, dtype=np.float64)
    shape = lat.shape
    if on_planet is None:
        on_planet = np.isfinite(lat)
    on_planet = np.asarray(on_planet, dtype=bool)
    filled = lambda value: np.full(shape, value, dtype=np.float64)  # noqa: E731
    incidence = filled(30.0) if incidence is None else np.asarray(incidence, float)
    emission = filled(20.0) if emission is None else np.asarray(emission, float)
    range_km = filled(100_000.0) if range_km is None else np.asarray(range_km, float)
    if boresight is None:
        boresight = {
            "lat": 10.0,
            "lon_east": 20.0,
            "range_km": 100_000.0,
            "emission": 21.0,
            "incidence": 31.0,
            "phase": 41.0,
        }
    corners = np.array(
        [
            [lat[0, 0], lon_east[0, 0]],
            [lat[0, -1], lon_east[0, -1]],
            [lat[-1, 0], lon_east[-1, 0]],
            [lat[-1, -1], lon_east[-1, -1]],
        ],
        dtype=np.float64,
    )
    return FrameGeometry(
        epoch="2017-02-02T11:40:03.870000",
        et=539_178_072.0,
        trgepc=539_178_069.0,
        band=band,
        frame=f"JUNO_JIRAM_I_{band}BAND",
        ifov_rad=IFOV,
        lines=shape[0],
        samples=shape[1],
        lat=lat,
        lon_east=lon_east,
        range_km=range_km,
        emission=emission,
        incidence=incidence,
        phase=filled(45.0),
        on_planet=on_planet,
        boresight=boresight,
        corners=corners,
        sub_sc_lat=1.0,
        sub_sc_lon_east=2.0,
        sc_altitude_km=3.0,
        obspos_km=np.zeros(3),
        sunpos_km=np.zeros(3),
    )


# --------------------------------------------------------------------------
# longitude arc
# --------------------------------------------------------------------------
def test_longitude_arc_wraps_across_the_prime_meridian():
    low, high, span = longitude_arc([350.0, 355.0, 0.0, 5.0])
    assert low == pytest.approx(350.0)
    assert high == pytest.approx(5.0)
    assert span == pytest.approx(15.0)


def test_longitude_arc_of_a_plain_interval():
    assert longitude_arc([10.0, 20.0, 30.0]) == pytest.approx((10.0, 30.0, 20.0))


def test_longitude_arc_all_around_is_the_whole_circle():
    assert longitude_arc(np.arange(0.0, 360.0, 10.0)) == (0.0, 360.0, 360.0)


def test_longitude_arc_of_nothing_is_nan():
    assert all(np.isnan(value) for value in longitude_arc([np.nan, np.nan]))


# --------------------------------------------------------------------------
# pole_inside, dayside_frac and the rest of one row
# --------------------------------------------------------------------------
def _row(**kwargs):
    return geo_row("PID", 4, "M", pd.Timestamp("2017-02-02T11:40:03.870"), **kwargs)


def test_pole_inside_when_the_north_pole_is_in_the_footprint():
    lat = np.array([[88.0, 89.6], [87.0, 89.9]])
    lon = np.array([[10.0, 200.0], [100.0, 300.0]])
    row = _row(geometry=make_geometry(lat=lat, lon_east=lon))
    assert row["pole_inside"] is True
    assert (row["lon_min_east"], row["lon_max_east"], row["lon_span_deg"]) == (
        0.0,
        360.0,
        360.0,
    )


def test_pole_inside_when_the_south_pole_is_in_the_footprint():
    lat = np.array([[-88.0, -89.6], [-87.0, -89.9]])
    lon = np.array([[10.0, 20.0], [30.0, 40.0]])
    row = _row(geometry=make_geometry(lat=lat, lon_east=lon))
    assert row["pole_inside"] is True
    assert row["lon_span_deg"] == 360.0


def test_a_full_longitude_ring_counts_as_a_pole_even_below_the_latitude_cut():
    lat = np.full((1, 36), 80.0)
    lon = np.arange(0.0, 360.0, 10.0).reshape(1, 36)
    row = _row(geometry=make_geometry(lat=lat, lon_east=lon))
    assert row["pole_inside"] is True
    assert row["lon_span_deg"] == 360.0


def test_no_pole_keeps_the_measured_arc():
    lat = np.array([[10.0, 20.0], [30.0, 40.0]])
    lon = np.array([[350.0, 355.0], [0.0, 5.0]])
    row = _row(geometry=make_geometry(lat=lat, lon_east=lon))
    assert row["pole_inside"] is False
    assert row["lon_min_east"] == pytest.approx(350.0)
    assert row["lon_max_east"] == pytest.approx(5.0)
    assert row["lon_span_deg"] == pytest.approx(15.0)
    assert row["min_lat"] == 10.0 and row["max_lat"] == 40.0


def test_dayside_frac_counts_only_on_planet_pixels_below_ninety_degrees():
    lat = np.array([[10.0, 20.0, 30.0, 40.0]])
    lon = np.array([[1.0, 2.0, 3.0, 4.0]])
    incidence = np.array([[10.0, 89.999, 90.0, 150.0]])
    on_planet = np.array([[True, True, True, True]])
    row = _row(
        geometry=make_geometry(
            lat=lat, lon_east=lon, incidence=incidence, on_planet=on_planet
        )
    )
    assert row["dayside_frac"] == pytest.approx(0.5)

    on_planet = np.array([[True, True, False, False]])
    lat = np.where(on_planet, lat, np.nan)
    row = _row(
        geometry=make_geometry(
            lat=lat, lon_east=lon, incidence=incidence, on_planet=on_planet
        )
    )
    assert row["dayside_frac"] == pytest.approx(1.0)
    assert row["on_planet_frac"] == pytest.approx(0.5)
    assert row["n_on_planet"] == 2


def test_off_planet_frame_keeps_the_spacecraft_columns_and_nans_the_rest():
    lat = np.full((2, 2), np.nan)
    lon = np.full((2, 2), np.nan)
    boresight = dict.fromkeys(
        ("lat", "lon_east", "range_km", "emission", "incidence", "phase"), np.nan
    )
    row = _row(
        geometry=make_geometry(
            lat=lat,
            lon_east=lon,
            on_planet=np.zeros((2, 2), dtype=bool),
            boresight=boresight,
        )
    )
    assert row["geo_ok"] is True
    assert row["n_on_planet"] == 0
    assert row["on_planet_frac"] == 0.0
    assert row["pole_inside"] is False
    assert row["sub_sc_lat"] == 1.0 and row["sc_altitude_km"] == 3.0
    for column in (
        "bore_lat",
        "min_lat",
        "max_lat",
        "lon_span_deg",
        "mean_emission",
        "median_pixel_km",
        "dayside_frac",
        "local_time_h",
    ):
        assert np.isnan(row[column]), column


def test_local_time_is_the_boresight_longitude_relative_to_the_subsolar_point():
    lat = np.array([[10.0]])
    lon = np.array([[20.0]])
    boresight = {
        "lat": 0.0,
        "lon_east": 100.0,
        "range_km": 1.0,
        "emission": 0.0,
        "incidence": 0.0,
        "phase": 0.0,
    }
    geometry = make_geometry(lat=lat, lon_east=lon, boresight=boresight)
    assert _row(geometry=geometry, subsolar_lon_east=100.0)["local_time_h"] == (
        pytest.approx(12.0)
    )
    assert _row(geometry=geometry, subsolar_lon_east=10.0)["local_time_h"] == (
        pytest.approx(18.0)
    )
    assert _row(geometry=geometry, subsolar_lon_east=280.0)["local_time_h"] == (
        pytest.approx(0.0)
    )
    assert np.isnan(_row(geometry=geometry)["local_time_h"])


def test_median_pixel_km_uses_the_on_planet_ranges_and_the_ifov():
    lat = np.array([[10.0, 20.0, 30.0]])
    lon = np.array([[1.0, 2.0, 3.0]])
    ranges = np.array([[1000.0, 2000.0, 300_000.0]])
    on_planet = np.array([[True, True, False]])
    row = _row(
        geometry=make_geometry(
            lat=lat, lon_east=lon, range_km=ranges, on_planet=on_planet
        )
    )
    assert row["median_pixel_km"] == pytest.approx(1500.0 * IFOV)


# --------------------------------------------------------------------------
# row schema
# --------------------------------------------------------------------------
def test_geo_row_has_exactly_the_documented_columns():
    lat = np.array([[10.0, 20.0], [30.0, 40.0]])
    lon = np.array([[1.0, 2.0], [3.0, 4.0]])
    row = _row(geometry=make_geometry(lat=lat, lon_east=lon))
    assert list(row) == GEO_COLUMNS
    assert len(row) == 38
    assert row["product_id"] == "PID"
    assert row["orbit_dir"] == 4
    assert row["half"] == "M"
    assert row["geo_error"] == ""
    assert (row["c1_lat"], row["c1_lon"]) == (10.0, 1.0)
    assert (row["c2_lat"], row["c2_lon"]) == (20.0, 2.0)
    assert (row["c3_lat"], row["c3_lon"]) == (30.0, 3.0)
    assert (row["c4_lat"], row["c4_lon"]) == (40.0, 4.0)


def test_failed_row_records_the_error_and_no_geometry():
    row = geo_row("PID", 24, "L", pd.NaT, None, error="ValueError: boom")
    assert list(row) == GEO_COLUMNS
    assert row["geo_ok"] is False
    assert row["geo_error"] == "ValueError: boom"
    assert row["n_on_planet"] == 0
    assert row["pole_inside"] is False
    numeric = set(GEO_COLUMNS) - {
        "product_id",
        "orbit_dir",
        "half",
        "start_time",
        "geo_ok",
        "geo_error",
        "n_on_planet",
        "pole_inside",
    }
    assert all(np.isnan(row[column]) for column in numeric)


def test_coerce_geo_gives_the_parquet_dtypes():
    lat = np.array([[10.0, 20.0], [30.0, 40.0]])
    lon = np.array([[1.0, 2.0], [3.0, 4.0]])
    table = geo.coerce_geo(pd.DataFrame.from_records([_row(geometry=make_geometry(lat=lat, lon_east=lon))]))
    assert list(table.columns) == GEO_COLUMNS
    assert table["orbit_dir"].dtype == "Int16"
    assert table["n_on_planet"].dtype == "Int32"
    assert table["geo_ok"].dtype == "boolean"
    assert str(table["start_time"].dtype).startswith("datetime64[")
    assert table["bore_lat"].dtype == "float64"


# --------------------------------------------------------------------------
# "LM" stacking (a stub stands in for the SPICE band geometry)
# --------------------------------------------------------------------------
@pytest.fixture
def stubbed_bands(monkeypatch):
    """Replace the band engine and the two imager-frame SPICE lookups."""
    marks = {"L": 1.0, "M": 2.0}

    def stub(epoch, band, kernels, *, lines=128, samples=432, abcorr="LT+S"):
        value = marks[band]
        return make_geometry(
            lat=np.full((lines, samples), value),
            lon_east=np.full((lines, samples), 10.0 * value),
            band=band,
        )

    monkeypatch.setattr(geometry, "band_geometry", stub)
    monkeypatch.setattr(geometry, "_imager_ifov", lambda: IFOV)
    monkeypatch.setattr(
        geometry,
        "_imager_boresight",
        lambda et, abcorr: {
            "lat": 5.0,
            "lon_east": 6.0,
            "range_km": 7.0,
            "emission": 8.0,
            "incidence": 9.0,
            "phase": 10.0,
        },
    )
    return marks


@pytest.mark.parametrize("order", [("L", "M"), ("M", "L")])
def test_lm_result_stacks_the_halves_in_lm_half_order(monkeypatch, stubbed_bands, order):
    monkeypatch.setattr(geometry, "LM_HALF_ORDER", order)
    result = geometry.frame_geometry("2017-02-02T11:40:03.870", "LM", kernels=None)

    assert result.band == "LM"
    assert result.frame == "JUNO_JIRAM_I"
    assert result.lines == 256
    assert result.samples == 432
    assert result.lat.shape == (256, 432)
    assert result.halves == order
    assert result.half_rows == ((0, 128), (128, 256))
    for band, (start, stop) in zip(result.halves, result.half_rows):
        assert (result.lat[start:stop] == stubbed_bands[band]).all()
        assert (result.lon_east[start:stop] == 10.0 * stubbed_bands[band]).all()
    assert result.ifov_rad == IFOV
    assert result.boresight["lat"] == 5.0
    assert result.corners[0].tolist() == [
        stubbed_bands[order[0]],
        10.0 * stubbed_bands[order[0]],
    ]
    assert result.corners[3].tolist() == [
        stubbed_bands[order[1]],
        10.0 * stubbed_bands[order[1]],
    ]


def test_single_band_geometry_carries_no_halves(stubbed_bands):
    result = geometry.frame_geometry("2017-02-02T11:40:03.870", "M", kernels=None)
    assert result.halves == ()
    assert result.half_rows == ()
    assert result.lines == 128


def test_lm_half_order_is_a_permutation_of_the_two_bands():
    assert sorted(geometry.LM_HALF_ORDER) == ["L", "M"]


def test_halves_by_band_covers_the_three_index_bands():
    assert geo.HALVES_BY_BAND == {"L": ("L",), "M": ("M",), "LM": ("L", "M")}
