"""Region time stacks: frame selection, reprojection, composites, NetCDF I/O.

A *stack* is every frame that saw a region, reprojected onto that region's
grid and laid out along a time axis, with the per-pixel validity and emission
angle that say where and how obliquely each pixel was actually seen.  Building
one is three questions in order: which frames overlap the region (cheap
geometry against the pre-computed footprint table, then an exact per-pixel
test), how large the result has to be (one geometry-only pass that unions the
footprint boxes), and what each frame paints onto it (the exact inverse
mapping of :func:`jiram_catalog.reproject.reproject_frame`).

The two geometry passes cost about 35 ms per frame each, which is nothing
beside the reprojection, and they buy the ability to allocate the output array
exactly once: a polar stack of a few hundred perijove frames is tens of
gigabytes if it is ever copied.  Only the reprojection pass is parallelised,
with ``spawn`` workers -- SPICE state is per process, so every worker furnsh'es
its orbit's kernels itself, once, exactly as :mod:`jiram_catalog.geo` does.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import os
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from .geo import frames_with_geo
from .geometry import LM_HALF_ORDER, KernelSet, frame_geometry, normalise_epoch
from .index import load_frames
from .pds import mirror_root
from .regions import RegionGrid, load_registry
from .reproject import footprint_bbox, reproject_frame

LOGGER = logging.getLogger(__name__)

__all__ = [
    "build_stack",
    "composite_sequences",
    "default_stack_jobs",
    "image_path",
    "read_frame_image",
    "read_stack",
    "registry_table",
    "select_frames",
    "stack_output_path",
    "valid_fraction",
    "write_stack",
]

#: Detector width of a JIRAM camera frame, and the height of one band half.
SAMPLES = 432
HALF_LINES = 128
#: Every 4th line and sample is enough to decide whether a frame's footprint
#: touches a region: consecutive pixels are a fraction of a map pixel apart.
OVERLAP_STRIDE = 4
#: Degrees of slack on the region's bounding box in the cheap prefilter; the
#: exact per-pixel test follows, so the prefilter only has to be generous.
PREFILTER_PAD_DEG = 1.0
#: An arc this wide covers every longitude (matches ``geo.FULL_ARC_DEG``).
FULL_ARC_DEG = 350.0
#: NetCDF deflate level for the image cubes.
COMPRESSION_LEVEL = 4
#: Variables stored as one chunk per time step.
STACK_VARIABLES: tuple[str, ...] = ("image", "valid", "emission")


def default_stack_jobs() -> int:
    return min(8, os.cpu_count() or 1)


def _software() -> str:
    try:
        from importlib.metadata import version

        return f"jiram_catalog {version('jiram-catalog')}"
    except Exception:  # noqa: BLE001 - provenance must never break a build
        return "jiram_catalog"


def _resolve_region(
    region: RegionGrid | str, registry: str | Path | None = None
) -> RegionGrid:
    if isinstance(region, RegionGrid):
        return region
    return RegionGrid.from_yaml(region, registry)


# --------------------------------------------------------------------------
# input
# --------------------------------------------------------------------------
def image_path(mirror: str | Path, orbit: int, product_id: str) -> Path:
    """Where a calibrated RDR frame is mirrored."""
    return (
        Path(mirror)
        / "pds4"
        / "juno_jiram_bundle"
        / "data_calibrated"
        / f"orbit{int(orbit):02d}"
        / f"{product_id}.IMG"
    )


def read_frame_image(path: str | Path, half: str, *, samples: int = SAMPLES) -> np.ndarray:
    """The 128-line image block of one band half of a calibrated RDR frame.

    The PDS4 labels declare ``IEEE754MSBSingle`` but the bytes on disk are
    little-endian (see ``docs/reports/paper_projection_fit.md``), so the frames
    are read as ``<f4``.  A 256-line product carries its two halves stacked in
    :data:`jiram_catalog.geometry.LM_HALF_ORDER` order.
    """
    raw = np.fromfile(path, dtype="<f4")
    if raw.size == 0 or raw.size % samples:
        raise ValueError(f"{path}: {raw.size} floats is not a whole number of lines")
    lines = raw.size // samples
    image = raw.reshape(lines, samples)
    if lines == HALF_LINES:
        return image
    if lines == 2 * HALF_LINES:
        if half not in LM_HALF_ORDER:
            raise ValueError(f"{path}: unknown band half {half!r}")
        start = LM_HALF_ORDER.index(half) * HALF_LINES
        return image[start : start + HALF_LINES]
    raise ValueError(f"{path}: unexpected line count {lines}")


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------
def _column(frame: pd.DataFrame, name: str) -> pd.Series:
    """A geometry column, whether or not ``frames_with_geo`` suffixed it."""
    return frame[f"{name}_geo"] if f"{name}_geo" in frame.columns else frame[name]


def _arcs_overlap(
    frame_low: np.ndarray, frame_high: np.ndarray, low: float, high: float
) -> np.ndarray:
    """Circular-interval overlap of frame longitude arcs with ``[low, high]``."""
    width = (high - low) % 360.0
    if (high - low) >= 360.0 or width >= FULL_ARC_DEG:
        return np.ones(frame_low.shape, dtype=bool)
    frame_width = (frame_high - frame_low) % 360.0
    unknown = ~np.isfinite(frame_low) | ~np.isfinite(frame_high)
    full = frame_width >= FULL_ARC_DEG
    overlap = ((frame_low - low) % 360.0 <= width) | ((low - frame_low) % 360.0 <= frame_width)
    return unknown | full | overlap


def _prefilter(table: pd.DataFrame, region: RegionGrid) -> pd.DataFrame:
    """Drop frames whose footprint box cannot touch the region's box."""
    lat_min, lat_max, lon_min, lon_max = region.bbox_latlon()
    lat_min -= PREFILTER_PAD_DEG
    lat_max += PREFILTER_PAD_DEG
    if (lon_max - lon_min) % 360.0 < FULL_ARC_DEG and (lon_max - lon_min) < 360.0:
        lon_min -= PREFILTER_PAD_DEG
        lon_max += PREFILTER_PAD_DEG
    frame_min = _column(table, "min_lat").to_numpy(dtype=float)
    frame_max = _column(table, "max_lat").to_numpy(dtype=float)
    latitudes = ~((frame_max < lat_min) | (frame_min > lat_max))
    latitudes |= ~np.isfinite(frame_min) | ~np.isfinite(frame_max)
    longitudes = _arcs_overlap(
        table["lon_min_east"].to_numpy(dtype=float),
        table["lon_max_east"].to_numpy(dtype=float),
        lon_min,
        lon_max,
    )
    return table.loc[latitudes & longitudes]


