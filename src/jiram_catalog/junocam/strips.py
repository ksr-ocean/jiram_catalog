"""JunoCam strips: one image, one grid, one file.

A JIRAM strip is a *chunk* of a sequence -- consecutive frames of one band,
cut where the boresight has walked twelve degrees or the cadence has broken --
because a single 128 x 432 JIRAM frame is far too small to be a map on its
own.  A JunoCam product needs no chunking at all: the spin sweeps the framelets
across the scene, so one product already *is* a contiguous strip, tens of
thousands of kilometres long, and the unit of the library is the image.

Everything else is the JIRAM recipe, and reuses the JIRAM functions so that it
cannot drift from it: :func:`jiram_catalog.strips.boresight_centroid` fixes the
tangent point, :func:`jiram_catalog.strips.resolution_class` quantises the
scale so two strips of a class can be compared without resampling, and
:func:`jiram_catalog.strips.chunk_grid` sizes the canvas from where the
geometry actually lands (capped at :data:`jiram_catalog.strips.MAX_SIDE_PX`,
which for a near-perijove swath at ten kilometres a pixel is what stops a
hemisphere from asking for a fourteen-thousand-pixel square).

The one structural difference is the band axis: ``image``, ``emission``,
``incidence`` and ``n_frames`` gain a leading ``band`` dimension, while
``valid`` stays two-dimensional and means "painted in at least one band".  That
is what lets the same viewer, the same statistics and the same index serve
both instruments -- a JIRAM strip is the ``band``-less special case.
"""

from __future__ import annotations

import json
import logging
import multiprocessing
import time
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from ..config import mirror_root
from ..geo import FULL_ARC_DEG, POLE_LATITUDE, longitude_arc
from ..geometry import KernelSet, normalise_epoch
from ..strips import (
    INDEX_COLUMNS,
    _coerce_index,
    _lat_lon_local_time,
    _software,
    _subsolar_lon_east,
    boresight_centroid,
    chunk_grid,
    index_row,
    migrate_index,
    resolution_class,
    strips_index_path,
    write_strip,
)
from .geometry import image_geometry
from .images import read_image
from .reproject import reproject_image
from .stacks import TASK_COLUMNS, image_table

LOGGER = logging.getLogger(__name__)

__all__ = [
    "build_library",
    "build_strip",
    "junocam_strip_path",
    "library_summary",
    "select_images",
    "update_index",
]

#: Strides of the swath subsample the canvas is sized from; the extent only
#: needs the extremes, and :func:`jiram_catalog.strips.chunk_grid` strides
#: again on top of this.
FRAME_STRIDE = 2
LINE_STRIDE = 8
SAMPLE_STRIDE = 8


def junocam_strip_path(mirror: str | Path | None, orbit: int, identifier: str) -> Path:
    """``<mirror>/strips/junocam/orbitNN/<identifier>.nc``.

    A directory of its own beside the JIRAM orbits, so that the JIRAM index
    rebuild -- which deletes orphans by globbing its own orbit directory --
    can never reach a JunoCam file.
    """
    return (
        mirror_root(mirror)
        / "strips"
        / "junocam"
        / f"orbit{int(orbit):02d}"
        / f"{identifier}.nc"
    )


def select_images(
    mirror: str | Path | None,
    orbits: Iterable[int] | None,
    bands: Sequence[str],
    *,
    quality_min: str = "A",
) -> pd.DataFrame:
    """Every placed image of ``orbits`` that carries all of ``bands``."""
    return image_table(mirror, orbits, quality_min=quality_min, bands=bands)


# --------------------------------------------------------------------------
# one strip
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _SwathExtent:
    """The three arrays :func:`chunk_grid` reads, subsampled and flattened.

    ``chunk_grid`` was written for a JIRAM frame's ``(line, sample)`` arrays
    and only ever takes minima and maxima of the projected points, so a swath
    can be handed to it as one tall two-dimensional block of every band's
    subsampled framelets without changing what the answer means.
    """

    on_planet: np.ndarray
    lat: np.ndarray
    lon_east: np.ndarray


