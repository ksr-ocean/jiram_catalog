"""The strip library: one reprojected swath per pass, per sequence, per chunk.

Outside the poles JIRAM does not revisit anything.  A mid-latitude or
equatorial target is seen once, as a narrow swath laid down by one spin
sequence in a couple of minutes, and never again at a useful resolution.  A
time stack is the wrong product for that: there is no time axis to speak of,
only a single look.  The right product is a *strip* -- that one look,
reprojected onto its own tangent-plane grid at its own resolution, carrying
the mask and the geometry that say where it is and how obliquely it was seen
-- and a library of strips that can be queried by latitude band, epoch,
resolution and illumination.

Three decisions define a strip.  *Which frames belong together*: a spin
sequence can slew tens of degrees while it runs, so a sequence is split into
chunks wherever the boresight has walked more than
:data:`CHUNK_SEPARATION_DEG` from where the chunk started or the cadence has
broken for more than :data:`CHUNK_GAP_S`.  *What grid to use*: a
``local_ortho`` tangent plane centred on the chunk's own boresight centroid,
at the :data:`RESOLUTION_CLASSES` entry nearest in log to the chunk's median
pixel scale, sized to the bounding box the frames actually project onto.
Quantising the resolution is what makes the library comparable: two strips of
the same class can be put side by side in a spectrum or a structure function
without resampling either.  *What to store*: the mean of the contributing
frames' bilinear samples, the best (smallest) emission and incidence angle any
of them achieved, and enough provenance to go back to the frames.

Every chunk is independent, so the work is embarrassingly parallel and each
chunk is built end to end -- geometry, reprojection, NetCDF -- inside one
``spawn`` worker that furnsh'es its orbit's kernels once, exactly as
:mod:`jiram_catalog.stacks` does.  A chunk that raises is logged and skipped;
a strip library is a survey product and one bad pass must not cost the rest.
"""

from __future__ import annotations

import argparse
import json
import logging
import multiprocessing
import os
import re
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import spiceypy
import xarray as xr

from .geo import FULL_ARC_DEG, POLE_LATITUDE, frames_with_geo, great_circle_deg, longitude_arc
from .geometry import KernelSet, frame_geometry, normalise_epoch
from .pds import mirror_root
from .regions import RegionGrid
from .reproject import footprint_bbox, reproject_frame
from .stacks import _worker_kernels, image_path, read_frame_image

LOGGER = logging.getLogger(__name__)

__all__ = [
    "RESOLUTION_CLASSES",
    "add_subparser",
    "boresight_centroid",
    "build_strip",
    "chunk_table",
    "default_strip_jobs",
    "load_strips",
    "make_parser",
    "main",
    "read_strip",
    "resolution_class",
    "run",
    "strip_id",
    "strip_path",
    "strips_index_path",
    "unit_rows",
    "write_strip",
]

#: Quantised map scales (km per pixel).  A strip is stored at the class
#: nearest in log to its own median pixel scale, which is what lets two
#: strips of the same class be compared without resampling.
RESOLUTION_CLASSES: tuple[float, ...] = (
    2.0, 3.0, 5.0, 7.0, 10.0, 15.0, 20.0, 30.0, 50.0, 70.0, 100.0, 150.0, 200.0, 300.0,
)
#: Great-circle walk of the boresight, from where a chunk started, that ends it.
CHUNK_SEPARATION_DEG = 12.0
#: Cadence break within a sequence that ends a chunk (seconds).
CHUNK_GAP_S = 120.0
#: Quality cuts that define a unit row.
MIN_ON_PLANET_FRAC = 0.3
MAX_BORE_EMISSION = 75.0
#: Every second line and sample is enough to bound a frame's footprint.
EXTENT_STRIDE = 2
#: Slack around the projected bounding box, in map pixels.
EXTENT_PAD_PX = 8
#: Hard cap on either side of a strip canvas, in pixels.
MAX_SIDE_PX = 6000
#: A ``local_ortho`` centre exactly at a pole has no north direction; a
#: centroid is pulled back to this latitude rather than losing the chunk.
POLE_CLAMP_DEG = 89.99
#: NetCDF deflate level.
COMPRESSION_LEVEL = 4
#: ``%Y%j T %H%M%S`` inside a ``seq_id`` such as ``04_M_2017033T114003``.
_SEQ_TIME_RE = re.compile(r"\d{7}T\d{6}")


def default_strip_jobs() -> int:
    return min(8, os.cpu_count() or 1)


def _software() -> str:
    try:
        from importlib.metadata import version

        return f"jiram_catalog {version('jiram-catalog')}"
    except Exception:  # noqa: BLE001 - provenance must never break a build
        return "jiram_catalog"


# --------------------------------------------------------------------------
# naming
# --------------------------------------------------------------------------
def seq_time(seq_id: str) -> str:
    """The ``%Y%jT%H%M%S`` stamp inside a ``seq_id``."""
    text = str(seq_id)
    match = _SEQ_TIME_RE.search(text)
    return match.group(0) if match else text.rsplit("_", 1)[-1]