def _pixels_inside(region: RegionGrid, geometry, stride: int = OVERLAP_STRIDE) -> int:
    """How many of a frame's sampled on-planet pixels land on the region canvas."""
    lat = np.asarray(geometry.lat)[::stride, ::stride]
    lon = np.asarray(geometry.lon_east)[::stride, ::stride]
    on_planet = np.asarray(geometry.on_planet)[::stride, ::stride]
    row, col, visible = region.latlon_to_pixel(lat, lon)
    return int((on_planet & visible & region.inside(row, col)).sum())


def select_frames(
    mirror: str | Path | None,
    region: RegionGrid | str,
    orbits: Iterable[int] | None,
    band: str,
    *,
    max_emission: float = 80.0,
    min_on_planet: float = 0.02,
) -> pd.DataFrame:
    """Every frame of ``orbits`` in ``band`` whose footprint touches ``region``.

    Quality cuts first (the geometry has to have converged, the frame has to
    see the planet at all, and the mean emission angle has to be below
    ``max_emission``), then the region's bounding box against the pre-computed
    footprint box, then the exact test: a fresh per-pixel geometry, every
    fourth line and sample mapped through the region's own projection, and at
    least one on-planet pixel landing on the canvas.  The count of those pixels
    comes back as ``n_inside``.
    """
    root = mirror_root(mirror)
    region = _resolve_region(region)
    band = str(band).upper()
    if band not in ("L", "M"):
        raise ValueError(f"band must be 'L' or 'M', not {band!r}")

    table = frames_with_geo(root, orbits)
    table = table.loc[
        (table["half"] == band)
        & table["geo_ok"].fillna(False).astype(bool)
        & (table["on_planet_frac"] >= min_on_planet)
        & (table["mean_emission"] <= max_emission)
    ]
    LOGGER.info("%d frame(s) pass the quality cuts", len(table))
    table = _prefilter(table, region)
    LOGGER.info("%d frame(s) survive the bounding-box prefilter", len(table))
    if table.empty:
        return table.assign(n_inside=pd.Series(dtype="int64"))

    frames = load_frames(root, orbits)
    counts: dict[Any, int] = {}
    for orbit, part in table.groupby("orbit_dir", sort=True):
        with KernelSet.for_orbits(root, [int(orbit)], frames) as kernels:
            for row in part.itertuples():
                geometry = frame_geometry(row.start_time, band, kernels)
                counts[row.Index] = _pixels_inside(region, geometry)
    inside = pd.Series(counts, name="n_inside")
    selected = table.assign(n_inside=inside).loc[inside > 0]
    LOGGER.info("%d frame(s) overlap region %s", len(selected), region.name)
    return selected.sort_values(
        ["start_time", "product_id"], kind="stable", ignore_index=True
    )


