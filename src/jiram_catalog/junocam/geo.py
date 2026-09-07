"""``junocam_geo.parquet``: where every JunoCam image looked, and how big.

This is :mod:`jiram_catalog.geo` for a camera whose unit of geometry is not a
frame but a swath.  A JIRAM frame is one 128 x 432 detector at one epoch, so
its footprint is a quadrilateral and four corners describe it exactly.  A
JunoCam product is a *time series* of framelets swept across the scene by the
spacecraft's spin, so its footprint is a long curved ribbon that four corners
would misrepresent by thousands of kilometres.  The catalog row therefore
carries an outline -- up to :data:`MAX_VERTICES` vertices traced round the
on-planet part of the swath -- where the JIRAM row carries ``c1..c4``.

Everything else is the JIRAM recipe, deliberately: the longitude arc is
:func:`jiram_catalog.geo.longitude_arc`, ``pole_inside`` is that module's rule
(:data:`~jiram_catalog.geo.POLE_LATITUDE` or a full arc), and a failure is
recorded as a row rather than raised, so one unplaceable product never aborts
a survey.

**Why a subsample.**  The survey question -- where did this image look, how
coarse are its pixels, was it lit -- is answered to a fraction of a degree by
every fourth frame and every eighth line and sample, which is 1/256 of the
samples and, on this archive, the difference between a minute and four hours.
The full-resolution geometry is what the *stack* and *strip* builders ask for,
one image at a time, and they pay for the limb fit as well.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import time
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from ..config import mirror_root
from ..geo import FULL_ARC_DEG, POLE_LATITUDE, longitude_arc
from ..geometry import KernelSet
from .camera import band_cameras
from .geometry import image_geometry
from .index import load_images
from .mirror import junocam_root

LOGGER = logging.getLogger(__name__)

__all__ = [
    "GEO_COLUMNS",
    "MAX_VERTICES",
    "build_geo",
    "default_geo_jobs",
    "footprint_outline",
    "geo_path",
    "geo_row",
    "geo_summary",
    "load_geo",
]

#: Strides of the survey subsample: every fourth frame, every eighth line and
#: sample of each framelet.
FRAME_STRIDE = 4
LINE_STRIDE = 8
SAMPLE_STRIDE = 8

#: Hard cap on the vertices of a footprint outline (the contract's list size).
MAX_VERTICES = 64
#: An outline shorter than this cannot describe a ribbon, and the row that
#: would carry it is recorded as a failure instead.
MIN_VERTICES = 8

_ERROR_CHARS = 400

#: Column order of ``junocam/index/junocam_geo.parquet``.
GEO_COLUMNS: list[str] = [
    "product_id",
    "orbit",
    "level",
    "start_time",
    "bands",
    "n_bands",
    "n_framelets",
    "geo_ok",
    "geo_error",
    "on_planet_frac",
    "n_on_planet",
    "bore_lat",
    "bore_lon_east",
    "bore_emission",
    "bore_incidence",
    "median_pixel_km",
    "dayside_frac",
    "min_lat",
    "max_lat",
    "lon_min_east",
    "lon_max_east",
    "lon_span_deg",
    "pole_inside",
    "fp_lon",
    "fp_lat",
    "n_vertices",
    "seconds",
]

_GEO_TYPES: dict[str, pa.DataType] = {
    "product_id": pa.string(),
    "orbit": pa.int16(),
    "level": pa.string(),
    "start_time": pa.timestamp("ns"),
    "bands": pa.string(),
    "n_bands": pa.int16(),
    "n_framelets": pa.int32(),
    "geo_ok": pa.bool_(),
    "geo_error": pa.string(),
    "n_on_planet": pa.int64(),
    "pole_inside": pa.bool_(),
    "fp_lon": pa.list_(pa.float32()),
    "fp_lat": pa.list_(pa.float32()),
    "n_vertices": pa.int32(),
}
GEO_SCHEMA = pa.schema(
    [(column, _GEO_TYPES.get(column, pa.float64())) for column in GEO_COLUMNS]
)

#: Per-process kernel cache; never populated in the parent before the pool.
_WORKER_STATE: dict[str, Any] = {}


def default_geo_jobs() -> int:
    return min(8, os.cpu_count() or 1)


def geo_path(mirror: str | Path | None = None) -> Path:
    """``<mirror>/junocam/index/junocam_geo.parquet``."""
    return junocam_root(mirror) / "index" / "junocam_geo.parquet"


def _error_text(exc: BaseException) -> str:
    return " ".join(f"{type(exc).__name__}: {exc}".split())[:_ERROR_CHARS]


# --------------------------------------------------------------------------
# the footprint outline
# --------------------------------------------------------------------------
def footprint_outline(
    mask: np.ndarray, lat: np.ndarray, lon_east: np.ndarray, *, max_vertices: int = MAX_VERTICES
) -> tuple[np.ndarray, np.ndarray]:
    """Trace the boundary of a swath's on-planet mask, in image order.

    ``mask``, ``lat`` and ``lon_east`` are the subsampled swath of one band
    laid out as the image is on disk: rows advance with the readout (frame
    after frame) and columns across the framelet.  For a pushframe camera that
    makes the array a real picture of the ground, so the first and last
    on-planet row of each column bound the ribbon, and walking the tops left to
    right and the bottoms right to left closes a ring in image order.

    The ring is then thinned to at most ``max_vertices`` by taking evenly
    spaced vertices, which keeps the two ends of the swath and the turn at each
    corner while dropping the collinear runs between them.  A mask too narrow
    for a column-wise trace (a swath that grazes the limb along a few columns)
    is traced row-wise instead; if even that yields fewer than
    :data:`MIN_VERTICES` vertices the caller records the row as a failure.
    """
    mask = np.asarray(mask, dtype=bool)
    if mask.ndim != 2:
        raise ValueError(f"the outline needs a 2-D mask, not {mask.shape}")
    ring = _ring_indices(mask)
    if ring.shape[0] < MIN_VERTICES:
        ring = _ring_indices(mask.T)
        if ring.shape[0] >= MIN_VERTICES:
            ring = ring[:, ::-1]
    if ring.shape[0] == 0:
        return np.empty(0, dtype=np.float32), np.empty(0, dtype=np.float32)
    if ring.shape[0] > max_vertices:
        take = np.unique(
            np.round(np.linspace(0, ring.shape[0] - 1, max_vertices)).astype(np.intp)
        )
        ring = ring[take]
    rows, cols = ring[:, 0], ring[:, 1]
    return (
        np.asarray(lon_east, dtype=np.float64)[rows, cols].astype(np.float32),
        np.asarray(lat, dtype=np.float64)[rows, cols].astype(np.float32),
    )


def _ring_indices(mask: np.ndarray) -> np.ndarray:
    """``(N, 2)`` row/column indices of the top-then-bottom ring of ``mask``."""
    columns = np.flatnonzero(mask.any(axis=0))
    if columns.size < 2:
        return np.empty((0, 2), dtype=np.intp)
    top = np.argmax(mask[:, columns], axis=0)
    bottom = mask.shape[0] - 1 - np.argmax(mask[::-1, columns], axis=0)
    forward = np.stack([top, columns], axis=1)
    backward = np.stack([bottom[::-1], columns[::-1]], axis=1)
    ring = np.concatenate([forward, backward], axis=0)
    keep = np.ones(ring.shape[0], dtype=bool)
    keep[1:] = np.any(ring[1:] != ring[:-1], axis=1)
    return ring[keep]


# --------------------------------------------------------------------------
# one row
# --------------------------------------------------------------------------
def _empty_row(row: Any, error: str = "") -> dict[str, Any]:
    record: dict[str, Any] = dict.fromkeys(GEO_COLUMNS, np.nan)
    record.update(
        product_id=str(row["product_id"]),
        orbit=None if pd.isna(row.get("orbit")) else int(row["orbit"]),
        level=str(row.get("level", "")),
        start_time=row.get("start_time"),
        bands=str(row.get("filters", "")),
        n_bands=None if pd.isna(row.get("n_bands")) else int(row["n_bands"]),
        n_framelets=None if pd.isna(row.get("n_framelets")) else int(row["n_framelets"]),
        geo_ok=False,
        geo_error=error,
        n_on_planet=0,
        pole_inside=False,
        fp_lon=np.empty(0, dtype=np.float32),
        fp_lat=np.empty(0, dtype=np.float32),
        n_vertices=0,
        seconds=np.nan,
    )
    return record


def geo_row(row: Any, geometry: Any, *, seconds: float = np.nan) -> dict[str, Any]:
    """One ``junocam_geo`` row from a subsampled :class:`ImageGeometry`.

    ``geometry`` still carries the full arrays; the strides are applied here so
    that the survey's definition of "subsample" lives in one place.
    """
    record = _empty_row(row)
    record["seconds"] = float(seconds)

    on_planet = np.asarray(geometry.on_planet)[::FRAME_STRIDE, :, ::LINE_STRIDE, ::SAMPLE_STRIDE]
    latitude = np.asarray(geometry.lat, dtype=np.float64)[
        ::FRAME_STRIDE, :, ::LINE_STRIDE, ::SAMPLE_STRIDE
    ]
    longitude = np.asarray(geometry.lon_east, dtype=np.float64)[
        ::FRAME_STRIDE, :, ::LINE_STRIDE, ::SAMPLE_STRIDE
    ]
    ranges = np.asarray(geometry.range_km, dtype=np.float64)[
        ::FRAME_STRIDE, :, ::LINE_STRIDE, ::SAMPLE_STRIDE
    ]
    emission = np.asarray(geometry.emission, dtype=np.float64)[
        ::FRAME_STRIDE, :, ::LINE_STRIDE, ::SAMPLE_STRIDE
    ]
    incidence = np.asarray(geometry.incidence, dtype=np.float64)[
        ::FRAME_STRIDE, :, ::LINE_STRIDE, ::SAMPLE_STRIDE
    ]

    count = int(on_planet.sum())
    record["n_on_planet"] = count
    record["on_planet_frac"] = float(on_planet.mean())
    if count == 0:
        record["geo_error"] = "no sampled pixel of the swath sees the planet"
        return record

    # Pixel scale: range x IFOV, and the IFOV is one over the focal length
    # expressed in pixel widths -- which is exactly what ``BandCamera.fl`` is.
    inverse_fl = np.array(
        [1.0 / camera.fl for camera in band_cameras(geometry.bands)], dtype=np.float64
    ).reshape(1, -1, 1, 1)
    pixel_km = (ranges * inverse_fl)[on_planet]
    record["median_pixel_km"] = float(np.median(pixel_km))
    record["dayside_frac"] = float((incidence[on_planet] < 90.0).mean())

    latitudes = latitude[on_planet]
    longitudes = longitude[on_planet]
    record["min_lat"] = float(latitudes.min())
    record["max_lat"] = float(latitudes.max())
    low, high, span = longitude_arc(longitudes)
    pole_inside = (
        record["max_lat"] > POLE_LATITUDE
        or record["min_lat"] < -POLE_LATITUDE
        or span >= FULL_ARC_DEG
    )
    if pole_inside:
        low, high, span = 0.0, 360.0, 360.0
    record["lon_min_east"] = float(low)
    record["lon_max_east"] = float(high)
    record["lon_span_deg"] = float(span)
    record["pole_inside"] = bool(pole_inside)

    _swath_centre(record, on_planet, latitude, longitude, emission, incidence)

    reference = int(np.argmax(on_planet.reshape(on_planet.shape[0], on_planet.shape[1], -1).sum(axis=(0, 2))))
    shape = (-1, on_planet.shape[3])
    fp_lon, fp_lat = footprint_outline(
        on_planet[:, reference].reshape(shape),
        latitude[:, reference].reshape(shape),
        longitude[:, reference].reshape(shape),
    )
    record["fp_lon"] = fp_lon
    record["fp_lat"] = fp_lat
    record["n_vertices"] = int(fp_lon.size)
    if fp_lon.size < MIN_VERTICES:
        record["geo_error"] = (
            f"footprint outline has only {fp_lon.size} vertices"
        )
        return record
    record["geo_ok"] = True
    return record


def _swath_centre(
    record: dict[str, Any],
    on_planet: np.ndarray,
    latitude: np.ndarray,
    longitude: np.ndarray,
    emission: np.ndarray,
    incidence: np.ndarray,
) -> None:
    """Fill the ``bore_*`` columns from the middle of the swath.

    The nominal point is the centre pixel of the middle band of the middle
    frame -- the boresight of the readout half way through the spin sweep.  A
    swath that only catches the planet at one end has no planet there, so the
    search widens to the on-planet sample nearest in *readout order* -- the
    next framelet, the next line, the next sample -- which for a pushframe
    camera is also the nearest on the ground.  What the column then means is
    "where the middle of the on-planet part of this swath looked", which is
    the question a catalog row is asked, and it is finite whenever the image
    saw the planet at all.
    """
    frames, bands, lines, samples = on_planet.shape
    centre = (frames // 2, bands // 2, lines // 2, samples // 2)
    if not on_planet[centre]:
        flat = on_planet.reshape(-1)
        index = np.ravel_multi_index(centre, on_planet.shape)
        candidates = np.flatnonzero(flat)
        centre = np.unravel_index(
            int(candidates[np.argmin(np.abs(candidates - index))]), on_planet.shape
        )
    record["bore_lat"] = float(latitude[centre])
    record["bore_lon_east"] = float(longitude[centre])
    record["bore_emission"] = float(emission[centre])
    record["bore_incidence"] = float(incidence[centre])


# --------------------------------------------------------------------------
# workers
# --------------------------------------------------------------------------
def _worker_kernels(mirror: str, orbit: int) -> KernelSet:
    """The furnsh'd kernel set of ``orbit`` in this process, loaded once."""
    cached = _WORKER_STATE.get("kernels")
    if cached is not None and _WORKER_STATE.get("key") == (mirror, orbit):
        return cached
    if cached is not None:
        cached.unload()
        _WORKER_STATE.clear()
    kernels = KernelSet.for_orbits(mirror, [orbit])
    _WORKER_STATE.update(key=(mirror, orbit), kernels=kernels)
    return kernels