def strip_id(orbit: int, band: str, seq_id: str, chunk_index: int) -> str:
    """``NN_B_YYYYDDDTHHMMSS_CC``: orbit, band, sequence start, chunk."""
    return (
        f"{int(orbit):02d}_{str(band).upper()}_{seq_time(seq_id)}_"
        f"{int(chunk_index):02d}"
    )


def strips_index_path(mirror: str | Path | None = None) -> Path:
    """Where the library index lives."""
    return mirror_root(mirror) / "strips" / "strips.parquet"


def strip_path(mirror: str | Path | None, orbit: int, identifier: str) -> Path:
    """Where one strip's NetCDF lives."""
    return mirror_root(mirror) / "strips" / f"orbit{int(orbit):02d}" / f"{identifier}.nc"


# --------------------------------------------------------------------------
# selection and chunking (pure; no kernels)
# --------------------------------------------------------------------------
def unit_rows(
    mirror: str | Path | None,
    orbits: Iterable[int] | None,
    band: str,
    *,
    lat_band: tuple[float, float] | None = None,
) -> pd.DataFrame:
    """Frames of ``band`` whose geometry is good enough to build a strip from.

    The cuts are the survey's definition of a usable look: the geometry
    converged, the frame saw the planet over at least
    :data:`MIN_ON_PLANET_FRAC` of the detector, and the boresight emission
    angle is at most :data:`MAX_BORE_EMISSION`.  ``lat_band`` further restricts
    the rows by boresight latitude.
    """
    band = str(band).upper()
    if band not in ("L", "M"):
        raise ValueError(f"band must be 'L' or 'M', not {band!r}")
    table = frames_with_geo(mirror_root(mirror), orbits)
    table = table.loc[
        (table["half"] == band)
        & table["geo_ok"].fillna(False).astype(bool)
        & (table["on_planet_frac"] >= MIN_ON_PLANET_FRAC)
        & (table["bore_emission"] <= MAX_BORE_EMISSION)
    ]
    if lat_band is not None:
        low, high = float(lat_band[0]), float(lat_band[1])
        table = table.loc[(table["bore_lat"] >= low) & (table["bore_lat"] <= high)]
    return table.sort_values(["seq_id", "start_time"], kind="stable").reset_index(
        drop=True
    )


def chunk_table(
    table: pd.DataFrame, *, min_frames: int = 2
) -> tuple[list[tuple[int, pd.DataFrame]], int]:
    """Split unit rows into chunks: ``[(chunk_index, rows), ...]`` and a skip count.

    Within a ``seq_id``, rows are taken in time order; a chunk ends when the
    boresight has walked more than :data:`CHUNK_SEPARATION_DEG` from *the
    chunk's first row* (not from the previous row -- the test is on the total
    drift, so a slow slew still terminates a chunk) or when the gap to the
    previous row exceeds :data:`CHUNK_GAP_S`.  Chunks shorter than
    ``min_frames`` are dropped but still consume a ``chunk_index``, so that a
    strip's identity does not move when ``min_frames`` changes.
    """
    if table.empty:
        return [], 0
    kept: list[tuple[int, pd.DataFrame]] = []
    skipped = 0
    for _, part in table.groupby("seq_id", sort=True):
        part = part.sort_values("start_time", kind="stable")
        latitudes = part["bore_lat"].to_numpy(dtype=float)
        longitudes = part["bore_lon_east"].to_numpy(dtype=float)
        seconds = (
            pd.to_datetime(part["start_time"]).to_numpy("datetime64[ns]").astype("int64")
            / 1e9
        )
        start = 0
        bounds: list[tuple[int, int]] = []
        for position in range(1, len(part)):
            walk = float(
                great_circle_deg(
                    latitudes[start],
                    longitudes[start],
                    latitudes[position],
                    longitudes[position],
                )
            )
            gap = float(seconds[position] - seconds[position - 1])
            if walk > CHUNK_SEPARATION_DEG or gap > CHUNK_GAP_S:
                bounds.append((start, position))
                start = position
        bounds.append((start, len(part)))
        for index, (low, high) in enumerate(bounds):
            if high - low < min_frames:
                skipped += 1
                continue
            kept.append((index, part.iloc[low:high].reset_index(drop=True)))
    return kept, skipped


def resolution_class(
    km_per_px: float, classes: Sequence[float] = RESOLUTION_CLASSES
) -> float:
    """The class nearest in log to ``km_per_px``; ties go to the smaller class."""
    value = float(km_per_px)
    if not np.isfinite(value) or value <= 0.0:
        raise ValueError(f"km_per_px must be positive and finite, not {km_per_px!r}")
    scales = np.asarray(classes, dtype=np.float64)
    distance = np.abs(np.log(value) - np.log(scales))
    # ``argmin`` takes the first minimum and the classes ascend, so an exact
    # tie -- the geometric mean of two neighbours -- lands on the finer grid.
    return float(scales[int(np.argmin(distance))])


