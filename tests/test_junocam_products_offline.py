"""Offline tests for the JunoCam products layer: outlines, grids, band axes.

Nothing here touches the archive or SPICE.  What it does test is every piece
of the layer that is a *rule* rather than a measurement -- how a swath's
on-planet mask becomes a footprint outline, when an outline counts as
overlapping a region, that a cropped grid still answers the same
``(lat, lon)`` as the grid it came from, and that a band axis survives the
NetCDF round trip and the statistics, index and filter code that read it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from jiram_catalog.junocam.geo import (
    MAX_VERTICES,
    MIN_VERTICES,
    coerce_geo,
    footprint_outline,
)
from jiram_catalog.junocam.stacks import (
    MAX_PIXEL_RATIO,
    NIGHT_INCIDENCE_DEG,
    _outline_box,
    night_masked_valid,
    outline_overlap,
    stack_output_path,
    tier_rank,
    window_grid,
)
from jiram_catalog.junocam.strips import MAX_PIXEL_KM
from jiram_catalog.regions import RegionGrid
from jiram_catalog.stacks import read_stack, write_stack
from jiram_catalog.stats2d import select_band, strip_statistics
from jiram_catalog.strips import (
    _coerce_index,
    ensure_instrument_columns,
    load_strips,
    migrate_index,
    write_strip,
)

BANDS = ("RED", "GREEN", "BLUE")


def _region() -> RegionGrid:
    return RegionGrid(
        name="test_pole",
        projection="polar_ortho",
        km_per_px=15.0,
        shape=(400, 400),
        hemisphere="N",
        pole_pixel=(200.0, 200.0),
        lon0_east_deg=90.0,
    )


# ---------------------------------------------------------------------------
# the footprint outline
# ---------------------------------------------------------------------------
def test_outline_rings_a_rectangular_mask_in_image_order():
    mask = np.zeros((20, 30), dtype=bool)
    mask[5:15, 4:24] = True
    latitude = np.tile(np.arange(20.0)[:, None], (1, 30))
    longitude = np.tile(np.arange(30.0)[None, :], (20, 1))
    lon, lat = footprint_outline(mask, latitude, longitude)
    assert lon.size == lat.size >= MIN_VERTICES
    # The ring walks the top edge left to right, then the bottom right to left.
    assert lon[0] == pytest.approx(4.0) and lat[0] == pytest.approx(5.0)
    assert lon[lon.size // 2 - 1] == pytest.approx(23.0)
    assert set(np.round(lat).astype(int)) == {5, 14}
    assert set(np.round(lon).astype(int)) <= set(range(4, 24))


def test_outline_follows_a_curved_swath_edge():
    """A ribbon whose edge moves with the column is not a bounding box."""
    rows, cols = 40, 60
    mask = np.zeros((rows, cols), dtype=bool)
    top = (8 + 6 * np.sin(np.linspace(0.0, np.pi, cols))).astype(int)
    for column in range(cols):
        mask[top[column] : top[column] + 12, column] = True
    latitude = np.tile(np.arange(float(rows))[:, None], (1, cols))
    longitude = np.tile(np.arange(float(cols))[None, :], (rows, 1))
    lon, lat = footprint_outline(mask, latitude, longitude)
    assert lat.min() == pytest.approx(float(top.min()))
    assert lat.max() == pytest.approx(float(top.max() + 11))
    # A bounding box would put every vertex on one of two rows; this must not.
    assert len(set(np.round(lat).astype(int))) > 4


def test_outline_is_thinned_to_the_contract_length():
    mask = np.ones((10, 500), dtype=bool)
    grid = np.tile(np.arange(500.0)[None, :], (10, 1))
    lon, lat = footprint_outline(mask, grid, grid)
    assert lon.size <= MAX_VERTICES and lon.size >= MIN_VERTICES


def test_outline_of_an_empty_mask_is_empty():
    mask = np.zeros((8, 8), dtype=bool)
    grid = np.zeros((8, 8))
    lon, lat = footprint_outline(mask, grid, grid)
    assert lon.size == 0 and lat.size == 0


def test_outline_falls_back_to_a_row_wise_trace_for_a_narrow_swath():
    """Three columns cannot make a ring; three rows can."""
    mask = np.zeros((20, 3), dtype=bool)
    mask[2:18, 0:2] = True
    latitude = np.tile(np.arange(20.0)[:, None], (1, 3))
    longitude = np.tile(np.arange(3.0)[None, :], (20, 1))
    lon, lat = footprint_outline(mask, latitude, longitude)
    assert lon.size >= MIN_VERTICES


def test_coerce_geo_gives_the_schema_its_types_and_array_columns():
    table = coerce_geo(
        pd.DataFrame(
            [
                {
                    "product_id": "JNCR_X",
                    "orbit": 4,
                    "level": "RDR",
                    "start_time": "2017-02-02T00:00:00",
                    "bands": "BLUE;GREEN;RED",
                    "geo_ok": True,
                    "fp_lon": np.array([1.0, 2.0], dtype=np.float32),
                    "fp_lat": np.array([3.0, 4.0], dtype=np.float32),
                }
            ]
        )
    )
    assert table["orbit"].dtype == "Int16" and table["geo_ok"].dtype == "boolean"
    assert isinstance(table["fp_lon"].iloc[0], np.ndarray)
    assert np.isnan(table["median_pixel_km"].iloc[0])


# ---------------------------------------------------------------------------
# selection geometry
# ---------------------------------------------------------------------------
def test_outline_overlap_counts_vertices_on_the_canvas():
    region = _region()
    # 89 N is 1250 km from the pole, well inside a canvas that reaches 3000.
    lat = np.full(12, 89.0)
    lon = np.linspace(0.0, 330.0, 12)
    assert outline_overlap(region, lon, lat) == 12
    away = np.full(12, -85.0)
    assert outline_overlap(region, lon, away) == 0


def test_outline_overlap_sees_a_footprint_that_swallows_the_region():
    """Every vertex outside the canvas is not a miss when the canvas is inside."""
    region = _region()
    # A ring at 60 N encloses the whole 400 x 400 canvas, which reaches only
    # about 21 degrees of colatitude from the pole.
    lon = np.linspace(0.0, 355.0, 72)
    lat = np.full(lon.size, 60.0)
    row, col, visible = region.latlon_to_pixel(lat, lon)
    assert not bool((visible & region.inside(row, col)).any())
    assert outline_overlap(region, lon, lat) > 0


def test_outline_box_is_clipped_to_the_canvas():
    region = _region()
    lon = np.linspace(0.0, 355.0, 72)
    lat = np.full(lon.size, 70.0)
    box = _outline_box(region, lon, lat)
    assert box is not None
    top, bottom, left, right = box
    assert 0 <= top < bottom <= region.rows and 0 <= left < right <= region.cols


def test_tier_rank_orders_the_tiers_and_defaults_to_the_worst():
    assert tier_rank("A") < tier_rank("B") < tier_rank("C") < tier_rank("?")


def test_window_grid_keeps_the_same_ground_point():
    region = _region()
    window = (100, 300, 120, 340)
    cropped = window_grid(region, window)
    assert cropped.shape == (200, 220)
    lat, lon = 82.0, 47.0
    row, col, visible = region.latlon_to_pixel(lat, lon)
    row2, col2, visible2 = cropped.latlon_to_pixel(lat, lon)
    assert bool(visible) and bool(visible2)
    assert float(row2) == pytest.approx(float(row) - window[0])
    assert float(col2) == pytest.approx(float(col) - window[2])
    # And the axes still measure from the pole, so the crop is a view.
    assert float(cropped.x_km(0)) == pytest.approx(float(region.x_km(window[2])))


def test_stack_output_path_names_the_bands_and_the_orbits(tmp_path):
    path = stack_output_path(tmp_path, "north_pole_paper", BANDS, "4,5", "frame")
    assert path.name == "junocam_RED-GREEN-BLUE_orbits4_5_frame.nc"
    assert path.parent.name == "north_pole_paper"


def test_the_pixel_ratio_cut_is_a_documented_constant():
    """It is the one cut the JunoCam selector makes that the JIRAM one does not."""
    assert 1.0 < MAX_PIXEL_RATIO < 100.0


# ---------------------------------------------------------------------------
# the 2026-09-07 photometry amendment: night masking and the scale cutoffs
# ---------------------------------------------------------------------------
def test_the_two_scale_cutoffs_keep_the_products_maps_rather_than_postcards():
    """A stack may upsample a little; the strip library wants close swaths.

    Both numbers are what the amendment measured against: at twenty-five the
    orbit-4 polar stack was built out of whole-disk views at 578 km a pixel,
    and at no cut at all the median strip of the same pass was a 200 km
    picture of Jupiter rather than a map of anything on it.
    """
    assert MAX_PIXEL_RATIO <= 3.0
    assert MAX_PIXEL_KM <= 30.0


def test_night_masked_valid_needs_one_band_that_is_both_painted_and_lit():
    """Painted is not enough, one lit band is, and NaN illumination is not lit."""
    image = np.array(
        [
            [[1.0, 2.0, 3.0, 4.0, np.nan]],
            [[1.0, 2.0, 3.0, np.nan, np.nan]],
        ]
    )
    incidence = np.array(
        [
            [[10.0, 100.0, np.nan, 10.0, 10.0]],
            [[100.0, 100.0, 20.0, 10.0, 10.0]],
        ]
    )
    valid = night_masked_valid(image, incidence)
    #   lit in band 0 | night in both | band 0's angle is NaN, band 1's is lit
    #                 | painted and lit in band 0 only | painted in neither
    assert valid.tolist() == [[True, False, True, True, False]]
    assert valid.shape == image.shape[1:]
    # The angle is a parameter, and the default is the module's constant.
    assert not night_masked_valid(image, incidence, night_deg=5.0).any()
    assert NIGHT_INCIDENCE_DEG == 88.0


def test_night_masked_valid_rejects_a_pixel_the_terminator_has_passed():
    """Eighty-eight degrees, not ninety: the last two degrees are not a signal."""
    image = np.ones((1, 1, 3))
    incidence = np.array([[[87.0, 88.5, 89.5]]])
    assert night_masked_valid(image, incidence).tolist() == [[True, False, False]]


# ---------------------------------------------------------------------------
# the band axis through NetCDF, statistics and the index
# ---------------------------------------------------------------------------
def _band_stack(steps: int = 2, size: int = 16) -> xr.Dataset:
    rng = np.random.default_rng(3)
    image = rng.normal(1.0, 0.1, (steps, 3, size, size)).astype("float32")
    return xr.Dataset(
        data_vars={
            "image": (("time", "band", "y", "x"), image),
            "valid": (("time", "y", "x"), np.ones((steps, size, size), dtype=bool)),
            "emission": (("time", "band", "y", "x"), image * 0 + 45.0),
            "n_frames": (("time", "band", "y", "x"), np.ones_like(image, dtype="uint8")),
        },
        coords={
            "time": pd.to_datetime(["2017-02-02T00:00", "2017-02-02T00:01"])[:steps],
            "band": ("band", np.array(BANDS)),
            "x_km": ("x", np.arange(float(size))),
            "y_km": ("y", np.arange(float(size))),
            "dt_refined_s": ("time", np.array([0.01, np.nan][:steps], dtype="float32")),
        },
        attrs={"region": "test", "instrument": "JunoCam", "bands": ";".join(BANDS),
               "band": ";".join(BANDS), "level": "frame", "km_per_px": 15.0},
    )


def test_write_stack_round_trips_a_band_dimension(tmp_path):
    dataset = _band_stack()
    target = write_stack(dataset, tmp_path / "regions" / "test" / "junocam_x_orbits4_frame.nc")
    with read_stack(target) as stored:
        assert stored["image"].dims == ("time", "band", "y", "x")
        assert list(stored["band"].values) == list(BANDS)
        assert stored["valid"].dims == ("time", "y", "x")
        assert stored.attrs["instrument"] == "JunoCam"
        np.testing.assert_allclose(
            stored["image"].values, dataset["image"].values, rtol=1e-6
        )
        # Every map-shaped variable is deflated, chunked one plane at a time.
        encoding = stored["image"].encoding
        assert encoding["zlib"] and tuple(encoding["chunksizes"])[:2] == (1, 1)


def test_write_stack_leaves_a_band_less_stack_alone(tmp_path):
    """The JIRAM path must be untouched: three variables, one chunk per step."""
    size = 8
    dataset = xr.Dataset(
        data_vars={
            "image": (("time", "y", "x"), np.zeros((2, size, size), dtype="float32")),
            "valid": (("time", "y", "x"), np.ones((2, size, size), dtype=bool)),
            "emission": (("time", "y", "x"), np.zeros((2, size, size), dtype="float32")),
        },
        coords={"time": pd.to_datetime(["2017-02-02", "2017-02-03"])},
        attrs={"band": "M", "level": "frame"},
    )
    target = write_stack(dataset, tmp_path / "M_orbits4_frame.nc")
    with read_stack(target) as stored:
        assert stored["image"].dims == ("time", "y", "x")
        assert tuple(stored["image"].encoding["chunksizes"]) == (1, size, size)


def _band_strip(strip_id: str = "JNCR_TEST", size: int = 24) -> xr.Dataset:
    rng = np.random.default_rng(5)
    image = rng.normal(1.0, 0.05, (3, size, size)).astype("float32")
    image[1] *= 2.0
    return xr.Dataset(
        data_vars={
            "image": (("band", "y", "x"), image),
            "valid": (("y", "x"), np.ones((size, size), dtype=bool)),
            "emission": (("band", "y", "x"), image * 0 + 40.0),
            "n_frames": (("band", "y", "x"), np.ones_like(image, dtype="uint8")),
        },
        coords={
            "band": ("band", np.array(BANDS)),
            "x_km": ("x", np.arange(float(size))),
            "y_km": ("y", np.arange(float(size))),
            "lat": (("y", "x"), np.full((size, size), 80.0, dtype="float32")),
            "lon_east": (("y", "x"), np.full((size, size), 30.0, dtype="float32")),
            "local_time_h": (("y", "x"), np.full((size, size), 12.0, dtype="float32")),
        },
        attrs={
            "strip_id": strip_id,
            "instrument": "JunoCam",
            "band": ";".join(BANDS),
            "bands": ";".join(BANDS),
            "km_per_px": 10.0,
            "orbit": 4,
            "valid_frac": 1.0,
        },
    )


def test_select_band_defaults_to_the_first_and_rejects_an_unknown_one():
    strip = _band_strip()
    first, name = select_band(strip)
    assert name == "RED" and first["image"].dims == ("y", "x")
    _, green = select_band(strip, "green")
    assert green == "GREEN"
    with pytest.raises(KeyError):
        select_band(strip, "PURPLE")


def test_strip_statistics_of_two_bands_differ_and_name_their_band():
    strip = _band_strip()
    red = strip_statistics(strip, band="RED")
    green = strip_statistics(strip, band="GREEN")
    assert red.attrs["band"] == "RED" and green.attrs["band"] == "GREEN"
    assert "k" in red.coords and red["E"].size > 5
    # GREEN is RED's field scaled by two, so its variance is the larger by
    # about four; the two bands are genuinely separate measurements.
    assert green.attrs["variance"] == pytest.approx(4.0 * red.attrs["variance"], rel=0.1)


def test_strip_statistics_still_works_without_a_band_axis():
    strip = _band_strip().isel(band=0)
    result = strip_statistics(strip)
    assert "k" in result.coords


def test_write_strip_deflates_a_banded_variable(tmp_path):
    path = write_strip(_band_strip(), tmp_path / "strips" / "junocam" / "orbit04" / "s.nc")
    with xr.open_dataset(path) as stored:
        assert stored["image"].dims == ("band", "y", "x")
        assert stored["image"].encoding["zlib"]


# ---------------------------------------------------------------------------
# the shared strip index
# ---------------------------------------------------------------------------
def _legacy_index() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "strip_id": ["04_M_2017033T114003_00", "04_L_2017033T114003_00"],
            "orbit": [4, 4],
            "band": ["M", "L"],
            "path": ["strips/orbit04/a.nc", "strips/orbit04/b.nc"],
            "chunk_index": [0, 0],
            "n_frames": [7, 7],
            "rows": [100, 100],
            "cols": [100, 100],
            "lat_min": [70.0, 70.0],
            "lat_max": [86.0, 86.0],
            "time_start": pd.to_datetime(["2017-02-02", "2017-02-02"]),
            "time_end": pd.to_datetime(["2017-02-02", "2017-02-02"]),
            "time_mid": pd.to_datetime(["2017-02-02", "2017-02-02"]),
            "pole_inside": [False, False],
        }
    )


def test_the_migration_calls_an_old_index_jiram_and_copies_its_band():
    migrated = ensure_instrument_columns(_legacy_index())
    assert list(migrated["instrument"]) == ["JIRAM", "JIRAM"]
    assert list(migrated["bands"]) == ["M", "L"]


def test_migrate_index_rewrites_the_file_once_and_is_then_a_no_op(tmp_path):
    path = tmp_path / "strips" / "strips.parquet"
    path.parent.mkdir(parents=True)
    _legacy_index().to_parquet(path, index=False)
    assert migrate_index(tmp_path) == path
    first = pd.read_parquet(path)
    assert {"instrument", "bands"} <= set(first.columns)
    assert migrate_index(tmp_path) == path
    pd.testing.assert_frame_equal(first, pd.read_parquet(path))
    assert migrate_index(tmp_path / "empty") is None


def test_load_strips_filters_on_instrument_and_on_one_band_of_many(tmp_path):
    table = _coerce_index(_legacy_index())
    junocam = table.iloc[[0]].copy()
    junocam["strip_id"] = "JNCR_TEST_04C00001_V01"
    junocam["instrument"] = "JunoCam"
    junocam["band"] = ";".join(BANDS)
    junocam["bands"] = ";".join(BANDS)
    junocam["path"] = "strips/junocam/orbit04/JNCR_TEST_04C00001_V01.nc"
    combined = pd.concat([table, junocam], ignore_index=True)
    path = tmp_path / "strips" / "strips.parquet"
    path.parent.mkdir(parents=True)
    combined.to_parquet(path, index=False)

    assert len(load_strips(tmp_path)) == 3
    assert len(load_strips(tmp_path, instrument="JunoCam")) == 1
    assert len(load_strips(tmp_path, instrument="jiram")) == 2
    assert len(load_strips(tmp_path, band="M")) == 1
    assert len(load_strips(tmp_path, band="GREEN")) == 1
    assert len(load_strips(tmp_path, band="METHANE")) == 0
