"""Offline tests for the VICAR reader and the map grid (no kernels needed).

The one test that does need kernels -- the round trip of the inverse camera
model against the geometry engine's own per-pixel latitudes and longitudes --
skips itself when ``JIRAM_SKIP_GATES=1`` or when the mirror is not there.
"""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from jiram_catalog.config import mirror_root, paper_data_root
from jiram_catalog.reproject import PAPER_GRID, PolarStereo
from jiram_catalog.vicar import read_vicar, read_vicar_label

PAPER = paper_data_root()
TRACKER_DIR = PAPER / "JIRAM velocity_vectors at 45 km"


# --------------------------------------------------------------------------
# a synthetic VICAR writer, so the reader is tested against files whose
# contents are known exactly
# --------------------------------------------------------------------------
def write_vicar(
    path: Path,
    cube: np.ndarray,
    *,
    fmt: str = "REAL",
    intfmt: str = "LOW",
    realfmt: str = "RIEEE",
    nlb: int = 0,
    nbb: int = 0,
    org: str = "BSQ",
) -> Path:
    """Write ``cube`` (bands, lines, samples) as a VICAR file and return the path."""
    bands, lines, samples = cube.shape
    itemsize = cube.dtype.itemsize
    recsize = nbb + samples * itemsize
    lblsize = recsize * max(1, -(-512 // recsize))  # a whole number of records
    label = (
        f"LBLSIZE={lblsize}  FORMAT='{fmt}'  TYPE='IMAGE'  ORG='{org}'  "
        f"NL={lines}  NS={samples}  NB={bands}  NBB={nbb}  NLB={nlb}  "
        f"RECSIZE={recsize}  INTFMT='{intfmt}'  REALFMT='{realfmt}'  "
        f"HOST='X86-64'  TASK='SYNTH'  USER='test'  TASK='COPY'  USER='test'  "
        f"NOTE='a quoted value with = and spaces'  CORNERS=(1.0, 2.0, 3.0, 4.0)  "
    )
    blob = bytearray(label.encode("ascii").ljust(lblsize, b"\0"))
    blob += b"\xa5" * (nlb * recsize)
    for band in range(bands):
        for line in range(lines):
            blob += b"\x5a" * nbb
            blob += cube[band, line].tobytes()
    path.write_bytes(bytes(blob))
    return path


def test_read_vicar_synthetic_little_endian(tmp_path):
    cube = np.arange(2 * 3 * 5, dtype="<f4").reshape(2, 3, 5) / 7.0
    path = write_vicar(tmp_path / "two_bands.img", cube, nlb=2, nbb=8)
    data, label = read_vicar(path)
    assert data.shape == (2, 3, 5)
    assert np.array_equal(data, cube.astype(np.float64).astype(np.float32))
    assert label["NB"] == 2 and label["NLB"] == 2 and label["NBB"] == 8
    assert label["TASK"] == ["SYNTH", "COPY"]           # repeated keys become lists
    assert label["NOTE"] == "a quoted value with = and spaces"
    assert label["CORNERS"] == (1.0, 2.0, 3.0, 4.0)
    assert read_vicar_label(path)["FORMAT"] == "REAL"


def test_read_vicar_synthetic_big_endian_single_band(tmp_path):
    # numpy arithmetic returns native byte order, so convert after building.
    cube = (np.arange(4 * 6, dtype=np.float32).reshape(1, 4, 6) - 10.0).astype(">f4")
    assert cube.dtype.byteorder == ">"
    path = write_vicar(tmp_path / "one_band.img", cube, realfmt="IEEE")
    data, label = read_vicar(path)
    assert data.shape == (4, 6)                        # NB == 1 is squeezed away
    assert data.dtype.byteorder in ("=", "|")          # returned in native order
    assert np.array_equal(data, cube[0])
    assert label["NB"] == 1


@pytest.mark.parametrize(
    "fmt,dtype,intfmt",
    [("BYTE", "u1", "LOW"), ("HALF", "<i2", "LOW"), ("HALF", ">i2", "HIGH"),
     ("FULL", ">i4", "HIGH"), ("DOUB", "<f8", "LOW")],
)
def test_read_vicar_sample_types(tmp_path, fmt, dtype, intfmt):
    cube = np.arange(1 * 3 * 4, dtype=dtype).reshape(1, 3, 4)
    realfmt = "IEEE" if intfmt == "HIGH" else "RIEEE"
    path = write_vicar(tmp_path / f"{fmt}.img", cube, fmt=fmt, intfmt=intfmt, realfmt=realfmt)
    data, _ = read_vicar(path)
    assert np.array_equal(data, cube[0])


def test_read_vicar_rejects_vax_and_unknown_format(tmp_path):
    cube = np.arange(6, dtype="<f4").reshape(1, 2, 3)
    vax = write_vicar(tmp_path / "vax.img", cube, realfmt="VAX")
    with pytest.raises(ValueError, match="VAX"):
        read_vicar(vax)
    unknown = write_vicar(tmp_path / "odd.img", cube, fmt="COMP")
    with pytest.raises(ValueError, match="FORMAT"):
        read_vicar(unknown)


def test_read_vicar_truncated(tmp_path):
    cube = np.arange(30, dtype="<f4").reshape(1, 5, 6)
    path = write_vicar(tmp_path / "cut.img", cube)
    blob = path.read_bytes()
    path.write_bytes(blob[: len(blob) - 40])
    with pytest.raises(ValueError, match="truncated"):
        read_vicar(path)


# --------------------------------------------------------------------------
# the real files
# --------------------------------------------------------------------------
@pytest.mark.skipif(not PAPER.is_dir(), reason="paper data not mounted")
def test_read_published_map():
    image, label = read_vicar(PAPER / "n01_15km" / "n01a.map")
    assert image.shape == (3000, 3200)
    assert image.dtype == np.float32
    assert int((image != 0).sum()) == 197089
    assert label["IMAGE_TIME"] == "2017-02-02T11:40:03.870"
    assert label["PRODUCT_ID"] == "JIR_IMG_RDR_2017033T114006_V02"
    assert label["MPS"] == 15.0 and label["MPROJ"] == 4


@pytest.mark.skipif(not TRACKER_DIR.is_dir(), reason="paper data not mounted")
def test_read_tracker4_table():
    """A ``.tp4`` has a 1728-byte label, EOL labels after the data, and NL >> NS."""
    path = sorted(TRACKER_DIR.glob("*.tp4"))[0]
    data, label = read_vicar(path)
    print(f"{path.name}: shape {data.shape}")
    assert data.ndim == 2
    assert data.shape == (int(label["NL"]), int(label["NS"]))
    assert label["EOL"] == 1
    assert "EOL_LABELS" in label and label["LBLSIZE"] == 1728
    assert np.isfinite(data).all()


# --------------------------------------------------------------------------
# the map grid
# --------------------------------------------------------------------------
GRID = PolarStereo(
    pole_line=1500.0, pole_sample=1600.0, km_per_px=15.0, lon0_deg=42.0,
    clockwise=False, hemisphere="N", radius_km=66854.0,
)


@pytest.mark.parametrize("grid", [
    GRID,
    replace(GRID, clockwise=True),
    replace(GRID, hemisphere="S", lon0_deg=310.0),
    replace(GRID, projection="orthographic", equatorial_radius_km=71492.0),
    replace(GRID, projection="equidistant"),
    replace(GRID, projection="lambert"),
    PAPER_GRID,
])
def test_polar_stereo_round_trip(grid):
    rng = np.random.default_rng(20260904)
    colatitude = rng.uniform(0.0, 60.0, 1000)
    lat = 90.0 - colatitude if grid.hemisphere == "N" else colatitude - 90.0
    lon = rng.uniform(0.0, 360.0, 1000)
    back_lat, back_lon = grid.pixel_to_latlon(*grid.latlon_to_pixel(lat, lon))
    assert np.abs(back_lat - lat).max() < 1e-9
    difference = (back_lon - lon + 180.0) % 360.0 - 180.0
    assert np.abs(difference).max() < 1e-9


def test_pole_maps_to_pole_pixel():
    for lon in (0.0, 42.0, 199.0):
        line, sample = GRID.latlon_to_pixel(90.0, lon)
        assert line == pytest.approx(GRID.pole_line, abs=1e-9)
        assert sample == pytest.approx(GRID.pole_sample, abs=1e-9)


def test_lon0_meridian_runs_along_the_positive_sample_axis():
    line, sample = GRID.latlon_to_pixel(80.0, GRID.lon0_deg)
    assert line == pytest.approx(GRID.pole_line, abs=1e-9)
    assert sample > GRID.pole_sample
    # 90 deg further east is "up" (towards line 0) for a counter-clockwise grid.
    line_up, sample_up = GRID.latlon_to_pixel(80.0, GRID.lon0_deg + 90.0)
    assert line_up < GRID.pole_line
    assert sample_up == pytest.approx(GRID.pole_sample, abs=1e-9)


def test_handedness_flips_the_cross_track_coordinate():
    mirrored = replace(GRID, clockwise=True)
    lat, lon = 82.5, GRID.lon0_deg + 37.0
    line, sample = GRID.latlon_to_pixel(lat, lon)
    line_cw, sample_cw = mirrored.latlon_to_pixel(lat, lon)
    assert sample_cw == pytest.approx(sample, abs=1e-9)
    assert (line_cw - GRID.pole_line) == pytest.approx(-(line - GRID.pole_line), abs=1e-9)


def test_pixel_scale_is_true_at_the_pole():
    """One pixel step from the pole is ``km_per_px`` on the ground, for every law."""
    for grid in (GRID, replace(GRID, projection="orthographic", equatorial_radius_km=71492.0)):
        lat, _ = grid.pixel_to_latlon(grid.pole_line, grid.pole_sample + 1.0)
        radius = grid.radius_km
        assert np.radians(90.0 - lat) * radius == pytest.approx(grid.km_per_px, rel=1e-6)


def test_paper_grid_places_the_pole_on_a_pixel_centre():
    assert PAPER_GRID.hemisphere == "N"
    assert PAPER_GRID.pole_line == pytest.approx(1800.0, abs=0.05)
    assert PAPER_GRID.pole_sample == pytest.approx(1800.0, abs=0.05)
    assert PAPER_GRID.km_per_px == pytest.approx(15.0, rel=1e-3)


# --------------------------------------------------------------------------
# the inverse camera model, against the engine itself
# --------------------------------------------------------------------------
def test_project_to_pixels_inverts_the_geometry_engine():
    if os.environ.get("JIRAM_SKIP_GATES") == "1":
        pytest.skip("JIRAM_SKIP_GATES=1")
    mirror = mirror_root()
    if not (mirror / "spice").is_dir():
        pytest.skip(f"no SPICE kernels under {mirror}")
    from jiram_catalog.geometry import KernelSet, frame_geometry
    from jiram_catalog.reproject import project_to_pixels

    kernels = KernelSet.for_orbits(mirror, [4])
    try:
        geo = frame_geometry("2017-02-02T11:40:03.870", "M", kernels)
        rows, columns = np.nonzero(geo.on_planet)
        line, sample, visible = project_to_pixels(
            geo, geo.lat[geo.on_planet], geo.lon_east[geo.on_planet]
        )
    finally:
        kernels.unload()

    assert geo.on_planet.sum() > 50_000
    assert visible.all()
    assert np.abs(line - (rows + 1)).max() < 1e-3
    assert np.abs(sample - (columns + 1)).max() < 1e-3