def _geo_task(task: tuple[str, int, dict[str, Any]]) -> dict[str, Any]:
    """One image: place it, or record why it could not be placed."""
    mirror, orbit, record = task
    row = pd.Series(record)
    started = time.perf_counter()
    try:
        kernels = _worker_kernels(mirror, orbit)
        geometry = image_geometry(
            row, kernels, mirror=mirror, refine=False, cache=False
        )
    except Exception as exc:  # noqa: BLE001 - one bad product must not end a survey
        return _empty_row(row, _error_text(exc))
    result = geo_row(row, geometry, seconds=time.perf_counter() - started)
    del geometry
    return result


#: The index columns a worker needs; the rest of the index (the whole PDS
#: label as JSON, among others) would be pickled into every task for nothing.
TASK_COLUMNS: tuple[str, ...] = (
    "product_id",
    "orbit",
    "level",
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


def _tasks(mirror: Path, images: pd.DataFrame) -> list[tuple[str, int, dict[str, Any]]]:
    """One task per image, ordered by orbit so a worker reloads kernels rarely."""
    ordered = images.sort_values(["orbit", "start_time", "product_id"], kind="stable")
    columns = [name for name in TASK_COLUMNS if name in ordered.columns]
    return [
        (str(mirror), int(row["orbit"]), {key: row[key] for key in columns})
        for _, row in ordered[columns].iterrows()
    ]


def _results(
    tasks: Sequence[tuple[str, int, dict[str, Any]]], jobs: int
) -> Iterable[dict[str, Any]]:
    if jobs <= 1 or len(tasks) < 2:
        return (_geo_task(task) for task in tasks)
    # spawn, never fork: SPICE state is per process and a forked child would
    # inherit a furnsh'd but unusable handle table.
    context = multiprocessing.get_context("spawn")
    pool = context.Pool(processes=jobs)

    def stream():
        try:
            yield from pool.imap(_geo_task, tasks, chunksize=1)
        finally:
            pool.terminate()
            pool.join()

    return stream()


# --------------------------------------------------------------------------
# the table
# --------------------------------------------------------------------------
def coerce_geo(table: pd.DataFrame) -> pd.DataFrame:
    """Apply the geo schema and column order before Parquet output."""
    result = table.reindex(columns=GEO_COLUMNS).copy()
    for name in ("product_id", "level", "bands", "geo_error"):
        result[name] = result[name].fillna("").astype("string")
    for name, dtype in (("orbit", "Int16"), ("n_bands", "Int16"), ("n_framelets", "Int32")):
        result[name] = pd.to_numeric(result[name], errors="coerce").astype(dtype)
    result["n_on_planet"] = pd.to_numeric(result["n_on_planet"], errors="coerce").astype("Int64")
    result["n_vertices"] = pd.to_numeric(result["n_vertices"], errors="coerce").astype("Int32")
    result["start_time"] = pd.to_datetime(result["start_time"], errors="coerce")
    if getattr(result["start_time"].dt, "tz", None) is not None:
        result["start_time"] = result["start_time"].dt.tz_convert(None)
    for name in ("geo_ok", "pole_inside"):
        result[name] = result[name].fillna(False).astype("boolean")
    for name in ("fp_lon", "fp_lat"):
        result[name] = [
            np.asarray([] if value is None or np.isscalar(value) else value, dtype=np.float32)
            for value in result[name]
        ]
    for name in GEO_COLUMNS:
        if name not in _GEO_TYPES:
            result[name] = pd.to_numeric(result[name], errors="coerce").astype("float64")
    return result


def _empty_geo() -> pd.DataFrame:
    return coerce_geo(
        pd.DataFrame({column: pd.Series(dtype="object") for column in GEO_COLUMNS})
    )


def _write_geo(table: pd.DataFrame, path: Path) -> None:
    arrays = [
        pa.array(table[column].tolist(), type=GEO_SCHEMA.field(column).type)
        if column in ("fp_lon", "fp_lat")
        else pa.array(table[column], type=GEO_SCHEMA.field(column).type, from_pandas=True)
        for column in GEO_COLUMNS
    ]
    temporary = path.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_arrays(arrays, schema=GEO_SCHEMA), temporary)
    temporary.replace(path)


