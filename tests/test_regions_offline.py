"""Offline tests for regions, composites, cadence runs, loggrad, and movies.

Nothing here needs SPICE kernels or mirrored image data: the region grids are
closed-form, and every stack is synthetic.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from jiram_catalog.export_goflow import (
    GRADIENT_FLOOR,
    constant_cadence_runs,
    export_stack,
    log_gradient,
)
from jiram_catalog.geometry import ellipsoid_intercept
from jiram_catalog.regions import (
    JUPITER_RADII_KM,
    RegionGrid,
    _ray_intercepts,
    load_registry,
)
from jiram_catalog.reproject import PAPER_GRID
from jiram_catalog.stacks import composite_sequences

#: Largest registration residual of the empirical PAPER_GRID fit, in pixels
#: (docs/reports/paper_projection_fit.md).
PAPER_FIT_RESIDUAL_PX = 0.86


@pytest.fixture(scope="module")
def registry() -> dict[str, RegionGrid]:
    return load_registry()


def _random_polar_points(count: int = 1000, colatitude: float = 25.0, seed: int = 0):
    rng = np.random.default_rng(seed)
    return 90.0 - rng.uniform(0.0, colatitude, count), rng.uniform(0.0, 360.0, count)


# --------------------------------------------------------------------------
# the polar convention
# --------------------------------------------------------------------------
def test_polar_ortho_reproduces_paper_grid_exactly():
    """The region convention *is* PAPER_GRID's, to floating-point precision."""
    fitted = RegionGrid(
        name="paper_fit",
        projection="polar_ortho",
        km_per_px=PAPER_GRID.km_per_px,
        shape=(3000, 3200),
        hemisphere="N",
        pole_pixel=(PAPER_GRID.pole_line, PAPER_GRID.pole_sample),
        lon0_east_deg=PAPER_GRID.lon0_deg,
    )
    lat, lon = _random_polar_points()
    row, col, visible = fitted.latlon_to_pixel(lat, lon)
    line, sample = PAPER_GRID.latlon_to_pixel(lat, lon)
    assert visible.all()
    assert np.abs(row - line).max() < 1e-6
    assert np.abs(col - sample).max() < 1e-6


def test_north_pole_paper_matches_paper_grid_within_the_fit_residual(registry):
    """The registry's nominal grid and the fitted one agree to under a pixel.

    ``north_pole_paper`` carries the map labels' declared values (15 km/px,
    pole at 1800/1800, 90 E along +column) rather than the fitted
    ``PAPER_GRID`` numbers, which differ from them in the fourth decimal.  The
    difference over the whole canvas stays inside the fit's own registration
    residual, so the two are the same grid to the precision the fit resolved.
    """
    region = registry["north_pole_paper"]
    lat, lon = _random_polar_points()
    row, col, _ = region.latlon_to_pixel(lat, lon)
    line, sample = PAPER_GRID.latlon_to_pixel(lat, lon)
    assert np.abs(row - line).max() < PAPER_FIT_RESIDUAL_PX
    assert np.abs(col - sample).max() < PAPER_FIT_RESIDUAL_PX


def test_north_pole_paper_body_fixed_convention(registry):
    """Column follows body-fixed Y (90 E), row follows X (0 E)."""
    region = registry["north_pole_paper"]
    row0, col0 = region.pole_pixel
    row, col, _ = region.latlon_to_pixel(89.0, 0.0)
    assert float(row) > row0 and abs(float(col) - col0) < 1e-6
    row, col, _ = region.latlon_to_pixel(89.0, 90.0)
    assert float(col) > col0 and abs(float(row) - row0) < 1e-6


def test_polar_ortho_south_round_trip_and_hemispheres(registry):
    region = registry["south_pole"]
    rng = np.random.default_rng(3)
    rows = rng.uniform(0.0, region.rows - 1.0, 1000)
    cols = rng.uniform(0.0, region.cols - 1.0, 1000)
    lat, lon = region.pixel_to_latlon(rows, cols)
    assert np.isfinite(lat).all() and (lat < 0.0).all()
    back_rows, back_cols, visible = region.latlon_to_pixel(lat, lon)
    assert visible.all()
    assert np.abs(back_rows - rows).max() < 1e-6
    assert np.abs(back_cols - cols).max() < 1e-6
    # The wrong hemisphere is never on a polar grid, however small its radius.
    _, _, wrong = region.latlon_to_pixel(np.array([10.0, 80.0]), np.array([0.0, 120.0]))
    assert not wrong.any()
    # ... and the column axis is negated with respect to the northern map.
    _, col_south, _ = region.latlon_to_pixel(-89.0, 90.0)
    assert float(col_south) < region.pole_pixel[1]


