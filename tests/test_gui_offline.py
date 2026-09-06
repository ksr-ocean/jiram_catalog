"""Offline tests for the GUI: synthetic tables, no mirror, no server.

Everything here runs on tables and Datasets built in memory, with the
loaders monkeypatched, so the suite says nothing about the archive and
everything about the code that draws it.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd
import panel as pn
import pytest
import xarray as xr

from jiram_catalog.gui import app, data, views_catalog, views_poles, views_strips
from jiram_catalog.gui.state import LAT_BAND_NAMES, CatalogState

ROWS = 12


# ---------------------------------------------------------------------------
# synthetic products
# ---------------------------------------------------------------------------
def synthetic_catalog() -> pd.DataFrame:
    """Twelve frames spread over orbit, epoch, band, latitude and quality."""
    times = pd.to_datetime(
        [datetime(2017, 1 + index % 12, 1 + index, 3, 0, 0) for index in range(ROWS)]
    )
    table = pd.DataFrame(
        {
            "product_id": [f"JIR_IMG_RDR_{index:04d}" for index in range(ROWS)],
            "orbit_dir": np.arange(ROWS, dtype="int64") + 1,
            "half": ["L" if index % 2 else "M" for index in range(ROWS)],
            "band": ["LM"] * ROWS,
            "seq_id": [f"{index:02d}_M_seq" for index in range(ROWS)],
            "start_time": times,
            "bore_lat": np.linspace(-85.0, 85.0, ROWS),
            "bore_lon_east": np.linspace(5.0, 355.0, ROWS),
            "bore_emission": np.linspace(5.0, 85.0, ROWS),
            "on_planet_frac": np.linspace(0.05, 1.0, ROWS),
            "median_pixel_km": np.linspace(10.0, 450.0, ROWS),
            "dayside_frac": np.where(np.arange(ROWS) % 3 == 0, 0.0, 0.7),
            "pole_inside": np.zeros(ROWS, dtype=bool),
            "min_lat": np.linspace(-88.0, 80.0, ROWS),
            "max_lat": np.linspace(-80.0, 88.0, ROWS),
            "geo_ok": np.ones(ROWS, dtype=bool),
            "trackable_30": np.arange(ROWS) % 4 == 0,
        }
    )
    table["orbit"] = table["orbit_dir"].astype("int64")
    table["lat_band"] = data.lat_band(table["bore_lat"].to_numpy())
    table["month"] = table["start_time"].dt.to_period("M").dt.to_timestamp()
    table["year"] = table["start_time"].dt.year.astype("int64")
    return table


def synthetic_strips() -> pd.DataFrame:
    """A two-row strip index, one polar and one mid-latitude."""
    table = pd.DataFrame(
        {
            "strip_id": ["04_L_synth_00", "24_M_synth_01"],
            "orbit": [4, 24],
            "band": ["L", "M"],
            "n_frames": [7, 5],
            "time_start": pd.to_datetime(["2017-02-02T04:00", "2020-01-01T04:00"]),
            "time_end": pd.to_datetime(["2017-02-02T04:05", "2020-01-01T04:05"]),
            "time_mid": pd.to_datetime(["2017-02-02T04:02", "2020-01-01T04:02"]),
            "center_lat": [78.0, -40.0],
            "center_lon_east": [30.0, 200.0],
            "km_per_px": [15.0, 150.0],
            "resolution_class": [15.0, 150.0],
            "valid_frac": [0.8, 0.4],
            "dayside_frac": [0.9, 0.1],
            "median_emission": [30.0, 60.0],
            "lat_min": [70.0, -50.0],
            "lat_max": [86.0, -30.0],
            "lon_min_east": [0.0, 180.0],
            "lon_max_east": [60.0, 220.0],
            "lon_span_deg": [60.0, 40.0],
            "pole_inside": [False, False],
            "path": ["strips/orbit04/a.nc", "strips/orbit24/b.nc"],
            "rows": [32, 32],
            "cols": [32, 32],
        }
    )
    table["lat_band"] = data.lat_band(table["center_lat"].to_numpy())
    table["year"] = table["time_mid"].dt.year.astype("int64")
    return table


def _grid(size: int = 32) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """A 32 x 32 polar patch whose longitudes cross the 0/360 seam."""
    x_km = np.linspace(-480.0, 480.0, size)
    y_km = np.linspace(-480.0, 480.0, size)
    xx, yy = np.meshgrid(x_km, y_km)
    radius = np.hypot(xx, yy)
    lat = 90.0 - radius / 100.0
    lon = np.degrees(np.arctan2(yy, xx)) % 360.0
    return x_km, y_km, lat.astype("float32"), lon.astype("float32")


def synthetic_stack(steps: int = 2, size: int = 32) -> xr.Dataset:
    """A two-step stack with the coordinates a region stack carries."""
    x_km, y_km, lat, lon = _grid(size)
    rng = np.random.default_rng(4)
    image = rng.normal(1.0, 0.1, (steps, size, size)).astype("float32")
    valid = np.ones((steps, size, size), dtype=bool)
    valid[:, :2, :] = False
    return xr.Dataset(
        data_vars={
            "image": (("time", "y", "x"), image),
            "valid": (("time", "y", "x"), valid),
            "emission": (("time", "y", "x"), (60.0 * np.ones_like(image)).astype("float32")),
        },
        coords={
            "time": pd.to_datetime(["2017-02-02T08:10", "2017-02-02T08:20"])[:steps],
            "x_km": ("x", x_km),
            "y_km": ("y", y_km),
            "lat": (("y", "x"), lat),
            "lon_east": (("y", "x"), lon),
            "product_id": ("time", np.array([f"JIR_IMG_RDR_{i}" for i in range(steps)])),
            "seq_id": ("time", np.array(["04_M_seq"] * steps)),
            "orbit": ("time", np.full(steps, 4, dtype="int32")),
        },
        attrs={"region": "synthetic", "band": "M", "level": "sequence", "km_per_px": 15.0},
    )


def synthetic_strip(strip_id: str, size: int = 32) -> xr.Dataset:
    """A strip Dataset with the mask, the angles and the local-time clock."""
    x_km, y_km, lat, lon = _grid(size)
    rng = np.random.default_rng(7)
    image = rng.normal(1.0, 0.05, (size, size)).astype("float32")
    valid = np.ones((size, size), dtype=bool)
    valid[:3, :] = False
    local_time = (lon / 15.0) % 24.0
    return xr.Dataset(
        data_vars={
            "image": (("y", "x"), image),
            "valid": (("y", "x"), valid),
            "emission": (("y", "x"), (45.0 * np.ones((size, size))).astype("float32")),
            "incidence": (("y", "x"), (70.0 * np.ones((size, size))).astype("float32")),
        },
        coords={
            "x_km": ("x", x_km),
            "y_km": ("y", y_km),
            "lat": (("y", "x"), lat),
            "lon_east": (("y", "x"), lon),
            "local_time_h": (("y", "x"), local_time.astype("float32")),
        },
        attrs={
            "strip_id": strip_id,
            "km_per_px": 30.0,
            "band": "L",
            "orbit": 4,
            "valid_frac": 0.9,
            "rows": size,
            "cols": size,
        },
    )


def synthetic_stats() -> xr.Dataset:
    """The shape ``stats2d.strip_statistics`` returns, without the cost."""
    k = np.linspace(0.0, 3e-5, 40)
    r = np.linspace(3e4, 9e5, 20)
    with np.errstate(divide="ignore"):
        spectrum = np.where(k > 0, k ** -2.0, np.nan)
    return xr.Dataset(
        data_vars={
            "E": ("k", spectrum),
            "P_x": ("kx", spectrum),
            "P_y": ("ky", spectrum),
            "S2": ("r", (r / 1e5) ** 0.66),
            "S3": ("r", -((r / 1e5) ** 1.0)),
        },
        coords={"k": k, "kx": k, "ky": k, "r": r},
        attrs={"km_per_px": 30.0, "band": "L", "orbit": 4, "valid_frac": 0.9},
    )


@pytest.fixture
def catalog() -> pd.DataFrame:
    return synthetic_catalog()


@pytest.fixture
def offline(monkeypatch, tmp_path) -> object:
    """Every loader answered from memory; nothing outside ``tmp_path`` is touched."""
    strips = synthetic_strips()
    stack = synthetic_stack()
    monkeypatch.setattr(data, "catalog_table", lambda mirror=None: synthetic_catalog())
    monkeypatch.setattr(data, "strips_table", lambda mirror=None: strips)
    monkeypatch.setattr(data, "trackability_table", lambda mirror=None: None)
    monkeypatch.setattr(data, "has_trackability", lambda mirror=None: True)
    monkeypatch.setattr(data, "stack_paths", lambda mirror=None: [tmp_path / "synthetic_sequence.nc"])
    monkeypatch.setattr(data, "open_stack", lambda path: stack)
    monkeypatch.setattr(
        data, "open_strip", lambda mirror, strip: synthetic_strip(str(strip))
    )
    monkeypatch.setattr(data, "strip_stats", lambda mirror, strip: synthetic_stats())
    return tmp_path


# ---------------------------------------------------------------------------
# filters
# ---------------------------------------------------------------------------
def test_apply_filters_default_keeps_everything(catalog):
    assert len(data.apply_filters(catalog, CatalogState())) == len(catalog)


def test_apply_filters_orbit_range(catalog):
    state = CatalogState(orbit_range=(3, 5))
    out = data.apply_filters(catalog, state)
    assert set(out["orbit"]) == {3, 4, 5}


def test_apply_filters_date_range(catalog):
    state = CatalogState(date_range=(datetime(2017, 3, 1), datetime(2017, 5, 1)))
    out = data.apply_filters(catalog, state)
    assert not out.empty
    assert out["start_time"].between(*state.date_range).all()


def test_apply_filters_band_uses_the_band_half(catalog):
    out = data.apply_filters(catalog, CatalogState(band="L"))
    assert set(out["half"]) == {"L"}
    assert len(out) == len(catalog) // 2


def test_apply_filters_resolution_and_emission(catalog):
    out = data.apply_filters(catalog, CatalogState(resolution_max_km=100.0))
    assert out["median_pixel_km"].max() <= 100.0
    out = data.apply_filters(catalog, CatalogState(emission_max=30.0))
    assert out["bore_emission"].max() <= 30.0


def test_apply_filters_keeps_rows_whose_value_is_unknown(catalog):
    table = catalog.copy()
    table.loc[0, "bore_emission"] = np.nan
    out = data.apply_filters(table, CatalogState(emission_max=10.0))
    assert table.loc[0, "product_id"] in set(out["product_id"])


def test_apply_filters_on_planet_and_dayside(catalog):
    out = data.apply_filters(catalog, CatalogState(on_planet_min=0.5))
    assert out["on_planet_frac"].min() >= 0.5
    out = data.apply_filters(catalog, CatalogState(dayside_only=True))
    assert (out["dayside_frac"] > 0).all()
    assert len(out) < len(catalog)


def test_apply_filters_lat_band(catalog):
    out = data.apply_filters(catalog, CatalogState(lat_band="N polar"))
    assert set(out["lat_band"]) == {"N polar"}
    assert out["bore_lat"].min() >= 60.0


def test_apply_filters_revisit_only(catalog):
    out = data.apply_filters(catalog, CatalogState(revisit_only=True))
    assert out["trackable_30"].all()
    assert len(out) == int(catalog["trackable_30"].sum())


def test_apply_filters_without_a_trackability_column(catalog):
    table = catalog.drop(columns=["trackable_30"])
    out = data.apply_filters(table, CatalogState(revisit_only=True))
    assert len(out) == len(table)


def test_apply_strip_filters():
    strips = synthetic_strips()
    state = CatalogState(strip_band="L")
    assert list(data.apply_strip_filters(strips, state)["strip_id"]) == ["04_L_synth_00"]
    state = CatalogState(strip_lat_band="N polar")
    assert list(data.apply_strip_filters(strips, state)["strip_id"]) == ["04_L_synth_00"]
    state = CatalogState(strip_resolution_max_km=20.0)
    assert list(data.apply_strip_filters(strips, state)["strip_id"]) == ["04_L_synth_00"]
    state = CatalogState(strip_valid_min=0.5, strip_dayside_min=0.5)
    assert list(data.apply_strip_filters(strips, state)["strip_id"]) == ["04_L_synth_00"]
    state = CatalogState(strip_date_range=(datetime(2019, 1, 1), datetime(2021, 1, 1)))
    assert list(data.apply_strip_filters(strips, state)["strip_id"]) == ["24_M_synth_01"]


# ---------------------------------------------------------------------------
# the polar view
# ---------------------------------------------------------------------------
def test_polar_xy_pole_and_rim():
    x, y = views_catalog.polar_xy([90.0, 0.0, 0.0], [0.0, 0.0, 90.0], "N")
    assert np.allclose(x[0], 0.0) and np.allclose(y[0], 0.0)
    assert np.allclose((x[1], y[1]), (90.0, 0.0), atol=1e-9)
    assert np.allclose((x[2], y[2]), (0.0, 90.0), atol=1e-9)


def test_polar_xy_radius_and_hemisphere():
    lat = np.array([60.0, 30.0, -60.0])
    lon = np.array([0.0, 180.0, 0.0])
    x, y = views_catalog.polar_xy(lat, lon, "N")
    assert np.allclose(np.hypot(x[:2], y[:2]), [30.0, 60.0])
    assert np.isnan(x[2]) and np.isnan(y[2])
    x, y = views_catalog.polar_xy(lat, lon, "S")
    assert np.isnan(x[0]) and np.allclose(np.hypot(x[2], y[2]), 30.0)


def test_display_frame_switches_coordinates(catalog):
    cylindrical = views_catalog.display_frame(catalog, "cyl")
    assert np.allclose(cylindrical["map_x"], catalog["bore_lon_east"])
    polar = views_catalog.display_frame(catalog, "N")
    assert np.nanmax(np.hypot(polar["map_x"], polar["map_y"])) <= 90.0


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------
def test_products_within_a_box(catalog):
    frame = views_catalog.display_frame(catalog, "cyl")
    everything = views_catalog.products_within(frame, (0.0, -90.0, 360.0, 90.0))
    assert everything == list(catalog["product_id"])
    north = views_catalog.products_within(frame, (0.0, 60.0, 360.0, 90.0))
    assert north and set(north) <= set(
        catalog.loc[catalog["bore_lat"] >= 60.0, "product_id"]
    )
    assert views_catalog.products_within(frame, None) is None
    # the same box means the same thing in the polar view
    polar = views_catalog.display_frame(catalog, "N")
    assert views_catalog.products_within(polar, (-30.0, -30.0, 30.0, 30.0)) == north


def test_state_json_round_trip():
    state = CatalogState(
        orbit_range=(4, 24),
        date_range=(datetime(2017, 1, 1), datetime(2018, 1, 1)),
        band="M",
        resolution_max_km=60.0,
        emission_max=45.0,
        on_planet_min=0.3,
        dayside_only=True,
        lat_band="N polar",
        revisit_only=True,
        polar_view="N",
        selection=["a", "b"],
        current_stack="/tmp/stack.nc",
        current_strip="04_L_synth_00",
        poles_orbits=[4, 5],
        strip_band="L",
        strip_valid_min=0.25,
    )
    other = CatalogState().from_json(state.to_json())
    for name in CatalogState.SESSION_PARAMETERS:
        assert getattr(other, name) == getattr(state, name), name


def test_state_from_json_ignores_junk():
    state = CatalogState()
    state.from_json('{"band": "not a band", "unknown": 1, "selection": ["x"]}')
    assert state.band == "all" and state.selection == ["x"]


# ---------------------------------------------------------------------------
# contours
# ---------------------------------------------------------------------------
def test_graticule_paths_cross_the_seam():
    x_km, y_km, lat, lon = _grid(64)
    paths = data.graticule_paths(lat, lon, x_km, y_km, dlat=2.0, dlon=30.0)
    assert paths, "no graticule"
    assert all(path.ndim == 2 and path.shape[1] == 2 for path in paths)
    assert all(np.isfinite(path).all() for path in paths)
    # the 0 degree meridian is drawn, and only on its own side of the seam
    seam = data.contour_paths(lon, x_km, y_km, [0.0], period=360.0)
    assert seam, "the seam-crossing meridian is missing"
    for path in seam:
        assert (path[:, 0] >= -1e-9).all(), "the 0 E meridian leaked past the seam"
    # every point sits inside the grid
    limits = (x_km.min(), x_km.max(), y_km.min(), y_km.max())
    for path in paths:
        assert path[:, 0].min() >= limits[0] - 1e-6 and path[:, 0].max() <= limits[1] + 1e-6
        assert path[:, 1].min() >= limits[2] - 1e-6 and path[:, 1].max() <= limits[3] + 1e-6


def test_contour_paths_periodic_local_time():
    x_km, y_km, _lat, lon = _grid(48)
    clock = (lon / 15.0) % 24.0
    paths = data.contour_paths(clock, x_km, y_km, np.arange(0.0, 24.0, 2.0), period=24.0)
    assert len(paths) >= 10


def test_contour_paths_rejects_a_mismatched_grid():
    with pytest.raises(ValueError):
        data.contour_paths(np.zeros((4, 5)), np.arange(4), np.arange(5), [0.0])


# ---------------------------------------------------------------------------
# the views
# ---------------------------------------------------------------------------
def _saves(obj, path) -> int:
    pn.panel(obj).save(str(path))
    return path.stat().st_size


def test_catalog_view_builds_and_saves(offline):
    state = CatalogState()
    view = views_catalog.build(state, offline)
    assert isinstance(view, pn.Column)
    assert _saves(view, offline / "catalog.html") > 1000
    assert isinstance(views_catalog.filters(state, offline), pn.Column)


def test_revisit_filter_is_hidden_without_a_trackability_table(offline, monkeypatch):
    state = CatalogState()
    labels = [
        getattr(widget, "label", "") for widget in views_catalog.filters(state, offline)
    ]
    assert any("revisit" in str(label) for label in labels)
    monkeypatch.setattr(data, "has_trackability", lambda mirror=None: False)
    labels = [
        getattr(widget, "label", "") for widget in views_catalog.filters(state, offline)
    ]
    assert not any("revisit" in str(label) for label in labels)


def test_poles_view_builds_and_saves(offline):
    state = CatalogState()
    view = views_poles.build(state, offline)
    assert state.current_stack.endswith("synthetic_sequence.nc")
    assert _saves(view, offline / "poles.html") > 1000


def test_strips_view_builds_and_saves(offline):
    state = CatalogState()
    view = views_strips.build(state, offline)
    assert state.current_strip == "04_L_synth_00"
    assert _saves(view, offline / "strips.html") > 1000


def test_app_builds_three_tabs_that_save(offline):
    tabs = app.build(offline)
    assert isinstance(tabs, pn.Tabs) and len(tabs) == 3
    for index, tab in enumerate(tabs):
        assert _saves(tab, offline / f"tab{index}.html") > 1000


def test_page_has_a_sidebar_and_a_header(offline):
    page = app.page(offline)
    assert isinstance(page, pn.template.FastListTemplate)
    assert page.sidebar and page.main


def test_selection_flows_to_the_other_tabs(offline):
    state = CatalogState()
    views_catalog.build(state, offline)
    state.selection = ["JIR_IMG_RDR_0011"]
    views_strips.build(state, offline)
    assert state.current_strip


def test_stack_options_narrow_to_orbits(tmp_path, monkeypatch):
    paths = [
        tmp_path / "north_pole_paper" / "M_orbits4_sequence.nc",
        tmp_path / "north_pole_paper" / "M_orbits24_frame.nc",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"")
    monkeypatch.setattr(data, "stack_paths", lambda mirror=None: paths)
    assert len(views_poles.stack_options(tmp_path)) == 2
    narrowed = views_poles.stack_options(tmp_path, [24])
    assert list(narrowed) == ["north_pole_paper/M_orbits24_frame.nc"]
    # an orbit no stack was built for leaves the full list rather than nothing
    assert len(views_poles.stack_options(tmp_path, [77])) == 2


def test_gui_cmd_parser_defaults():
    from jiram_catalog import gui_cmd

    args = gui_cmd.make_parser().parse_args([])
    assert args.port == gui_cmd.DEFAULT_PORT
    assert args.address == gui_cmd.DEFAULT_ADDRESS
    assert args.no_browser is False
    args = gui_cmd.make_parser().parse_args(["--port", "5111", "--no-browser"])
    assert args.port == 5111 and args.no_browser is True


def test_gui_cmd_registers_a_subcommand():
    """The wiring `cli.make_parser` needs: one `add_subparser` call."""
    import argparse

    from jiram_catalog import gui_cmd

    parser = argparse.ArgumentParser()
    gui_cmd.add_subparser(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(["gui", "--port", "5111"])
    assert args.command == "gui" and args.port == 5111
    assert args.func is gui_cmd.run