def _swath_extent(geometry: Any) -> _SwathExtent:
    cut = (slice(None, None, FRAME_STRIDE), slice(None), slice(None, None, LINE_STRIDE),
           slice(None, None, SAMPLE_STRIDE))
    samples = np.asarray(geometry.on_planet)[cut].shape[-1]
    return _SwathExtent(
        on_planet=np.asarray(geometry.on_planet)[cut].reshape(-1, samples),
        lat=np.asarray(geometry.lat)[cut].reshape(-1, samples),
        lon_east=np.asarray(geometry.lon_east)[cut].reshape(-1, samples),
    )


def build_strip(
    mirror: str | Path | None,
    row: Any,
    bands: Sequence[str],
    *,
    kernels: KernelSet | None = None,
    refine: bool = True,
) -> xr.Dataset:
    """Reproject one image's bands onto their own ``local_ortho`` grid.

    ``row`` is one row of :func:`select_images`; the orbit's kernels must
    already be furnsh'd (``kernels`` is carried only so that a caller's context
    manager is visibly alive).
    """
    root = mirror_root(mirror)
    names = tuple(str(name).upper() for name in bands)
    identifier = str(row["product_id"])
    orbit = int(row["orbit"])

    image = read_image(row, root)
    geometry = image_geometry(row, kernels, mirror=root, refine=refine, image=image)
    extent = _swath_extent(geometry)
    if not extent.on_planet.any():
        raise ValueError(f"{identifier}: no sampled pixel of the swath sees the planet")

    center = boresight_centroid(
        extent.lat[extent.on_planet], extent.lon_east[extent.on_planet]
    )
    km_per_px = resolution_class(float(row["median_pixel_km"]))
    grid, capped = chunk_grid(identifier, center, km_per_px, [extent])

    picture, counts = reproject_image(image, geometry, grid, names)
    del image
    emission, _ = reproject_image(
        np.asarray(geometry.emission, dtype=np.float64), geometry, grid, names
    )
    incidence, _ = reproject_image(
        np.asarray(geometry.incidence, dtype=np.float64), geometry, grid, names
    )
    dt_refined = float(geometry.dt_refined_s)
    epochs = np.asarray(geometry.et, dtype=np.float64)
    del geometry

    valid = np.isfinite(picture).any(axis=0)
    if not valid.any():
        raise ValueError(f"{identifier}: no band painted a pixel of the canvas")

    middle = float(np.median(epochs))
    subsolar = _subsolar_lon_east(middle)
    latitude, longitude, local_time = _lat_lon_local_time(grid, subsolar)
    # ``normalise_epoch`` refuses anything finer than a microsecond, and the
    # readout cadence lands on nanoseconds; floor as the JIRAM builder does.
    start = pd.Timestamp(row["start_time"]).floor("us")
    end = (start + pd.Timedelta(seconds=float(epochs[-1] - epochs[0]))).floor("us")
    mid = (start + pd.Timedelta(seconds=float(middle - epochs[0]))).floor("us")

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
    # ``+inf`` where a band painted nothing, then back to NaN: the plain
    # ``nanmin`` would warn on every cell no band reached, and there are many.
    best = lambda values: np.where(  # noqa: E731 - one expression, used twice
        np.isfinite(values), values, np.inf
    ).min(axis=0)
    best_incidence = best(incidence)
    best_emission = best(emission)
    with np.errstate(invalid="ignore"):
        dayside_frac = float(np.mean(np.nan_to_num(best_incidence[valid], nan=180.0) < 90.0))
        finite_emission = best_emission[valid]
        finite_emission = finite_emission[np.isfinite(finite_emission)]
        median_emission = (
            float(np.median(finite_emission)) if finite_emission.size else float("nan")
        )

    dataset = xr.Dataset(
        data_vars={
            "image": (("band", "y", "x"), picture.astype(np.float32)),
            "valid": (("y", "x"), valid),
            "emission": (("band", "y", "x"), emission.astype(np.float32)),
            "incidence": (("band", "y", "x"), incidence.astype(np.float32)),
            "n_frames": (("band", "y", "x"), counts),
        },
        coords={
            "band": ("band", np.asarray(names, dtype=object).astype(str)),
            "y_km": ("y", grid.y_km(np.arange(grid.rows))),
            "x_km": ("x", grid.x_km(np.arange(grid.cols))),
            "lat": (("y", "x"), latitude),
            "lon_east": (("y", "x"), longitude),
            "local_time_h": (("y", "x"), local_time),
            "product_ids": ("frame", np.array([identifier])),
            "frame_times": ("frame", np.array([start.to_datetime64()])),
        },
        attrs={
            "strip_id": identifier,
            "instrument": "JunoCam",
            "orbit": orbit,
            "seq_id": identifier,
            "chunk_index": 0,
            "band": ";".join(names),
            "bands": ";".join(names),
            "n_frames": int(row["n_framelets"]),
            "time_start": normalise_epoch(start),
            "time_end": normalise_epoch(end),
            "time_mid": normalise_epoch(mid),
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
            "quality_tier": str(row.get("quality_tier", "")),
            "dt_refined_s": dt_refined,
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "software": _software(),
        },
    )
    dataset["image"].attrs.update(long_name="mean reprojected radiance", units="DN")
    dataset["valid"].attrs.update(long_name="painted in at least one band")
    dataset["emission"].attrs.update(long_name="emission angle", units="degree")
    dataset["incidence"].attrs.update(long_name="incidence angle", units="degree")
    dataset["n_frames"].attrs.update(long_name="framelets averaged into this cell")
    dataset["y_km"].attrs.update(units="km", long_name="local north offset")
    dataset["x_km"].attrs.update(units="km", long_name="local east offset")
    dataset["lat"].attrs.update(units="degree_north", long_name="planetocentric latitude")
    dataset["lon_east"].attrs.update(units="degree_east")
    dataset["local_time_h"].attrs.update(units="hour", long_name="local solar time")
    return dataset