def test_polar_ortho_beyond_the_limb_is_not_on_the_planet():
    region = RegionGrid(
        name="wide",
        projection="polar_ortho",
        km_per_px=100.0,
        shape=(2000, 2000),
        hemisphere="N",
        pole_pixel=(1000, 1000),
    )
    lat, lon = region.pixel_to_latlon(np.array([1000.0, 0.0]), np.array([1000.0, 0.0]))
    assert np.isclose(float(lat[0]), 90.0)
    assert not np.isfinite(lat[1]) and not np.isfinite(lon[1])


# --------------------------------------------------------------------------
# the tangent-plane convention
# --------------------------------------------------------------------------
def test_local_ortho_round_trip_and_centre(registry):
    region = registry["neb_15n"]
    row0, col0 = region.center_pixel
    lat, lon = region.pixel_to_latlon(row0, col0)
    assert float(lat) == pytest.approx(region.center[0], abs=1e-9)
    assert float(lon) == pytest.approx(region.center[1], abs=1e-9)

    rng = np.random.default_rng(5)
    rows = rng.uniform(0.0, region.rows - 1.0, 2000)
    cols = rng.uniform(0.0, region.cols - 1.0, 2000)
    lat, lon = region.pixel_to_latlon(rows, cols)
    assert np.isfinite(lat).all()
    back_rows, back_cols, visible = region.latlon_to_pixel(lat, lon)
    assert visible.all()
    assert np.abs(back_rows - rows).max() < 1e-6
    assert np.abs(back_cols - cols).max() < 1e-6


def test_local_ortho_axes_are_east_and_north(registry):
    region = registry["neb_15n"]
    row0, col0 = region.center_pixel
    lat, lon = region.pixel_to_latlon(row0, col0 + 100.0)
    assert float(lon) > region.center[1]
    assert float(lat) == pytest.approx(region.center[0], abs=0.2)
    lat, lon = region.pixel_to_latlon(row0 + 100.0, col0)
    assert float(lat) > region.center[0]


def test_local_ortho_far_side_is_not_visible(registry):
    region = registry["neb_15n"]
    lat0, lon0 = region.center
    far_lat = np.array([-lat0, lat0, lat0])
    far_lon = np.array([(lon0 + 180.0) % 360.0, (lon0 + 150.0) % 360.0, lon0])
    _, _, visible = region.latlon_to_pixel(far_lat, far_lon)
    assert list(visible) == [False, False, True]


def test_ray_intercepts_matches_the_library_solver():
    radii = np.asarray(JUPITER_RADII_KM)
    rng = np.random.default_rng(11)
    direction = np.array([0.2, -0.4, -0.9])
    direction /= np.linalg.norm(direction)
    origins = rng.normal(scale=3.0e4, size=(64, 3)) - 3.0e5 * direction
    points, distances, hit = _ray_intercepts(origins, direction, radii)
    assert hit.sum() > 32
    for index in range(len(origins)):
        expected, expected_t, expected_hit = ellipsoid_intercept(
            origins[index], direction, radii
        )
        assert bool(hit[index]) == bool(expected_hit)
        if hit[index]:
            assert np.allclose(points[index], expected, atol=1e-9)
            assert float(distances[index]) == pytest.approx(float(expected_t), abs=1e-9)


def test_registry_round_trips_through_to_dict(registry):
    for region in registry.values():
        assert RegionGrid.from_dict(region.to_dict()) == region