def boresight_centroid(lat, lon_east) -> tuple[float, float]:
    """Unit-vector mean of boresight directions, back as ``(lat, lon_east)``.

    Averaging the angles themselves would put a pass that straddles the
    antimeridian in the middle of the far side of the planet; averaging the
    unit vectors and re-deriving the angles cannot.
    """
    latitudes = np.radians(np.asarray(lat, dtype=np.float64).ravel())
    longitudes = np.radians(np.asarray(lon_east, dtype=np.float64).ravel())
    finite = np.isfinite(latitudes) & np.isfinite(longitudes)
    if not finite.any():
        raise ValueError("no finite boresight to average")
    latitudes, longitudes = latitudes[finite], longitudes[finite]
    vector = np.stack(
        [
            np.cos(latitudes) * np.cos(longitudes),
            np.cos(latitudes) * np.sin(longitudes),
            np.sin(latitudes),
        ],
        axis=-1,
    ).mean(axis=0)
    norm = float(np.linalg.norm(vector))
    if norm < 1e-12:
        raise ValueError("boresights average to the centre of the planet")
    x, y, z = vector / norm
    return (
        float(np.degrees(np.arctan2(z, np.hypot(x, y)))),
        float(np.degrees(np.arctan2(y, x)) % 360.0),
    )


# --------------------------------------------------------------------------
# the grid of one chunk
# --------------------------------------------------------------------------
def _side(low: float, high: float) -> tuple[int, int, bool]:
    """Pixel range ``[start, start + count)`` around ``[low, high]``, capped."""
    start = int(np.floor(low)) - EXTENT_PAD_PX
    stop = int(np.ceil(high)) + EXTENT_PAD_PX
    count = stop - start + 1
    if count <= MAX_SIDE_PX:
        return start, count, False
    middle = 0.5 * (low + high)
    return int(round(middle - MAX_SIDE_PX / 2.0)), MAX_SIDE_PX, True


def chunk_grid(
    identifier: str,
    center: tuple[float, float],
    km_per_px: float,
    geometries: Sequence[Any],
) -> tuple[RegionGrid, bool]:
    """The ``local_ortho`` grid that holds a chunk, and whether it was capped.

    The centre and the scale are fixed first, which makes the tangent plane
    well defined; the extent then follows from where the frames actually land
    on it.  Row index increases with local north and column with local east,
    which is what :class:`~jiram_catalog.regions.RegionGrid` already does for
    ``local_ortho``.
    """
    latitude = float(np.clip(center[0], -POLE_CLAMP_DEG, POLE_CLAMP_DEG))
    probe = RegionGrid(
        name=identifier,
        projection="local_ortho",
        km_per_px=float(km_per_px),
        shape=(1, 1),
        center=(latitude, float(center[1])),
        center_pixel=(0.0, 0.0),
    )
    row_low = col_low = np.inf
    row_high = col_high = -np.inf
    for geometry in geometries:
        on_planet = np.asarray(geometry.on_planet)[::EXTENT_STRIDE, ::EXTENT_STRIDE]
        if not on_planet.any():
            continue
        latitudes = np.asarray(geometry.lat)[::EXTENT_STRIDE, ::EXTENT_STRIDE][on_planet]
        longitudes = np.asarray(geometry.lon_east)[::EXTENT_STRIDE, ::EXTENT_STRIDE][
            on_planet
        ]
        row, col, visible = probe.latlon_to_pixel(latitudes, longitudes)
        good = visible & np.isfinite(row) & np.isfinite(col)
        if not good.any():
            continue
        row_low = min(row_low, float(row[good].min()))
        row_high = max(row_high, float(row[good].max()))
        col_low = min(col_low, float(col[good].min()))
        col_high = max(col_high, float(col[good].max()))
    if not np.isfinite(row_low) or not np.isfinite(col_low):
        raise ValueError(f"{identifier}: no on-planet pixel projects onto the plane")

    row_start, rows, row_capped = _side(row_low, row_high)
    col_start, cols, col_capped = _side(col_low, col_high)
    grid = RegionGrid(
        name=identifier,
        projection="local_ortho",
        km_per_px=float(km_per_px),
        shape=(rows, cols),
        center=(latitude, float(center[1])),
        center_pixel=(-float(row_start), -float(col_start)),
        description="JIRAM strip",
    )
    return grid, bool(row_capped or col_capped)