# --------------------------------------------------------------------------
# the library
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class _Task:
    mirror: str
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


def _strip_task(task: _Task) -> dict[str, Any]:
    """Build and write one strip; a failure comes back as a record, not a raise."""
    started = time.perf_counter()
    row = pd.Series(task.record)
    identifier = str(row["product_id"])
    record: dict[str, Any] = {
        "strip_id": identifier,
        "ok": False,
        "error": "",
        "seconds": np.nan,
        "row": None,
    }
    try:
        kernels = _worker_kernels(task.mirror, task.orbit)
        dataset = build_strip(
            task.mirror, row, task.bands, kernels=kernels, refine=task.refine
        )
        path = junocam_strip_path(task.mirror, task.orbit, identifier)
        write_strip(dataset, path)
        record["row"] = index_row(dataset, path, task.mirror)
        record["ok"] = True
        dataset.close()
    except Exception as exc:  # noqa: BLE001 - one bad image must not end a run
        record["error"] = " ".join(f"{type(exc).__name__}: {exc}".split())[:400]
    record["seconds"] = time.perf_counter() - started
    return record


def _results(tasks: Sequence[_Task], jobs: int) -> Iterator[dict[str, Any]]:
    if jobs <= 1 or len(tasks) < 2:
        return (_strip_task(task) for task in tasks)
    context = multiprocessing.get_context("spawn")
    pool = context.Pool(processes=jobs)

    def stream() -> Iterator[dict[str, Any]]:
        try:
            yield from pool.imap_unordered(_strip_task, tasks, chunksize=1)
        finally:
            pool.terminate()
            pool.join()

    return stream()