def build_geo(
    mirror: str | Path | None = None,
    orbits: Iterable[int] | None = None,
    jobs: int | None = None,
) -> pd.DataFrame:
    """Place every mirrored RDR image of the selected orbits.

    Rows of the selected orbits replace the previous ones and every other
    orbit already in the table is left alone, exactly as the index and the
    quality table grow.
    """
    root = mirror_root(mirror)
    output = geo_path(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = coerce_geo(pd.read_parquet(output)) if output.exists() else _empty_geo()

    images = load_images(root, orbits)
    images = images.loc[
        (images["level"].astype(str) == "RDR")
        & images["img_present"].fillna(False).astype(bool)
        & images["orbit"].notna()
    ].reset_index(drop=True)
    selected = sorted({int(value) for value in images["orbit"]}) if orbits is None else sorted(
        {int(value) for value in orbits}
    )
    LOGGER.info("placing %d mirrored RDR image(s) of %d orbit(s)", len(images), len(selected))

    worker_count = default_geo_jobs() if jobs is None else int(jobs)
    if worker_count < 1:
        raise ValueError("--jobs must be at least 1")
    records: list[dict[str, Any]] = []
    started = time.perf_counter()
    for done, record in enumerate(_results(_tasks(root, images), worker_count), start=1):
        records.append(record)
        if done % 25 == 0 or done == len(images):
            LOGGER.info("placed %d/%d image(s)", done, len(images))

    fresh = coerce_geo(pd.DataFrame.from_records(records)) if records else _empty_geo()
    kept = existing.loc[~existing["orbit"].isin(selected)]
    combined = pd.concat([kept, fresh], ignore_index=True)
    combined = combined.drop_duplicates("product_id", keep="last")
    combined = coerce_geo(
        combined.sort_values(
            ["orbit", "start_time", "product_id"],
            kind="stable",
            na_position="last",
            ignore_index=True,
        )
    )
    _write_geo(combined, output)
    LOGGER.info(
        "wrote %d geo rows to %s in %.1f s", len(combined), output, time.perf_counter() - started
    )
    return combined


def load_geo(
    mirror: str | Path | None = None, orbits: Iterable[int] | None = None
) -> pd.DataFrame:
    """Read the geometry table, optionally restricted to some orbits."""
    path = geo_path(mirror)
    if not path.exists():
        raise FileNotFoundError(
            f"JunoCam geometry table not found: {path} (run `junocam geo --orbits ...`)"
        )
    table = coerce_geo(pd.read_parquet(path))
    if orbits is not None:
        table = table.loc[table["orbit"].isin(sorted({int(value) for value in orbits}))]
    return table.reset_index(drop=True)


def geo_summary(table: pd.DataFrame) -> str:
    """The ``junocam geo`` subcommand's report."""
    ok = table.loc[table["geo_ok"].fillna(False).astype(bool)]
    lines = [
        f"geo rows: {len(table)}",
        f"placed: {len(ok)}   failed: {len(table) - len(ok)}",
    ]
    for error, count in table.loc[~table["geo_ok"].fillna(False).astype(bool), "geo_error"].value_counts().items():
        lines.append(f"  {count:4d}  {error}")
    if ok.empty:
        return "\n".join(lines)
    lines.append("orbit  rows   on_planet   px_km(med)   lat range        vertices")
    for orbit, part in ok.groupby("orbit", sort=True, dropna=True):
        lines.append(
            f"{int(orbit):5d}  {len(part):4d}   {part['on_planet_frac'].mean():9.3f}   "
            f"{part['median_pixel_km'].median():10.2f}   "
            f"{part['min_lat'].min():7.2f}..{part['max_lat'].max():6.2f}   "
            f"{int(part['n_vertices'].median()):8d}"
        )
    lines.append(f"seconds per image: median {ok['seconds'].median():.2f}, max {ok['seconds'].max():.2f}")
    return "\n".join(lines)