def _lat_lon_local_time(
    grid: RegionGrid, subsolar_lon_east: float, block: int = 512
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """2-D latitude, east longitude and local time of a strip canvas.

    Computed in row blocks: a capped 6000 x 6000 canvas would otherwise ask for
    several gigabytes of float64 intermediates at once.
    """
    latitude = np.empty(grid.shape, dtype=np.float32)
    longitude = np.empty(grid.shape, dtype=np.float32)
    for start in range(0, grid.rows, block):
        stop = min(start + block, grid.rows)
        band_lat, band_lon = grid.lat_lon_grids((start, stop, 0, grid.cols))
        latitude[start:stop] = band_lat.astype(np.float32)
        longitude[start:stop] = band_lon.astype(np.float32)
    with np.errstate(invalid="ignore"):
        local_time = (
            (longitude.astype(np.float64) - subsolar_lon_east) / 15.0 + 12.0
        ) % 24.0
    return latitude, longitude, local_time.astype(np.float32)


def _subsolar_lon_east(et: float, abcorr: str = "LT+S") -> float:
    """Planetocentric east longitude of the sub-solar point (degrees)."""
    spoint, _, _ = spiceypy.subslr(
        "NEAR POINT/ELLIPSOID", "JUPITER", et, "IAU_JUPITER", abcorr, "JUNO"
    )
    point = np.asarray(spoint, dtype=np.float64)
    return float(np.degrees(np.arctan2(point[1], point[0])) % 360.0)


# --------------------------------------------------------------------------
# building one strip
# --------------------------------------------------------------------------
def build_strip(
    mirror: str | Path | None,
    rows: pd.DataFrame,
    *,
    band: str,
    chunk_index: int,
    kernels: KernelSet | None = None,
) -> xr.Dataset:
    """Reproject one chunk of frames onto its own grid.

    ``rows`` is a chunk as :func:`chunk_table` returns it; the orbit's kernels
    must already be furnsh'd (``kernels`` is carried only so that a caller's
    context manager is visibly alive).  Frames whose image is not mirrored are
    dropped, so a partially mirrored orbit yields a shorter strip rather than
    nothing at all.
    """
    root = mirror_root(mirror)
    band = str(band).upper()
    rows = rows.sort_values("start_time", kind="stable").reset_index(drop=True)
    if rows.empty:
        raise ValueError("cannot build a strip from an empty chunk")
    orbit = int(rows["orbit_dir"].iloc[0])
    sequence = str(rows["seq_id"].iloc[0])
    identifier = strip_id(orbit, band, sequence, chunk_index)

    present = np.array(
        [image_path(root, orbit, str(pid)).is_file() for pid in rows["product_id"]]
    )
    if not present.any():
        raise FileNotFoundError(f"{identifier}: no frame of the chunk is mirrored")
    if not present.all():
        LOGGER.warning(
            "%s: %d of %d frames are not mirrored and are dropped",
            identifier,
            int((~present).sum()),
            len(present),
        )
        rows = rows.loc[present].reset_index(drop=True)

    epochs = [normalise_epoch(value) for value in rows["start_time"]]
    geometries = [frame_geometry(epoch, band, kernels) for epoch in epochs]

    center = boresight_centroid(rows["bore_lat"], rows["bore_lon_east"])
    km_per_px = resolution_class(float(np.median(rows["median_pixel_km"])))
    grid, capped = chunk_grid(identifier, center, km_per_px, geometries)
    shape = (grid.rows, grid.cols)
    canvas = grid.canvas_grid()

    total = np.zeros(shape, dtype=np.float64)
    counts = np.zeros(shape, dtype=np.int32)
    emission = np.full(shape, np.inf, dtype=np.float32)
    incidence = np.full(shape, np.inf, dtype=np.float32)
    frame_index = np.full(shape, -1, dtype=np.int16)

    kept: list[int] = []
    for position, geometry in enumerate(geometries):
        box = footprint_bbox(geometry, canvas, shape)
        if box is None:
            continue
        top, bottom, left, right = box
        window = (bottom - top, right - left)
        patch = grid.canvas_grid(top, left)
        image = read_frame_image(
            image_path(root, orbit, str(rows["product_id"].iloc[position])), band
        )
        values, weight = reproject_frame(image, geometry, patch, window)
        painted = weight > 0.0
        if not painted.any():
            continue
        index = len(kept)
        kept.append(position)

        view = (slice(top, bottom), slice(left, right))
        total[view] += np.where(painted, values, 0.0)
        counts[view] += painted
        frame_index[view] = np.where(painted, np.int16(index), frame_index[view])
        for source, target in (
            (geometry.emission, emission),
            (geometry.incidence, incidence),
        ):
            angles, angle_weight = reproject_frame(
                np.asarray(source, dtype=np.float64), geometry, patch, window
            )
            good = painted & (angle_weight > 0.0)
            target[view] = np.where(
                good, np.minimum(target[view], angles.astype(np.float32)), target[view]
            )

    if not kept:
        raise ValueError(f"{identifier}: no frame painted a pixel of the canvas")

    valid = counts > 0
    with np.errstate(invalid="ignore"):
        picture = np.where(valid, total / np.maximum(counts, 1), np.nan).astype(np.float32)
    emission = np.where(np.isfinite(emission), emission, np.nan).astype(np.float32)
    incidence = np.where(np.isfinite(incidence), incidence, np.nan).astype(np.float32)

    selected = rows.iloc[kept].reset_index(drop=True)
    times = pd.to_datetime(selected["start_time"])
    start, end = times.min(), times.max()
    middle = (start + (end - start) / 2).floor("us")
    subsolar = _subsolar_lon_east(float(spiceypy.str2et(normalise_epoch(middle))))
    latitude, longitude, local_time = _lat_lon_local_time(grid, subsolar)

    lat_valid = latitude[valid].astype(np.float64)
    lon_valid = longitude[valid].astype(np.float64)
    lat_min = float(np.nanmin(lat_valid))
    lat_max = float(np.nanmax(lat_valid))
    lon_min, lon_max, lon_span = longitude_arc(lon_valid)
    pole_inside = bool(
        lat_max > POLE_LATITUDE or lat_min < -POLE_LATITUDE or lon_span >= FULL_ARC_DEG
    )
    if pole_inside:
        lon_min, lon_max, lon_span = 0.0, 360.0, 360.0
    with np.errstate(invalid="ignore"):
        dayside = incidence[valid]
        dayside_frac = float(np.mean(np.nan_to_num(dayside, nan=180.0) < 90.0))
        median_emission = float(np.nanmedian(emission[valid])) if valid.any() else np.nan

    dataset = xr.Dataset(
        data_vars={
            "image": (("y", "x"), picture),
            "valid": (("y", "x"), valid),
            "emission": (("y", "x"), emission),
            "incidence": (("y", "x"), incidence),
            "n_frames": (("y", "x"), np.minimum(counts, 255).astype(np.uint8)),
            "frame_index": (("y", "x"), frame_index),
        },
        coords={
            "y_km": ("y", grid.y_km(np.arange(grid.rows))),
            "x_km": ("x", grid.x_km(np.arange(grid.cols))),
            "lat": (("y", "x"), latitude),
            "lon_east": (("y", "x"), longitude),
            "local_time_h": (("y", "x"), local_time),
            "product_ids": ("frame", selected["product_id"].astype(str).to_numpy()),
            "frame_times": ("frame", times.to_numpy("datetime64[ns]")),
        },
        attrs={
            "strip_id": identifier,
            "orbit": int(orbit),
            "seq_id": sequence,
            "chunk_index": int(chunk_index),
            "band": band,
            "n_frames": int(len(selected)),
            "time_start": normalise_epoch(start),
            "time_end": normalise_epoch(end),
            "time_mid": normalise_epoch(middle),
            "center_lat": float(grid.center[0]),
            "center_lon_east": float(grid.center[1]),
            "km_per_px": float(km_per_px),
            "resolution_class": float(km_per_px),
            "projection": json.dumps(grid.to_dict(), sort_keys=True),
            "valid_frac": float(valid.mean()),
            "dayside_frac": dayside_frac,
            "median_emission": median_emission,
            "lat_min": lat_min,
            "lat_max": lat_max,
            "lon_min_east": float(lon_min),
            "lon_max_east": float(lon_max),
            "lon_span_deg": float(lon_span),
            "pole_inside": int(pole_inside),
            "capped": int(capped),
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "software": _software(),
        },
    )
    dataset["image"].attrs.update(
        long_name="mean reprojected radiance", units="W m-2 sr-1 um-1"
    )
    dataset["valid"].attrs.update(long_name="painted by at least one frame")
    dataset["emission"].attrs.update(long_name="best emission angle", units="degree")
    dataset["incidence"].attrs.update(long_name="best incidence angle", units="degree")
    dataset["n_frames"].attrs.update(long_name="contributing frames")
    dataset["frame_index"].attrs.update(
        long_name="index into product_ids of the last contributing frame"
    )
    dataset["y_km"].attrs.update(units="km", long_name="local north offset")
    dataset["x_km"].attrs.update(units="km", long_name="local east offset")
    dataset["lat"].attrs.update(units="degree_north", long_name="planetocentric latitude")
    dataset["lon_east"].attrs.update(units="degree_east")
    dataset["local_time_h"].attrs.update(units="hour", long_name="local solar time")
    return dataset


# --------------------------------------------------------------------------
# NetCDF I/O and the library index
# --------------------------------------------------------------------------
#: Attributes of a strip, in the order the index carries them.
INDEX_ATTRS: tuple[str, ...] = (
    "strip_id", "orbit", "seq_id", "chunk_index", "band", "n_frames",
    "time_start", "time_end", "time_mid", "center_lat", "center_lon_east",
    "km_per_px", "resolution_class", "projection", "valid_frac", "dayside_frac",
    "median_emission", "lat_min", "lat_max", "lon_min_east", "lon_max_east",
    "lon_span_deg", "pole_inside", "created_utc", "software",
)
INDEX_COLUMNS: tuple[str, ...] = (*INDEX_ATTRS, "path", "rows", "cols")


def write_strip(dataset: xr.Dataset, path: str | Path) -> Path:
    """Write one strip as deflated NetCDF4, NaN-filled."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoding: dict[str, dict[str, Any]] = {}
    for name in list(dataset.data_vars) + ["lat", "lon_east", "local_time_h"]:
        variable = dataset[name]
        if variable.dims != ("y", "x"):
            continue
        encoding[name] = {"zlib": True, "complevel": COMPRESSION_LEVEL}
        if variable.dtype.kind == "f":
            encoding[name].update({"dtype": "float32", "_FillValue": np.float32(np.nan)})
    temporary = target.with_suffix(".nc.tmp")
    dataset.to_netcdf(temporary, format="NETCDF4", engine="netcdf4", encoding=encoding)
    temporary.replace(target)
    return target


def index_row(dataset: xr.Dataset, path: str | Path, root: str | Path) -> dict[str, Any]:
    """The library-index row of a strip that has just been written."""
    relative = Path(path).relative_to(Path(root))
    row = {name: dataset.attrs.get(name) for name in INDEX_ATTRS}
    row["path"] = str(relative)
    row["rows"] = int(dataset.sizes["y"])
    row["cols"] = int(dataset.sizes["x"])
    return row


def _coerce_index(table: pd.DataFrame) -> pd.DataFrame:
    """Give the index its natural dtypes: real timestamps, a real boolean."""
    table = table.copy()
    for name in ("time_start", "time_end", "time_mid"):
        if name in table.columns and table[name].dtype.kind != "M":
            table[name] = pd.to_datetime(table[name], errors="coerce")
    if "pole_inside" in table.columns:
        table["pole_inside"] = table["pole_inside"].astype(bool)
    for name in ("orbit", "chunk_index", "n_frames", "rows", "cols"):
        if name in table.columns:
            table[name] = table[name].astype("int64")
    return table


def load_index(mirror: str | Path | None = None) -> pd.DataFrame:
    """Read the library index."""
    path = strips_index_path(mirror)
    if not path.exists():
        raise FileNotFoundError(
            f"strip index not found: {path}; run: jiram-catalog strips --orbits ..."
        )
    return pd.read_parquet(path)


def read_strip(mirror: str | Path | None, strip: str) -> xr.Dataset:
    """Open a strip by ``strip_id`` or by path (absolute, or relative to ``mirror``)."""
    root = mirror_root(mirror)
    text = str(strip)
    if text.endswith(".nc") or "/" in text:
        path = Path(text)
        if not path.is_absolute():
            path = root / path
    else:
        index = load_index(root)
        match = index.loc[index["strip_id"].astype(str) == text]
        if match.empty:
            raise ValueError(f"unknown strip_id: {text!r}")
        path = root / str(match["path"].iloc[0])
    return xr.open_dataset(path, engine="netcdf4")


def load_strips(
    mirror: str | Path | None = None,
    *,
    orbits: Iterable[int] | None = None,
    band: str | None = None,
    lat_min: float | None = None,
    lat_max: float | None = None,
    time_min: Any = None,
    time_max: Any = None,
    resolution_max_km: float | None = None,
    min_valid_frac: float | None = None,
    dayside_min: float | None = None,
) -> pd.DataFrame:
    """The library index, filtered.

    Latitude and time are *overlap* filters -- a strip is returned when its own
    span meets the query's span, which is what a coverage question actually
    asks -- while resolution, valid fraction and dayside fraction are
    thresholds on the strip's own scalar.
    """
    table = _coerce_index(load_index(mirror))
    if orbits is not None:
        table = table.loc[table["orbit"].isin(sorted({int(v) for v in orbits}))]
    if band is not None:
        table = table.loc[table["band"].astype(str) == str(band).upper()]
    if lat_min is not None:
        table = table.loc[table["lat_max"] >= float(lat_min)]
    if lat_max is not None:
        table = table.loc[table["lat_min"] <= float(lat_max)]
    if time_min is not None:
        table = table.loc[table["time_end"] >= pd.Timestamp(time_min)]
    if time_max is not None:
        table = table.loc[table["time_start"] <= pd.Timestamp(time_max)]
    if resolution_max_km is not None:
        table = table.loc[table["km_per_px"] <= float(resolution_max_km)]
    if min_valid_frac is not None:
        table = table.loc[table["valid_frac"] >= float(min_valid_frac)]
    if dayside_min is not None:
        table = table.loc[table["dayside_frac"] >= float(dayside_min)]
    return table.reset_index(drop=True)


def update_index(
    mirror: str | Path | None,
    rows: Sequence[dict[str, Any]],
    orbits: Sequence[int],
    band: str,
) -> Path:
    """Replace the selected orbits' rows of one band, keeping every other row.

    The replacement is per ``(orbit, band)``, not per orbit: the two bands are
    built by separate runs, and a run of one band must not delete the other's
    strips.  Files of the rows that go are deleted, as are any orphans left in
    the orbit's directory by an earlier run whose index row is gone.
    """
    root = mirror_root(mirror)
    path = strips_index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    band = str(band).upper()
    selected = sorted({int(value) for value in orbits})
    fresh = _coerce_index(pd.DataFrame(list(rows), columns=list(INDEX_COLUMNS)))

    kept = fresh
    if path.exists():
        existing = _coerce_index(pd.read_parquet(path))
        replaced = existing["orbit"].isin(selected) & (
            existing["band"].astype(str) == band
        )
        keep_ids = set(fresh["strip_id"].astype(str))
        for relative in existing.loc[replaced, "path"].astype(str):
            if Path(relative).stem not in keep_ids:
                (root / relative).unlink(missing_ok=True)
        kept = pd.concat([existing.loc[~replaced], fresh], ignore_index=True)

    written = set(fresh["path"].astype(str))
    for orbit in selected:
        directory = root / "strips" / f"orbit{orbit:02d}"
        for candidate in directory.glob(f"{orbit:02d}_{band}_*.nc"):
            if str(candidate.relative_to(root)) not in written:
                candidate.unlink(missing_ok=True)

    kept = kept.sort_values(
        ["orbit", "band", "time_start", "strip_id"], kind="stable"
    ).reset_index(drop=True)
    temporary = path.with_suffix(".parquet.tmp")
    kept.to_parquet(temporary, index=False)
    temporary.replace(path)
    return path


# --------------------------------------------------------------------------
# workers
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _Task:
    """One chunk, flattened to something cheap to pickle."""

    mirror: str
    orbit: int
    band: str
    chunk_index: int
    strip_id: str
    records: tuple[tuple[Any, ...], ...]
    columns: tuple[str, ...]

    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(list(self.records), columns=list(self.columns))


#: Columns a worker needs to rebuild its chunk.
TASK_COLUMNS: tuple[str, ...] = (
    "product_id",
    "orbit_dir",
    "seq_id",
    "start_time",
    "bore_lat",
    "bore_lon_east",
    "median_pixel_km",
)


def _make_task(
    root: Path, band: str, chunk_index: int, rows: pd.DataFrame
) -> _Task:
    orbit = int(rows["orbit_dir"].iloc[0])
    subset = rows.loc[:, list(TASK_COLUMNS)]
    records = tuple(
        tuple(value for value in record) for record in subset.itertuples(index=False)
    )
    return _Task(
        mirror=str(root),
        orbit=orbit,
        band=band,
        chunk_index=chunk_index,
        strip_id=strip_id(orbit, band, str(rows["seq_id"].iloc[0]), chunk_index),
        records=records,
        columns=TASK_COLUMNS,
    )


def _strip_task(task: _Task) -> dict[str, Any]:
    """Build and write one strip; a failure comes back as a message, not a raise."""
    try:
        kernels = _worker_kernels(task.mirror, task.orbit)
        dataset = build_strip(
            task.mirror,
            task.frame(),
            band=task.band,
            chunk_index=task.chunk_index,
            kernels=kernels,
        )
        path = write_strip(
            dataset, strip_path(task.mirror, task.orbit, task.strip_id)
        )
        row = index_row(dataset, path, task.mirror)
        return {
            "strip_id": task.strip_id,
            "row": row,
            "capped": bool(dataset.attrs.get("capped", 0)),
            "pixels": int(dataset.sizes["y"] * dataset.sizes["x"]),
            "n_frames": int(dataset.attrs["n_frames"]),
            "km_per_px": float(dataset.attrs["km_per_px"]),
        }
    except Exception as exc:  # noqa: BLE001 - one bad chunk must not abort a survey
        return {
            "strip_id": task.strip_id,
            "error": f"{type(exc).__name__}: {' '.join(str(exc).split())[:400]}",
        }


def _results(tasks: Sequence[_Task], jobs: int) -> Iterator[dict[str, Any]]:
    """Built strips, in one process or in a pool of ``spawn`` workers."""
    if jobs <= 1 or len(tasks) < 2:
        return (_strip_task(task) for task in tasks)
    # spawn, never fork: SPICE state is per process and a forked child would
    # inherit a furnsh'd but unusable handle table.
    context = multiprocessing.get_context("spawn")
    pool = context.Pool(processes=jobs)

    def stream() -> Iterator[dict[str, Any]]:
        try:
            yield from pool.imap_unordered(_strip_task, tasks, chunksize=1)
        finally:
            pool.terminate()
            pool.join()

    return stream()


# --------------------------------------------------------------------------
# the survey
# --------------------------------------------------------------------------
def build_library(
    mirror: str | Path | None,
    orbits: Iterable[int] | None,
    band: str,
    *,
    lat_band: tuple[float, float] | None = None,
    min_frames: int = 2,
    jobs: int = 1,
    limit: int | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Build every strip of ``orbits`` in ``band`` and rewrite the index.

    Returns the per-orbit summary table and the report lines a caller prints.
    """
    root = mirror_root(mirror)
    band = str(band).upper()
    table = unit_rows(root, orbits, band, lat_band=lat_band)
    selected = (
        sorted({int(value) for value in table["orbit_dir"].unique()})
        if orbits is None
        else sorted({int(value) for value in orbits})
    )

    rows: list[dict[str, Any]] = []
    summary: list[dict[str, Any]] = []
    lines: list[str] = []
    for orbit in selected:
        started = time.perf_counter()
        part = table.loc[table["orbit_dir"] == orbit]
        chunks, short = chunk_table(part, min_frames=min_frames)
        mirrored = [
            (index, chunk)
            for index, chunk in chunks
            if any(
                image_path(root, orbit, str(pid)).is_file()
                for pid in chunk["product_id"]
            )
        ]
        unmirrored = len(chunks) - len(mirrored)
        if limit is not None:
            mirrored = mirrored[: int(limit)]
        tasks = [
            _make_task(root, band, index, chunk) for index, chunk in mirrored
        ]
        built = 0
        failed = 0
        pixels = 0
        capped = 0
        for result in _results(tasks, jobs):
            if "error" in result:
                failed += 1
                LOGGER.warning("chunk %s skipped: %s", result["strip_id"], result["error"])
                continue
            built += 1
            pixels += int(result["pixels"])
            capped += int(bool(result["capped"]))
            rows.append(result["row"])
        elapsed = time.perf_counter() - started
        summary.append(
            {
                "orbit": orbit,
                "band": band,
                "unit_rows": int(len(part)),
                "chunks": built,
                "skipped": short + unmirrored + failed,
                "skipped_short": short,
                "skipped_unmirrored": unmirrored,
                "skipped_failed": failed,
                "capped": capped,
                "pixels": pixels,
                "seconds": elapsed,
            }
        )
        lines.append(
            f"orbit {orbit:02d} {band}: unit rows {len(part)}, chunks built {built}, "
            f"skipped {short + unmirrored + failed} "
            f"(short {short}, unmirrored {unmirrored}, failed {failed}), "
            f"total pixels {pixels}, capped {capped}, {elapsed:.1f} s"
        )
    update_index(root, rows, selected, band)
    return pd.DataFrame(summary), lines


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------
def parse_lat_band(specification: str) -> tuple[float, float]:
    """``LO:HI`` to a pair of latitudes."""
    pieces = str(specification).split(":")
    if len(pieces) != 2:
        raise ValueError(f"bad --lat-band: {specification!r}; expected LO:HI")
    low, high = (float(piece) for piece in pieces)
    if low > high:
        raise ValueError(f"bad --lat-band: {specification!r}; LO must not exceed HI")
    return low, high


def _add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mirror", help="local mirror root")
    parser.add_argument(
        "--orbits", default="all", metavar="SPEC", help="all or e.g. 4,5,10-20"
    )
    parser.add_argument("--band", required=True, choices=["L", "M"])
    parser.add_argument(
        "--lat-band",
        metavar="LO:HI",
        help=(
            "keep only frames with LO <= bore_lat <= HI; a negative LO has to "
            "be written --lat-band=-45:45, as argparse would otherwise read it "
            "as an option"
        ),
    )
    parser.add_argument("--min-frames", type=int, default=2)
    parser.add_argument("--jobs", type=int, default=default_strip_jobs())
    parser.add_argument(
        "--limit", type=int, help="build only the first K chunks of each orbit"
    )
    parser.add_argument("-v", action="store_true", help="enable debug logging")


def add_subparser(subparsers: Any) -> None:
    """Register the ``strips`` subcommand on an argparse subparser action."""
    parser = subparsers.add_parser(
        "strips", help="build the per-pass, per-sequence strip library"
    )
    _add_arguments(parser)
    parser.set_defaults(func=run)


def make_parser() -> argparse.ArgumentParser:
    """The stand-alone parser of ``python -m jiram_catalog.strips``."""
    parser = argparse.ArgumentParser(
        prog="jiram-catalog strips",
        description="build the per-pass, per-sequence strip library",
    )
    _add_arguments(parser)
    parser.set_defaults(func=run)
    return parser


def run(args: argparse.Namespace) -> int:
    """Build the library for one band and print the per-orbit summary."""
    from .cli import parse_orbits

    orbits = parse_orbits(getattr(args, "orbits", "all"))
    root = mirror_root(getattr(args, "mirror", None))
    lat_band = (
        parse_lat_band(args.lat_band) if getattr(args, "lat_band", None) else None
    )
    summary, lines = build_library(
        root,
        orbits,
        args.band,
        lat_band=lat_band,
        min_frames=args.min_frames,
        jobs=args.jobs,
        limit=args.limit,
    )
    for line in lines:
        print(line)
    if not summary.empty:
        print(
            f"total: chunks {int(summary['chunks'].sum())}, "
            f"skipped {int(summary['skipped'].sum())}, "
            f"pixels {int(summary['pixels'].sum())}, "
            f"{float(summary['seconds'].sum()):.1f} s"
        )
    print(f"index: {strips_index_path(root)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = make_parser()
    try:
        args = parser.parse_args(argv)
        logging.basicConfig(
            level=logging.DEBUG if args.v else logging.INFO,
            format="%(levelname)s: %(message)s",
        )
        return run(args)
    except (FileNotFoundError, OSError, ValueError) as exc:
        import sys

        print(f"error: {' '.join(str(exc).split())}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Import the module under its proper name so that ``spawn`` workers resolve
    # the task function as ``jiram_catalog.strips._strip_task`` rather than
    # re-importing this file as ``__mp_main__``.
    from jiram_catalog.strips import main as _main

    raise SystemExit(_main())