# --------------------------------------------------------------------------
# reprojection workers
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _Task:
    """Everything a worker needs for one frame; cheap to pickle."""

    mirror: str
    region: dict[str, Any]
    window: tuple[int, int, int, int]
    index: int
    orbit: int
    product_id: str
    epoch: str
    half: str


#: Per-process kernel cache; never populated in the parent before a pool.
_WORKER_STATE: dict[str, Any] = {}


def _worker_kernels(mirror: str, orbit: int) -> KernelSet:
    cached = _WORKER_STATE.get("kernels")
    if cached is not None and _WORKER_STATE.get("key") == (mirror, orbit):
        return cached
    if cached is not None:
        cached.unload()
        _WORKER_STATE.clear()
    kernels = KernelSet.for_orbits(mirror, [orbit])
    _WORKER_STATE.update(key=(mirror, orbit), kernels=kernels)
    return kernels


def _reproject_task(
    task: _Task,
) -> tuple[int, tuple[int, int, int, int] | None, np.ndarray, np.ndarray]:
    """Reproject one frame; returns its own box and the arrays inside it.

    Only the frame's footprint is returned, not the whole window, so a worker
    hands back kilobytes for a near-perijove frame instead of the megabytes the
    full canvas would cost.  ``image`` is NaN where the map pixel saw nothing,
    which is also the validity mask.
    """
    region = RegionGrid.from_dict(task.region)
    row0, row1, col0, col1 = task.window
    shape = (row1 - row0, col1 - col0)
    grid = region.canvas_grid(row0, col0)
    kernels = _worker_kernels(task.mirror, task.orbit)
    geometry = frame_geometry(task.epoch, task.half, kernels)

    box = footprint_bbox(geometry, grid, shape)
    if box is None:
        return task.index, None, np.empty((0, 0), np.float32), np.empty((0, 0), np.float32)
    image = read_frame_image(image_path(task.mirror, task.orbit, task.product_id), task.half)
    values, weight = reproject_frame(image, geometry, grid, shape)
    emission, emission_weight = reproject_frame(
        np.asarray(geometry.emission, dtype=np.float64), geometry, grid, shape
    )

    top, bottom, left, right = box
    valid = weight[top:bottom, left:right] > 0.0
    painted = np.where(valid, values[top:bottom, left:right], np.nan).astype(np.float32)
    angles = np.where(
        valid & (emission_weight[top:bottom, left:right] > 0.0),
        emission[top:bottom, left:right],
        np.nan,
    ).astype(np.float32)
    return task.index, box, painted, angles


