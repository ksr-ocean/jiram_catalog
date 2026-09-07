"""JunoCam region stacks: the same map, one more axis.

A JIRAM region stack is ``(time, y, x)`` because a JIRAM frame is one
wavelength band; a JunoCam product carries up to four filter strips that see
the same ground a fraction of a second apart, so its stack is
``(time, band, y, x)`` and the extra axis is not decoration.  The three colour
strips sit 155 pixel widths apart on the focal plane, so RED sees in frame
``i`` what GREEN sees about one and a third frames later: the bands are
separate measurements of the same scene at slightly different epochs and
slightly different geometry, and averaging them into one plane before
reprojection would throw away exactly the registration signal that says the
timing model is right.

Selection follows :func:`jiram_catalog.stacks.select_frames` step for step --
quality cuts, then the region's bounding box against the pre-computed
footprint box, then an exact test -- with one substitution: where the JIRAM
test recomputes a frame's per-pixel geometry and counts on-planet pixels
landing on the canvas, this one walks the footprint outline that
:mod:`jiram_catalog.junocam.geo` already stored.  A JunoCam swath's outline is
a ribbon of up to 64 vertices rather than a quadrilateral, and re-deriving it
would cost seventeen seconds an image for an answer the survey already has.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from ..config import mirror_root
from ..geometry import KernelSet, normalise_epoch
from ..regions import RegionGrid
from ..stacks import _prefilter, _software, _union
from .geo import load_geo
from .geometry import image_geometry
from .images import read_image
from .index import load_images
from .quality import load_quality
from .reproject import reproject_image

LOGGER = logging.getLogger(__name__)

__all__ = [
    "QUALITY_TIERS",
    "build_stack",
    "select_images",
    "stack_output_path",
    "stack_summary",
    "window_grid",
]

#: Quality tiers, best first; ``quality_min="B"`` admits A and B.
QUALITY_TIERS: tuple[str, ...] = ("A", "B", "C")

#: How much coarser than the map an image's own ground sample may be.
#:
#: A region stack exists to be compared pixel to pixel across time, so an
#: image whose ground sample is far coarser than the map's cannot contribute
#: anything the map can hold: reprojecting it only smears one input pixel over
#: hundreds of output cells.  The JIRAM builder needs no such rule because a
#: JIRAM orbit's frames sit within a factor of a few of each other; a JunoCam
#: perijove pass sweeps a factor of fifty in two hours, from a whole-disk view
#: at 578 km per pixel to a close-up at 11.  Twenty-five is generous -- most
#: mapping pipelines refuse to upsample by more than a factor of a few -- and
#: on the 15 km paper grid it is the point at which perijove 4's colour
#: sequence stops being a picture of the whole planet.
MAX_PIXEL_RATIO = 25.0

#: Probe points of the canvas used when no outline vertex lands on it: a
#: footprint larger than the region has every vertex outside and would
#: otherwise look like a miss.
_CANVAS_PROBES = ((0.0, 0.0), (0.0, 1.0), (1.0, 0.0), (1.0, 1.0), (0.5, 0.5))


def _resolve_region(region: RegionGrid | str, config: str | Path | None = None) -> RegionGrid:
    return region if isinstance(region, RegionGrid) else RegionGrid.from_yaml(str(region), config)


def tier_rank(tier: Any) -> int:
    """``A`` -> 0, ``B`` -> 1, ``C`` -> 2; anything else ranks worst."""
    text = str(tier).strip().upper()
    return QUALITY_TIERS.index(text) if text in QUALITY_TIERS else len(QUALITY_TIERS)


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------
def outline_overlap(region: RegionGrid, fp_lon: Any, fp_lat: Any) -> int:
    """How much of one footprint outline lands on ``region``'s canvas.

    The count is of outline vertices, which is a lower bound on the overlap
    and zero in one case that is not a miss: a footprint that *contains* the
    whole region has every vertex outside it.  So a zero is retested by asking
    whether the canvas's corners or centre fall inside the outline polygon,
    and a hit there counts as one.
    """
    longitudes = np.asarray(fp_lon, dtype=np.float64).ravel()
    latitudes = np.asarray(fp_lat, dtype=np.float64).ravel()
    if longitudes.size < 3:
        return 0
    row, col, visible = region.latlon_to_pixel(latitudes, longitudes)
    inside = visible & region.inside(row, col)
    count = int(np.count_nonzero(inside))
    if count:
        return count
    usable = visible & np.isfinite(row) & np.isfinite(col)
    if int(np.count_nonzero(usable)) < 3:
        return 0
    from matplotlib.path import Path as MplPath

    polygon = MplPath(np.stack([col[usable], row[usable]], axis=1))
    probes = np.array(
        [[fraction_col * (region.cols - 1), fraction_row * (region.rows - 1)]
         for fraction_row, fraction_col in _CANVAS_PROBES],
        dtype=np.float64,
    )
    return int(np.count_nonzero(polygon.contains_points(probes)))


def image_table(
    mirror: str | Path | None,
    orbits: Iterable[int] | None,
    *,
    quality_min: str = "A",
    bands: Sequence[str] | None = None,
) -> pd.DataFrame:
    """Mirrored RDR images joined to their quality tier and their geometry.

    The join is the catalog's own three tables -- ``junocam_images`` for what
    exists, ``junocam_quality`` for whether it is worth using, ``junocam_geo``
    for where it looked -- and the result is what both the stack builder and
    the strip builder select from.
    """
    root = mirror_root(mirror)
    images = load_images(root, orbits)
    images = images.loc[
        (images["level"].astype(str) == "RDR")
        & images["img_present"].fillna(False).astype(bool)
    ]
    quality = load_quality(root, orbits)[["product_id", "quality_tier", "quality_epoch"]]
    geo = load_geo(root, orbits).drop(
        columns=["orbit", "level", "start_time", "bands", "n_bands", "n_framelets"]
    )
    table = images.merge(quality, on="product_id", how="inner").merge(
        geo, on="product_id", how="inner"
    )
    table = table.loc[table["geo_ok"].fillna(False).astype(bool)]
    table = table.loc[
        table["quality_tier"].map(tier_rank) <= QUALITY_TIERS.index(str(quality_min).upper())
    ]
    if bands is not None:
        wanted = {str(name).upper() for name in bands}
        has_bands = table["filters"].astype(str).apply(
            lambda text: wanted <= {part.strip().upper() for part in text.split(";") if part.strip()}
        )
        table = table.loc[has_bands]
    return table.sort_values(["start_time", "product_id"], kind="stable", ignore_index=True)


def select_images(
    mirror: str | Path | None,
    region: RegionGrid | str,
    orbits: Iterable[int] | None,
    *,
    quality_min: str = "A",
    max_emission: float = 80.0,
    bands: Sequence[str] | None = None,
    min_on_planet: float = 0.0,
    max_pixel_ratio: float = MAX_PIXEL_RATIO,
    config: str | Path | None = None,
) -> pd.DataFrame:
    """Every mirrored image of ``orbits`` whose footprint touches ``region``.

    ``n_inside`` comes back on each row, as it does from
    :func:`jiram_catalog.stacks.select_frames`, so a caller can order or
    threshold on how much of the region an image actually covers.  See
    :data:`MAX_PIXEL_RATIO` for the one cut this selector makes that the JIRAM
    one does not.
    """
    root = mirror_root(mirror)
    grid = _resolve_region(region, config)
    table = image_table(root, orbits, quality_min=quality_min, bands=bands)
    table = table.loc[
        (table["on_planet_frac"] > float(min_on_planet))
        & ~(table["bore_emission"] > float(max_emission))
        & ~(table["median_pixel_km"] > float(max_pixel_ratio) * grid.km_per_px)
    ]
    LOGGER.info("%d image(s) pass the quality cuts", len(table))
    table = _prefilter(table, grid)
    LOGGER.info("%d image(s) survive the bounding-box prefilter", len(table))
    if table.empty:
        return table.assign(n_inside=pd.Series(dtype="int64"))
    inside = pd.Series(
        {
            index: outline_overlap(grid, row["fp_lon"], row["fp_lat"])
            for index, row in table.iterrows()
        },
        name="n_inside",
        dtype="int64",
    )
    selected = table.assign(n_inside=inside).loc[inside > 0]
    LOGGER.info("%d image(s) overlap region %s", len(selected), grid.name)
    return selected.sort_values(["start_time", "product_id"], kind="stable", ignore_index=True)


# --------------------------------------------------------------------------
# geometry of the output canvas
# --------------------------------------------------------------------------
def window_grid(region: RegionGrid, window: tuple[int, int, int, int]) -> RegionGrid:
    """The same projection restricted to ``(row0, row1, col0, col1)``.

    :func:`jiram_catalog.junocam.reproject.reproject_image` fills a whole grid,
    so cropping has to happen in the grid rather than after the fact; shifting
    the reference pixel by the crop origin is what makes the small grid answer
    the same ``(lat, lon)`` for a ground point as the big one.
    """
    row0, row1, col0, col1 = window
    entry = region.to_dict()
    entry["shape"] = [int(row1 - row0), int(col1 - col0)]
    key = "pole_pixel" if region.projection == "polar_ortho" else "center_pixel"
    reference = entry[key]
    entry[key] = [float(reference[0]) - row0, float(reference[1]) - col0]
    return RegionGrid.from_dict(entry)


def _outline_box(
    region: RegionGrid, fp_lon: Any, fp_lat: Any, *, pad: int = 2
) -> tuple[int, int, int, int] | None:
    """Canvas bounding box of one footprint outline, clipped to the canvas."""
    longitudes = np.asarray(fp_lon, dtype=np.float64).ravel()
    latitudes = np.asarray(fp_lat, dtype=np.float64).ravel()
    if longitudes.size == 0:
        return None
    row, col, visible = region.latlon_to_pixel(latitudes, longitudes)
    keep = visible & np.isfinite(row) & np.isfinite(col)
    if not keep.any():
        return None
    top = max(int(np.floor(row[keep].min())) - pad, 0)
    bottom = min(int(np.ceil(row[keep].max())) + pad + 1, region.rows)
    left = max(int(np.floor(col[keep].min())) - pad, 0)
    right = min(int(np.ceil(col[keep].max())) + pad + 1, region.cols)
    if bottom <= top or right <= left:
        return None
    return top, bottom, left, right


def _pad(box: tuple[int, int, int, int], margin: int, shape: tuple[int, int]):
    top, bottom, left, right = box
    return (
        max(top - margin, 0),
        min(bottom + margin, shape[0]),
        max(left - margin, 0),
        min(right + margin, shape[1]),
    )


# --------------------------------------------------------------------------
# reprojection workers
# --------------------------------------------------------------------------
#: Index columns a worker needs to read an image and place it.
TASK_COLUMNS: tuple[str, ...] = (
    "product_id",
    "orbit",
    "start_time",
    "filters",
    "n_bands",
    "n_framelets",
    "interframe_delay_s",
    "path",
    "lines",
    "samples",
    "sample_bits",
)


@dataclass(frozen=True)
class _Task:
    """One image, flattened to something cheap to pickle."""

    mirror: str
    grid: dict[str, Any]
    index: int
    orbit: int
    bands: tuple[str, ...]
    refine: bool
    record: dict[str, Any]


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


def _reproject_task(task: _Task) -> dict[str, Any]:
    """Place one image on the window; return only the box it painted.

    Handing back the painted box rather than the whole window is what keeps a
    pool of workers cheap: a JunoCam swath lands on a few per cent of a polar
    canvas, and the difference is megabytes per image against gigabytes.
    """
    grid = RegionGrid.from_dict(task.grid)
    row = pd.Series(task.record)
    kernels = _worker_kernels(task.mirror, task.orbit)
    image = read_image(row, task.mirror)
    geometry = image_geometry(
        row, kernels, mirror=task.mirror, refine=task.refine, image=image
    )
    values, counts = reproject_image(image, geometry, grid, task.bands)
    del image
    angles, _ = reproject_image(
        np.asarray(geometry.emission, dtype=np.float64), geometry, grid, task.bands
    )
    dt_refined = float(geometry.dt_refined_s)
    del geometry

    painted = np.isfinite(values).any(axis=0)
    result: dict[str, Any] = {
        "index": int(task.index),
        "dt_refined_s": dt_refined,
        "box": None,
    }
    if not painted.any():
        return result
    rows_hit = np.flatnonzero(painted.any(axis=1))
    cols_hit = np.flatnonzero(painted.any(axis=0))
    box = (
        int(rows_hit[0]),
        int(rows_hit[-1]) + 1,
        int(cols_hit[0]),
        int(cols_hit[-1]) + 1,
    )
    cut = (slice(None), slice(box[0], box[1]), slice(box[2], box[3]))
    result.update(
        box=box,
        image=np.ascontiguousarray(values[cut]),
        emission=np.ascontiguousarray(angles[cut].astype(np.float32)),
        counts=np.ascontiguousarray(counts[cut]),
    )
    return result


def _results(tasks: Sequence[_Task], jobs: int) -> Iterator[dict[str, Any]]:
    if jobs <= 1 or len(tasks) < 2:
        return (_reproject_task(task) for task in tasks)
    context = multiprocessing.get_context("spawn")
    pool = context.Pool(processes=jobs)

    def stream() -> Iterator[dict[str, Any]]:
        try:
            yield from pool.imap_unordered(_reproject_task, tasks, chunksize=1)
        finally:
            pool.terminate()
            pool.join()

    return stream()


# --------------------------------------------------------------------------
# the stack
# --------------------------------------------------------------------------
def build_stack(
    mirror: str | Path | None,
    region: RegionGrid | str,
    images: pd.DataFrame,
    bands: Sequence[str],
    *,
    refine: bool = True,
    crop: bool = True,
    margin_px: int = 16,
    jobs: int = 1,
    config: str | Path | None = None,
) -> xr.Dataset:
    """Reproject every image of ``images`` onto ``region`` as a band stack.

    ``images`` is what :func:`select_images` returned.  The output window is
    the union of the footprint boxes, so the array is allocated once at its
    final size, and with ``crop`` it is narrowed again to what the images
    actually painted.  With ``jobs`` above one the reprojection runs in
    ``spawn`` workers, so a caller's entry point must be guarded by
    ``if __name__ == "__main__":`` as the CLI's is.
    """
    root = mirror_root(mirror)
    grid = _resolve_region(region, config)
    names = tuple(str(name).upper() for name in bands)
    selection = images.reset_index(drop=True)
    if selection.empty:
        raise ValueError(f"no image selected for region {grid.name}")

    canvas = (grid.rows, grid.cols)
    boxes = [
        _outline_box(grid, row["fp_lon"], row["fp_lat"]) for _, row in selection.iterrows()
    ]
    union = _union(boxes)
    if union is None:
        raise ValueError(f"no image of the selection lands on region {grid.name}")
    window = _pad(union, margin_px, canvas) if crop else (0, canvas[0], 0, canvas[1])
    height, width = window[1] - window[0], window[3] - window[2]
    LOGGER.info(
        "reprojecting %d image(s) x %d band(s) onto a %d x %d window of %s",
        len(selection),
        len(names),
        height,
        width,
        grid.name,
    )

    count = len(selection)
    shape = (count, len(names), height, width)
    image = np.full(shape, np.nan, dtype=np.float32)
    emission = np.full(shape, np.nan, dtype=np.float32)
    overlaps = np.zeros(shape, dtype=np.uint8)
    valid = np.zeros((count, height, width), dtype=bool)
    dt_refined = np.full(count, np.nan, dtype=np.float64)

    columns = [name for name in TASK_COLUMNS if name in selection.columns]
    tasks = [
        _Task(
            mirror=str(root),
            grid=window_grid(grid, window).to_dict(),
            index=position,
            orbit=int(row["orbit"]),
            bands=names,
            refine=bool(refine),
            record={key: row[key] for key in columns},
        )
        for position, (_, row) in enumerate(selection.iterrows())
    ]

    painted_boxes: list[tuple[int, int, int, int] | None] = [None] * count
    for done, result in enumerate(_results(tasks, jobs), start=1):
        position = int(result["index"])
        dt_refined[position] = result["dt_refined_s"]
        box = result["box"]
        if box is not None:
            top, bottom, left, right = box
            cut = (position, slice(None), slice(top, bottom), slice(left, right))
            image[cut] = result["image"]
            emission[cut] = result["emission"]
            overlaps[cut] = result["counts"]
            valid[position, top:bottom, left:right] = np.isfinite(result["image"]).any(axis=0)
            painted_boxes[position] = box
        if done % 10 == 0 or done == count:
            LOGGER.info("reprojected %d/%d image(s)", done, count)

    if crop:
        tight = _union(painted_boxes)
        if tight is None:
            raise ValueError(f"no valid pixel on region {grid.name}")
        inner = _pad(tight, margin_px, (height, width))
        if inner != (0, height, 0, width):
            cut4 = (slice(None), slice(None), slice(inner[0], inner[1]), slice(inner[2], inner[3]))
            cut3 = (slice(None), slice(inner[0], inner[1]), slice(inner[2], inner[3]))
            image = np.ascontiguousarray(image[cut4])
            emission = np.ascontiguousarray(emission[cut4])
            overlaps = np.ascontiguousarray(overlaps[cut4])
            valid = np.ascontiguousarray(valid[cut3])
            window = (
                window[0] + inner[0],
                window[0] + inner[1],
                window[2] + inner[2],
                window[2] + inner[3],
            )
    return _stack_dataset(
        grid, window, selection, names, image, valid, emission, overlaps, dt_refined
    )


def _stack_dataset(
    region: RegionGrid,
    window: tuple[int, int, int, int],
    selection: pd.DataFrame,
    bands: tuple[str, ...],
    image: np.ndarray,
    valid: np.ndarray,
    emission: np.ndarray,
    overlaps: np.ndarray,
    dt_refined: np.ndarray,
) -> xr.Dataset:
    top, _, left, _ = window
    rows = np.arange(top, top + image.shape[2], dtype=np.float64)
    cols = np.arange(left, left + image.shape[3], dtype=np.float64)
    latitude, longitude = region.lat_lon_grids(
        (top, top + image.shape[2], left, left + image.shape[3])
    )
    times = pd.to_datetime(selection["start_time"]).to_numpy("datetime64[ns]")
    product_ids = selection["product_id"].astype(str).to_numpy()
    coords: dict[str, Any] = {
        "time": ("time", times),
        "band": ("band", np.asarray(bands, dtype=object).astype(str)),
        "y_km": ("y", region.y_km(rows)),
        "x_km": ("x", region.x_km(cols)),
        "lat": (("y", "x"), latitude.astype(np.float32)),
        "lon_east": (("y", "x"), longitude.astype(np.float32)),
        "product_id": ("time", product_ids),
        "seq_id": ("time", product_ids),
        "orbit": ("time", selection["orbit"].to_numpy(dtype=np.int32)),
        "quality_tier": ("time", selection["quality_tier"].astype(str).to_numpy()),
        "start_time_iso": (
            "time",
            np.array([normalise_epoch(value) for value in selection["start_time"]]),
        ),
        "bore_emission": ("time", selection["bore_emission"].to_numpy(dtype=np.float32)),
        "dt_refined_s": ("time", np.asarray(dt_refined, dtype=np.float32)),
    }
    dataset = xr.Dataset(
        data_vars={
            "image": (("time", "band", "y", "x"), image),
            "valid": (("time", "y", "x"), valid),
            "emission": (("time", "band", "y", "x"), emission),
            "n_frames": (("time", "band", "y", "x"), overlaps),
        },
        coords=coords,
        attrs={
            "region": region.name,
            "projection": json.dumps(region.to_dict(), sort_keys=True),
            "km_per_px": float(region.km_per_px),
            "instrument": "JunoCam",
            "band": ";".join(bands),
            "bands": ";".join(bands),
            "level": "frame",
            "row0": int(top),
            "col0": int(left),
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "software": _software(),
        },
    )
    dataset["image"].attrs.update(long_name="reprojected radiance", units="DN")
    dataset["emission"].attrs.update(long_name="emission angle", units="degree")
    dataset["valid"].attrs.update(long_name="map pixel painted in at least one band")
    dataset["n_frames"].attrs.update(long_name="framelets averaged into this cell")
    dataset["y_km"].attrs.update(units="km")
    dataset["x_km"].attrs.update(units="km")
    dataset["lat"].attrs.update(units="degree_north", long_name="planetocentric latitude")
    dataset["lon_east"].attrs.update(units="degree_east")
    dataset["dt_refined_s"].attrs.update(
        units="s", long_name="limb-fitted start-time offset of this image"
    )
    return dataset


# --------------------------------------------------------------------------
# naming and reporting
# --------------------------------------------------------------------------
def stack_output_path(
    mirror: str | Path | None,
    region: str,
    bands: Sequence[str],
    orbits: str,
    level: str = "frame",
) -> Path:
    """``<mirror>/regions/<region>/junocam_<bands>_orbits<SPEC>_<level>.nc``."""
    token = str(orbits).strip().lower().replace(",", "_").replace(" ", "")
    names = "-".join(str(name).upper() for name in bands)
    return (
        mirror_root(mirror)
        / "regions"
        / str(region)
        / f"junocam_{names}_orbits{token}_{level}.nc"
    )


def stack_summary(dataset: xr.Dataset, path: str | Path) -> str:
    """The ``junocam region-stack`` subcommand's report."""
    dt = np.asarray(dataset["dt_refined_s"].values, dtype=float)
    valid = np.asarray(dataset["valid"].values)
    return "\n".join(
        [
            f"wrote {path}",
            f"  dims: time={dataset.sizes['time']} band={dataset.sizes['band']} "
            f"y={dataset.sizes['y']} x={dataset.sizes['x']}",
            f"  bands: {', '.join(str(v) for v in dataset['band'].values)}",
            f"  valid pixels: {float(valid.mean()):.4f} of the canvas",
            f"  limb-refined epochs: {int(np.isfinite(dt).sum())}/{dt.size}",
            f"  size on disk: {Path(path).stat().st_size / 1e9:.2f} GB",
        ]
    )