# --------------------------------------------------------------------------
# composites
# --------------------------------------------------------------------------
def _synthetic_stack() -> xr.Dataset:
    """Four frames in two sequences, with deliberate holes."""
    image = np.array(
        [
            [[1.0, np.nan], [3.0, 4.0]],
            [[3.0, 6.0], [np.nan, 8.0]],
            [[10.0, 10.0], [np.nan, np.nan]],
            [[20.0, np.nan], [np.nan, 30.0]],
        ],
        dtype=np.float32,
    )
    valid = np.isfinite(image)
    emission = np.array(
        [
            [[10.0, np.nan], [40.0, 20.0]],
            [[30.0, 5.0], [np.nan, 60.0]],
            [[70.0, 15.0], [np.nan, np.nan]],
            [[25.0, np.nan], [np.nan, 35.0]],
        ],
        dtype=np.float32,
    )
    times = pd.to_datetime(
        [
            "2017-02-02T11:40:03",
            "2017-02-02T11:40:33",
            "2017-02-02T11:48:10",
            "2017-02-02T11:48:40",
        ]
    ).to_numpy("datetime64[ns]")
    return xr.Dataset(
        data_vars={
            "image": (("time", "y", "x"), image),
            "valid": (("time", "y", "x"), valid),
            "emission": (("time", "y", "x"), emission),
        },
        coords={
            "time": ("time", times),
            "seq_id": ("time", np.array(["a", "a", "b", "b"])),
            "orbit": ("time", np.array([4, 4, 4, 4], dtype=np.int32)),
            "product_id": ("time", np.array(["p0", "p1", "p2", "p3"])),
            "y_km": ("y", np.array([0.0, 15.0])),
            "x_km": ("x", np.array([0.0, 15.0])),
        },
        attrs={"region": "synthetic", "km_per_px": 15.0, "band": "M", "level": "frame"},
    )


def test_composite_sequences_means_any_and_min():
    composite = composite_sequences(_synthetic_stack())
    assert list(np.asarray(composite["seq_id"].values).astype(str)) == ["a", "b"]
    assert list(np.asarray(composite["n_frames"].values)) == [2, 2]
    assert composite.attrs["level"] == "sequence"

    image = np.asarray(composite["image"].values)
    valid = np.asarray(composite["valid"].values)
    emission = np.asarray(composite["emission"].values)
    assert image[0] == pytest.approx(np.array([[2.0, 6.0], [3.0, 6.0]]))
    assert valid[0].all()
    assert emission[0] == pytest.approx(np.array([[10.0, 5.0], [40.0, 20.0]]))

    assert image[1][0] == pytest.approx(np.array([15.0, 10.0]))
    assert np.isnan(image[1][1, 0]) and image[1][1, 1] == pytest.approx(30.0)
    assert valid[1].tolist() == [[True, True], [False, True]]
    assert emission[1][0] == pytest.approx(np.array([25.0, 15.0]))
    assert np.isnan(emission[1][1, 0])

    times = pd.to_datetime(np.asarray(composite["time"].values))
    assert list(times) == [
        pd.Timestamp("2017-02-02T11:40:03"),
        pd.Timestamp("2017-02-02T11:48:10"),
    ]
    assert pd.Timestamp(np.asarray(composite["time_last"].values)[0]) == pd.Timestamp(
        "2017-02-02T11:40:33"
    )


# --------------------------------------------------------------------------
# constant-cadence runs
# --------------------------------------------------------------------------
def _axis(offsets: list[float]) -> np.ndarray:
    base = np.datetime64("2017-02-02T00:00:00", "ns")
    return base + (np.asarray(offsets) * 1e9).astype("timedelta64[ns]")


def test_runs_split_on_a_gap():
    times = _axis([0, 487, 974, 20000, 20487, 20974, 21461, 21948])
    assert constant_cadence_runs(times) == [(0, 2), (3, 7)]


def test_runs_reject_too_short():
    times = _axis([0, 487, 9000, 9487])
    assert constant_cadence_runs(times) == []
    # Two frames always have a trivially constant spacing, so with
    # ``min_frames=2`` even the long gap between the pairs is its own run.
    assert constant_cadence_runs(times, min_frames=2) == [(0, 1), (1, 2), (2, 3)]


def test_runs_tolerate_jitter_within_tolerance_only():
    inside = _axis([0, 487, 975, 1461, 1949])
    assert constant_cadence_runs(inside) == [(0, 4)]
    outside = _axis([0, 487, 975, 1461, 2400])
    assert constant_cadence_runs(outside) == [(0, 3)]


def test_runs_on_a_single_cadence():
    times = _axis([0, 30, 60, 90])
    assert constant_cadence_runs(times) == [(0, 3)]
    assert constant_cadence_runs(times, min_frames=5) == []


# --------------------------------------------------------------------------
# log gradient
# --------------------------------------------------------------------------
def test_log_gradient_of_a_known_ramp():
    slope = 0.25  # per pixel
    dx_m = 15000.0
    image = slope * np.arange(8, dtype=np.float64)[None, :] * np.ones((6, 1))
    result = log_gradient(image[None, ...], dx_m)
    assert result.shape == (1, 6, 8)
    assert result == pytest.approx(np.log10(slope / dx_m))


def test_log_gradient_floors_a_flat_image():
    result = log_gradient(np.zeros((1, 4, 4)), 1000.0)
    assert result == pytest.approx(np.log10(GRADIENT_FLOOR))