def _footprint_boxes(
    root: Path,
    region: RegionGrid,
    selection: pd.DataFrame,
    band: str,
    frames: pd.DataFrame,
) -> list[tuple[int, int, int, int] | None]:
    """Footprint box of every selected frame on the full canvas."""
    grid = region.canvas_grid()
    shape = (region.rows, region.cols)
    boxes: list[tuple[int, int, int, int] | None] = [None] * len(selection)
    for orbit, part in selection.groupby("orbit_dir", sort=True):
        with KernelSet.for_orbits(root, [int(orbit)], frames) as kernels:
            for position, row in zip(part.index, part.itertuples(), strict=True):
                geometry = frame_geometry(row.start_time, band, kernels)
                boxes[int(position)] = footprint_bbox(geometry, grid, shape)
    return boxes


def _union(
    boxes: Iterable[tuple[int, int, int, int] | None],
) -> tuple[int, int, int, int] | None:
    present = [box for box in boxes if box is not None]
    if not present:
        return None
    array = np.asarray(present, dtype=np.int64)
    return (
        int(array[:, 0].min()),
        int(array[:, 1].max()),
        int(array[:, 2].min()),
        int(array[:, 3].max()),
    )


def _pad(
    box: tuple[int, int, int, int], margin: int, shape: tuple[int, int]
) -> tuple[int, int, int, int]:
    top, bottom, left, right = box
    return (
        max(top - margin, 0),
        min(bottom + margin, shape[0]),
        max(left - margin, 0),
        min(right + margin, shape[1]),
    )


def _results(tasks: Sequence[_Task], jobs: int) -> Iterator[tuple]:
    """Reprojected frames, in one process or in a pool of ``spawn`` workers."""
    if jobs <= 1 or len(tasks) < 2:
        return (_reproject_task(task) for task in tasks)
    # spawn, never fork: SPICE state is per process and a forked child would
    # inherit a furnsh'd but unusable handle table.
    context = multiprocessing.get_context("spawn")
    pool = context.Pool(processes=jobs)

    def stream() -> Iterator[tuple]:
        try:
            yield from pool.imap_unordered(_reproject_task, tasks, chunksize=1)
        finally:
            pool.terminate()
            pool.join()

    return stream()


# --------------------------------------------------------------------------
# the stack
# --------------------------------------------------------------------------
def _time_coords(selection: pd.DataFrame) -> dict[str, tuple[str, np.ndarray]]:
    times = pd.to_datetime(selection["start_time"]).to_numpy("datetime64[ns]")
    return {
        "time": ("time", times),
        "product_id": ("time", selection["product_id"].astype(str).to_numpy()),
        "seq_id": ("time", selection["seq_id"].fillna("").astype(str).to_numpy()),
        "orbit": ("time", selection["orbit_dir"].to_numpy(dtype=np.int32)),
        "band": ("time", selection["band"].astype(str).to_numpy()),
        "half": ("time", selection["half"].astype(str).to_numpy()),
        "start_time_iso": (
            "time",
            np.array([normalise_epoch(value) for value in selection["start_time"]]),
        ),
        "bore_emission": (
            "time",
            selection["bore_emission"].to_numpy(dtype=np.float32),
        ),
    }