def update_index(
    mirror: str | Path | None, rows: Sequence[dict[str, Any]], orbits: Sequence[int]
) -> Path:
    """Replace the JunoCam rows of the selected orbits, keeping every other row.

    The replacement key is ``(orbit, instrument)`` rather than the JIRAM
    ``(orbit, band)``: a JunoCam run owns every JunoCam row of the orbits it
    was given and must not touch a JIRAM row of the same orbit.
    """
    root = mirror_root(mirror)
    path = strips_index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    selected = sorted({int(value) for value in orbits})
    fresh = _coerce_index(pd.DataFrame(list(rows), columns=list(INDEX_COLUMNS)))

    kept = fresh
    if path.exists():
        existing = _coerce_index(pd.read_parquet(path))
        replaced = existing["orbit"].isin(selected) & (
            existing["instrument"].astype(str) == "JunoCam"
        )
        keep_ids = set(fresh["strip_id"].astype(str))
        for relative in existing.loc[replaced, "path"].astype(str):
            if Path(relative).stem not in keep_ids:
                (root / relative).unlink(missing_ok=True)
        kept = pd.concat([existing.loc[~replaced], fresh], ignore_index=True)

    written = set(fresh["path"].astype(str))
    for orbit in selected:
        directory = root / "strips" / "junocam" / f"orbit{orbit:02d}"
        for candidate in directory.glob("*.nc"):
            if str(candidate.relative_to(root)) not in written:
                candidate.unlink(missing_ok=True)

    kept = kept.sort_values(
        ["instrument", "orbit", "band", "time_start", "strip_id"], kind="stable"
    ).reset_index(drop=True)
    temporary = path.with_suffix(".parquet.tmp")
    kept.to_parquet(temporary, index=False)
    temporary.replace(path)
    return path


def build_library(
    mirror: str | Path | None,
    orbits: Iterable[int] | None,
    bands: Sequence[str],
    *,
    quality_min: str = "A",
    refine: bool = True,
    jobs: int = 1,
) -> pd.DataFrame:
    """Build one strip per image of ``orbits`` and rewrite the library index."""
    root = mirror_root(mirror)
    names = tuple(str(name).upper() for name in bands)
    migrate_index(root)
    images = select_images(root, orbits, names, quality_min=quality_min)
    selected = sorted({int(value) for value in images["orbit"]}) if orbits is None else sorted(
        {int(value) for value in orbits}
    )
    if images.empty:
        raise ValueError(f"no placed JunoCam image carries {', '.join(names)}")
    LOGGER.info("building %d strip(s) in bands %s", len(images), ", ".join(names))

    columns = [name for name in (*TASK_COLUMNS, "median_pixel_km", "quality_tier") if name in images.columns]
    tasks = [
        _Task(
            mirror=str(root),
            orbit=int(row["orbit"]),
            bands=names,
            refine=bool(refine),
            record={key: row[key] for key in columns},
        )
        for _, row in images.iterrows()
    ]
    records: list[dict[str, Any]] = []
    for done, record in enumerate(_results(tasks, jobs), start=1):
        records.append(record)
        if done % 10 == 0 or done == len(tasks):
            LOGGER.info("built %d/%d strip(s)", done, len(tasks))
    rows = [record["row"] for record in records if record["ok"]]
    if not rows:
        raise ValueError("no JunoCam strip could be built")
    update_index(root, rows, selected)
    table = pd.DataFrame.from_records(records)
    LOGGER.info("wrote %d strip(s) of %d image(s)", len(rows), len(tasks))
    return table


def library_summary(table: pd.DataFrame, bands: Sequence[str]) -> str:
    """The ``junocam strips`` subcommand's report."""
    ok = table.loc[table["ok"].astype(bool)]
    lines = [
        f"bands: {', '.join(str(name).upper() for name in bands)}",
        f"strips written: {len(ok)}   failed: {len(table) - len(ok)}",
    ]
    for error, count in table.loc[~table["ok"].astype(bool), "error"].value_counts().items():
        lines.append(f"  {count:4d}  {error}")
    if not ok.empty:
        seconds = pd.to_numeric(ok["seconds"], errors="coerce")
        lines.append(
            f"seconds per strip: median {seconds.median():.1f}, max {seconds.max():.1f}, "
            f"total {seconds.sum():.0f}"
        )
    return "\n".join(lines)