def test_log_gradient_diagonal_ramp():
    dx_m = 1000.0
    rows, cols = np.mgrid[0:5, 0:5]
    image = (3.0 * cols + 4.0 * rows).astype(np.float64)
    result = log_gradient(image, dx_m)
    assert result == pytest.approx(np.log10(5.0 / dx_m))


def test_export_writes_the_model_layout(tmp_path):
    stack = composite_sequences(_synthetic_stack())
    stack = stack.isel(time=[0, 1])
    times = _axis([0, 487, 974])
    stack = xr.concat([stack, stack.isel(time=[0])], dim="time").assign_coords(
        time=("time", times)
    )
    manifest = export_stack(stack, tmp_path, min_frames=3)
    assert manifest["n_realizations"] == 1
    assert (tmp_path / "spec.json").exists() and (tmp_path / "dataset_manifest.json").exists()
    written = xr.open_dataset(tmp_path / "r00000" / "realization.nc")
    try:
        assert written["image"].dims == ("frame", "y_img", "x_img")
        assert written["valid"].dtype == bool
        assert float(written.attrs["dx_img_m"]) == 15000.0
        assert float(written.attrs["dt_img_s"]) == pytest.approx(487.0, abs=1.0)
        assert written.attrs["units_velocity"] == "m s-1"
        valid = np.asarray(written["valid"].values)
        assert np.isfinite(np.asarray(written["loggrad"].values)[valid]).all()
        assert (np.asarray(written["image"].values)[~valid] == 0.0).all()
        assert (np.asarray(written["loggrad"].values)[~valid] == 0.0).all()
    finally:
        written.close()


def test_export_crop_to_valid_trims_to_the_common_footprint(tmp_path):
    rng = np.random.default_rng(9)
    valid = np.zeros((3, 8, 10), dtype=bool)
    valid[:, 2:6, 3:9] = True
    valid[0, 2, 3] = False  # one frame's hole shrinks nothing: the box is a box
    image = np.where(valid, rng.normal(size=(3, 8, 10)), np.nan).astype(np.float32)
    stack = xr.Dataset(
        data_vars={
            "image": (("time", "y", "x"), image),
            "valid": (("time", "y", "x"), valid),
        },
        coords={"time": ("time", _axis([0, 487, 974]))},
        attrs={"km_per_px": 15.0, "region": "synthetic", "band": "M", "level": "frame"},
    )
    export_stack(stack, tmp_path, min_frames=3, crop_to_valid=True)
    written = xr.open_dataset(tmp_path / "r00000" / "realization.nc")
    try:
        assert written["image"].shape == (3, 4, 6)
        assert list(np.asarray(written.attrs["crop_origin"])) == [2, 3]
        seen = np.asarray(written["valid"].values)
        assert np.isfinite(np.asarray(written["loggrad"].values)[seen]).all()
        assert (np.asarray(written["image"].values)[~seen] == 0.0).all()
    finally:
        written.close()


# --------------------------------------------------------------------------
# movies
# --------------------------------------------------------------------------
def _tiny_stack() -> xr.Dataset:
    rng = np.random.default_rng(7)
    image = rng.normal(size=(2, 32, 32)).astype(np.float32)
    image[:, :4, :4] = np.nan
    return xr.Dataset(
        data_vars={
            "image": (("time", "y", "x"), image),
            "valid": (("time", "y", "x"), np.isfinite(image)),
        },
        coords={
            "time": ("time", _axis([0, 487])),
            "seq_id": ("time", np.array(["a", "b"])),
        },
        attrs={"km_per_px": 15.0, "region": "tiny", "band": "M", "level": "sequence"},
    )


@pytest.mark.parametrize("suffix", [".mp4", ".gif"])
def test_movie_round_trips_through_imageio(tmp_path, suffix):
    import imageio.v3 as iio

    from jiram_catalog.movie import write_movie

    stack = _tiny_stack()
    summary = write_movie(stack, tmp_path / f"tiny{suffix}", fps=4.0)
    assert summary["frames"] == 2
    assert summary["bytes"] > 0
    assert sum(1 for _ in iio.imiter(tmp_path / f"tiny{suffix}")) == 2


def test_stretch_limits_ignore_nan():
    from jiram_catalog.movie import stretch_limits

    low, high = stretch_limits(_tiny_stack(), (1.0, 99.0))
    assert np.isfinite(low) and np.isfinite(high) and high > low