def _stack_dataset(
    region: RegionGrid,
    window: tuple[int, int, int, int],
    selection: pd.DataFrame,
    image: np.ndarray,
    valid: np.ndarray,
    emission: np.ndarray,
    *,
    band: str,
    level: str,
) -> xr.Dataset:
    top, _, left, _ = window
    rows = np.arange(top, top + image.shape[1], dtype=np.float64)
    cols = np.arange(left, left + image.shape[2], dtype=np.float64)
    latitude, longitude = region.lat_lon_grids(
        (top, top + image.shape[1], left, left + image.shape[2])
    )
    coords: dict[str, Any] = {
        "y_km": ("y", region.y_km(rows)),
        "x_km": ("x", region.x_km(cols)),
        "lat": (("y", "x"), latitude.astype(np.float32)),
        "lon_east": (("y", "x"), longitude.astype(np.float32)),
        **_time_coords(selection),
    }
    dataset = xr.Dataset(
        data_vars={
            "image": (("time", "y", "x"), image),
            "valid": (("time", "y", "x"), valid),
            "emission": (("time", "y", "x"), emission),
        },
        coords=coords,
        attrs={
            "region": region.name,
            "projection": json.dumps(region.to_dict(), sort_keys=True),
            "km_per_px": float(region.km_per_px),
            "band": band,
            "level": level,
            "row0": int(top),
            "col0": int(left),
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "software": _software(),
        },
    )
    dataset["image"].attrs.update(long_name="reprojected radiance", units="W m-2 sr-1 um-1")
    dataset["emission"].attrs.update(long_name="emission angle", units="degree")
    dataset["valid"].attrs.update(long_name="map pixel painted by this frame")
    dataset["y_km"].attrs.update(units="km")
    dataset["x_km"].attrs.update(units="km")
    dataset["lat"].attrs.update(units="degree_north", long_name="planetocentric latitude")
    dataset["lon_east"].attrs.update(units="degree_east")
    return dataset


def build_stack(
    mirror: str | Path | None,
    region: RegionGrid | str,
    frames: pd.DataFrame,
    band: str,
    *,
    crop: bool = True,
    margin_px: int = 16,
    jobs: int = 1,
) -> xr.Dataset:
    """Reproject every frame of ``frames`` onto ``region`` as a time stack.

    ``frames`` is what :func:`select_frames` returned.  With ``jobs`` above one
    the reprojection runs in ``spawn`` workers, so a caller's own entry point
    must be guarded by ``if __name__ == "__main__":`` as the CLI's is.  One
    geometry-only pass unions the frames' footprint boxes so that the output
    array is allocated once at its final size; the reprojection pass then
    paints into it, and with ``crop`` the result is narrowed to the bounding
    box of the pixels that any frame actually painted, widened by
    ``margin_px``.
    """
    root = mirror_root(mirror)
    region = _resolve_region(region)
    band = str(band).upper()
    selection = frames.reset_index(drop=True)
    if selection.empty:
        raise ValueError(f"no frames selected for region {region.name}")

    canvas = (region.rows, region.cols)
    index = load_frames(root, sorted({int(value) for value in selection["orbit_dir"]}))
    boxes = _footprint_boxes(root, region, selection, band, index)
    union = _union(boxes)
    if union is None:
        raise ValueError(f"no frame of the selection lands on region {region.name}")
    window = _pad(union, margin_px, canvas) if crop else (0, canvas[0], 0, canvas[1])
    height, width = window[1] - window[0], window[3] - window[2]
    LOGGER.info(
        "reprojecting %d frame(s) onto a %d x %d window of %s",
        len(selection),
        height,
        width,
        region.name,
    )

    count = len(selection)
    image = np.full((count, height, width), np.nan, dtype=np.float32)
    emission = np.full((count, height, width), np.nan, dtype=np.float32)
    valid = np.zeros((count, height, width), dtype=bool)

    tasks = [
        _Task(
            mirror=str(root),
            region=region.to_dict(),
            window=window,
            index=position,
            orbit=int(row.orbit_dir),
            product_id=str(row.product_id),
            epoch=normalise_epoch(row.start_time),
            half=band,
        )
        for position, row in enumerate(selection.itertuples())
    ]
    painted_boxes: list[tuple[int, int, int, int] | None] = [None] * count
    for done, (position, box, values, angles) in enumerate(_results(tasks, jobs), start=1):
        if box is not None:
            top, bottom, left, right = box
            image[position, top:bottom, left:right] = values
            emission[position, top:bottom, left:right] = angles
            painted = np.isfinite(values)
            valid[position, top:bottom, left:right] = painted
            if painted.any():
                rows_hit = np.flatnonzero(painted.any(axis=1))
                cols_hit = np.flatnonzero(painted.any(axis=0))
                painted_boxes[position] = (
                    top + int(rows_hit[0]),
                    top + int(rows_hit[-1]) + 1,
                    left + int(cols_hit[0]),
                    left + int(cols_hit[-1]) + 1,
                )
        if done % 25 == 0 or done == count:
            LOGGER.info("reprojected %d/%d frame(s)", done, count)

    if crop:
        tight = _union(painted_boxes)
        if tight is None:
            raise ValueError(f"no valid pixel on region {region.name}")
        inner = _pad(tight, margin_px, (height, width))
        if inner != (0, height, 0, width):
            # Contiguous copies, not views: the NetCDF writer would make them
            # anyway, and rebinding here releases the full-window allocation.
            cut = (slice(None), slice(inner[0], inner[1]), slice(inner[2], inner[3]))
            image = np.ascontiguousarray(image[cut])
            emission = np.ascontiguousarray(emission[cut])
            valid = np.ascontiguousarray(valid[cut])
            window = (
                window[0] + inner[0],
                window[0] + inner[1],
                window[2] + inner[2],
                window[2] + inner[3],
            )
    return _stack_dataset(
        region, window, selection, image, valid, emission, band=band, level="frame"
    )


# --------------------------------------------------------------------------
# sequence composites
# --------------------------------------------------------------------------
def composite_sequences(stack: xr.Dataset) -> xr.Dataset:
    """Average each spin sequence of a frame stack into one snapshot.

    Within a sequence the spacecraft has turned by a few tens of seconds' worth
    of spin, not by anything the atmosphere notices, so the twelve frames of a
    sequence are twelve looks at the same scene: the mean over the frames that
    painted a pixel is the composite, the pixel is valid if any frame painted
    it, and its emission angle is the best (smallest) any frame achieved.
    """
    sequences = np.asarray(stack["seq_id"].values).astype(str)
    times = pd.to_datetime(np.asarray(stack["time"].values))
    order = np.argsort(times.to_numpy("datetime64[ns]"), kind="stable")
    groups: dict[str, list[int]] = {}
    for position in order:
        groups.setdefault(sequences[position], []).append(int(position))

    image = np.asarray(stack["image"].values)
    valid = np.asarray(stack["valid"].values)
    emission = np.asarray(stack["emission"].values)
    shape = image.shape[1:]
    count = len(groups)
    out_image = np.full((count, *shape), np.nan, dtype=np.float32)
    out_emission = np.full((count, *shape), np.nan, dtype=np.float32)
    out_valid = np.zeros((count, *shape), dtype=bool)

    rows: list[dict[str, Any]] = []
    orbits = np.asarray(stack["orbit"].values)
    for position, (name, members) in enumerate(groups.items()):
        block = image[members]
        mask = valid[members]
        painted = mask.sum(axis=0)
        with np.errstate(invalid="ignore"):
            total = np.where(mask, np.nan_to_num(block), 0.0).sum(axis=0)
            mean = np.where(painted > 0, total / np.maximum(painted, 1), np.nan)
            # np.inf rather than np.nanmin: a pixel no frame painted would make
            # nanmin warn about an all-NaN slice, and there are many of those.
            angles = np.where(mask & np.isfinite(emission[members]), emission[members], np.inf)
            best = angles.min(axis=0)
            best = np.where(np.isfinite(best), best, np.nan)
        out_image[position] = mean.astype(np.float32)
        out_emission[position] = best.astype(np.float32)
        out_valid[position] = painted > 0
        member_times = times[members]
        rows.append(
            {
                "seq_id": name,
                "n_frames": len(members),
                "orbit": int(orbits[members[0]]),
                "time": member_times.min(),
                "time_first": member_times.min(),
                "time_last": member_times.max(),
            }
        )

    table = pd.DataFrame(rows)
    spatial = ("y_km", "x_km", "lat", "lon_east")
    coords: dict[str, Any] = {
        name: stack.coords[name] for name in spatial if name in stack.coords
    }
    coords.update(
        {
            "time": ("time", table["time"].to_numpy("datetime64[ns]")),
            "seq_id": ("time", table["seq_id"].to_numpy(dtype=object).astype(str)),
            "n_frames": ("time", table["n_frames"].to_numpy(dtype=np.int32)),
            "orbit": ("time", table["orbit"].to_numpy(dtype=np.int32)),
            "time_first": ("time", table["time_first"].to_numpy("datetime64[ns]")),
            "time_last": ("time", table["time_last"].to_numpy("datetime64[ns]")),
            "start_time_iso": (
                "time",
                np.array([normalise_epoch(value) for value in table["time"]]),
            ),
        }
    )
    composite = xr.Dataset(
        data_vars={
            "image": (("time", "y", "x"), out_image),
            "valid": (("time", "y", "x"), out_valid),
            "emission": (("time", "y", "x"), out_emission),
        },
        coords=coords,
        attrs={**stack.attrs, "level": "sequence"},
    )
    for name in ("image", "emission", "valid"):
        composite[name].attrs.update(stack[name].attrs)
    return composite


# --------------------------------------------------------------------------
# NetCDF I/O
# --------------------------------------------------------------------------
def stack_output_path(
    mirror: str | Path | None, region: str, band: str, orbits: str, level: str
) -> Path:
    """The default output location of a region stack."""
    token = str(orbits).strip().lower().replace(",", "_").replace(" ", "")
    return (
        mirror_root(mirror)
        / "regions"
        / region
        / f"{str(band).upper()}_orbits{token}_{level}.nc"
    )


def write_stack(dataset: xr.Dataset, path: str | Path) -> Path:
    """Write a stack as NetCDF4, deflated, one chunk per time step."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    height, width = dataset.sizes["y"], dataset.sizes["x"]
    encoding: dict[str, dict[str, Any]] = {}
    for name in STACK_VARIABLES:
        if name not in dataset:
            continue
        encoding[name] = {
            "zlib": True,
            "complevel": COMPRESSION_LEVEL,
            "chunksizes": (1, height, width),
        }
        if dataset[name].dtype.kind == "f":
            encoding[name].update({"dtype": "float32", "_FillValue": np.float32(np.nan)})
    temporary = target.with_suffix(".nc.tmp")
    dataset.to_netcdf(temporary, format="NETCDF4", engine="netcdf4", encoding=encoding)
    temporary.replace(target)
    return target


def read_stack(path: str | Path) -> xr.Dataset:
    """Open a stack written by :func:`write_stack` (lazily; it may be large)."""
    return xr.open_dataset(path, engine="netcdf4")


def valid_fraction(dataset: xr.Dataset) -> float:
    """Fraction of the stack's pixels that any frame painted."""
    valid = dataset["valid"]
    return float(np.asarray(valid.values).mean()) if valid.size else 0.0


def registry_table(path: str | Path | None = None) -> str:
    """The ``regions`` subcommand's listing of the registry."""
    registry = load_registry(path)
    lines = [
        "| region | projection | km/px | rows x cols | extent km | lat range | lon range |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for region in registry.values():
        lat_min, lat_max, lon_min, lon_max = region.bbox_latlon()
        width = region.cols * region.km_per_px
        height = region.rows * region.km_per_px
        lines.append(
            f"| {region.name} | {region.projection} | {region.km_per_px:g} "
            f"| {region.rows} x {region.cols} | {width:.0f} x {height:.0f} "
            f"| {lat_min:.2f} to {lat_max:.2f} "
            f"| {lon_min:.2f} to {lon_max:.2f} |"
        )
    return "\n".join(lines)
