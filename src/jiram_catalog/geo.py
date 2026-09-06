"""Geometry augmentation of the frame index: one row per (frame, band half).

Archive labels carry geometry for at most one band and none at all for the
later orbits, so every frame's footprint is recomputed from SPICE. A frame is
one row per band half -- a 256-line dual-band product yields two, because the
two halves look at different parts of the planet and are geometrically
independent frames that merely share a file.

SPICE state is per process and cannot be shared across a fork, so the pool
uses the ``spawn`` start method and every worker furnsh'es the orbit's kernels
itself, once, and keeps them until it is handed a different orbit.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import spiceypy

from .geometry import (
    OBSERVER,
    TARGET,
    TARGET_FRAME,
    FrameGeometry,
    KernelSet,
    frame_geometry,
    planetocentric,
)
from .index import load_frames
from .pds import mirror_root

LOGGER = logging.getLogger(__name__)

__all__ = [
    "GEO_COLUMNS",
    "build_geo",
    "frames_with_geo",
    "geo_report_text",
    "geo_row",
    "load_geo",
    "longitude_arc",
]

#: Column order of ``index/frames_geo.parquet``.
GEO_COLUMNS: list[str] = [
    "product_id",
    "orbit_dir",
    "half",
    "start_time",
    "geo_ok",
    "geo_error",
    "on_planet_frac",
    "n_on_planet",
    "bore_lat",
    "bore_lon_east",
    "bore_emission",
    "bore_incidence",
    "bore_phase",
    "bore_range_km",
    "c1_lat",
    "c1_lon",
    "c2_lat",
    "c2_lon",
    "c3_lat",
    "c3_lon",
    "c4_lat",
    "c4_lon",
    "min_lat",
    "max_lat",
    "lon_min_east",
    "lon_max_east",
    "lon_span_deg",
    "pole_inside",
    "mean_emission",
    "mean_incidence",
    "min_emission",
    "max_emission",
    "median_pixel_km",
    "sub_sc_lat",
    "sub_sc_lon_east",
    "sc_altitude_km",
    "local_time_h",
    "dayside_frac",
]
GEO_ARROW_TYPES: dict[str, pa.DataType] = {
    "product_id": pa.string(),
    "orbit_dir": pa.int16(),
    "half": pa.string(),
    "start_time": pa.timestamp("ns"),
    "geo_ok": pa.bool_(),
    "geo_error": pa.string(),
    "n_on_planet": pa.int32(),
    "pole_inside": pa.bool_(),
}
GEO_SCHEMA = pa.schema(
    [(column, GEO_ARROW_TYPES.get(column, pa.float64())) for column in GEO_COLUMNS]
)
#: Band halves a frame of each index ``band`` contributes.
HALVES_BY_BAND: dict[str, tuple[str, ...]] = {
    "L": ("L",),
    "M": ("M",),
    "LM": ("L", "M"),
}
#: Latitude beyond which the ellipsoid pole counts as inside the footprint.
POLE_LATITUDE = 89.5
#: A longitude arc this wide is treated as covering every longitude.
FULL_ARC_DEG = 350.0
#: Frames per pool task; kernels are loaded once per worker per orbit anyway.
CHUNK_FRAMES = 32
_ERROR_CHARS = 400
#: Per-process kernel cache; never populated in the parent before the pool.
_WORKER_STATE: dict[str, Any] = {}


def default_geo_jobs() -> int:
    return min(16, os.cpu_count() or 1)


# --------------------------------------------------------------------------
# row construction (pure; no kernels needed)
# --------------------------------------------------------------------------
def longitude_arc(lon_east: np.ndarray) -> tuple[float, float, float]:
    """Smallest longitude arc containing every ``lon_east`` value, in degrees.

    Longitudes are unwrapped about their circular mean, so an arc that crosses
    the prime meridian is reported as the short way round: ``{350, 355, 0, 5}``
    gives ``(350, 5, 15)``, not ``(0, 355, 355)``. An arc of at least
    :data:`FULL_ARC_DEG` is reported as the whole circle, ``(0, 360, 360)``,
    because the circular mean of a footprint that rings the planet -- which is
    what a frame containing a pole looks like -- carries no information.
    """
    values = np.asarray(lon_east, dtype=np.float64).ravel()
    values = values[np.isfinite(values)]
    if values.size == 0:
        return (np.nan, np.nan, np.nan)
    radians = np.radians(values)
    mean = np.degrees(
        np.arctan2(np.sin(radians).mean(), np.cos(radians).mean())
    )
    offsets = ((values - mean + 180.0) % 360.0) - 180.0
    low, high = float(offsets.min()), float(offsets.max())
    span = high - low
    if span >= FULL_ARC_DEG:
        return (0.0, 360.0, 360.0)
    return ((mean + low) % 360.0, (mean + high) % 360.0, span)


def _error_text(exc: BaseException) -> str:
    """One-line, bounded description of a failure (SPICE messages are huge)."""
    text = " ".join(f"{type(exc).__name__}: {exc}".split())
    return text[:_ERROR_CHARS]


def geo_row(
    product_id: str,
    orbit_dir: int | None,
    half: str,
    start_time: Any,
    geometry: FrameGeometry | None,
    *,
    subsolar_lon_east: float = np.nan,
    error: str = "",
) -> dict[str, Any]:
    """Build one ``frames_geo`` row from a computed frame geometry.

    ``geometry=None`` records a failure: every geometric column is NaN and
    ``geo_ok`` is False, so a frame that SPICE cannot place never aborts a run.
    """
    row: dict[str, Any] = dict.fromkeys(GEO_COLUMNS, np.nan)
    row.update(
        product_id=product_id,
        orbit_dir=orbit_dir,
        half=half,
        start_time=start_time,
        geo_ok=geometry is not None,
        geo_error=error,
        n_on_planet=0,
        pole_inside=False,
    )
    if geometry is None:
        return row

    on_planet = np.asarray(geometry.on_planet, dtype=bool)
    count = int(on_planet.sum())
    row["n_on_planet"] = count
    row["on_planet_frac"] = float(on_planet.mean())

    boresight = geometry.boresight
    row["bore_lat"] = float(boresight["lat"])
    row["bore_lon_east"] = float(boresight["lon_east"])
    row["bore_emission"] = float(boresight["emission"])
    row["bore_incidence"] = float(boresight["incidence"])
    row["bore_phase"] = float(boresight["phase"])
    row["bore_range_km"] = float(boresight["range_km"])

    corners = np.asarray(geometry.corners, dtype=np.float64)
    for index in range(4):
        row[f"c{index + 1}_lat"] = float(corners[index, 0])
        row[f"c{index + 1}_lon"] = float(corners[index, 1])

    row["sub_sc_lat"] = float(geometry.sub_sc_lat)
    row["sub_sc_lon_east"] = float(geometry.sub_sc_lon_east)
    row["sc_altitude_km"] = float(geometry.sc_altitude_km)
    if np.isfinite(row["bore_lon_east"]) and np.isfinite(subsolar_lon_east):
        row["local_time_h"] = float(
            ((row["bore_lon_east"] - subsolar_lon_east) / 15.0 + 12.0) % 24.0
        )

    if count == 0:
        return row

    latitudes = np.asarray(geometry.lat, dtype=np.float64)[on_planet]
    longitudes = np.asarray(geometry.lon_east, dtype=np.float64)[on_planet]
    emission = np.asarray(geometry.emission, dtype=np.float64)[on_planet]
    incidence = np.asarray(geometry.incidence, dtype=np.float64)[on_planet]
    ranges = np.asarray(geometry.range_km, dtype=np.float64)[on_planet]

    row["min_lat"] = float(latitudes.min())
    row["max_lat"] = float(latitudes.max())
    low, high, span = longitude_arc(longitudes)
    pole_inside = (
        row["max_lat"] > POLE_LATITUDE
        or row["min_lat"] < -POLE_LATITUDE
        or span >= FULL_ARC_DEG
    )
    if pole_inside:
        low, high, span = 0.0, 360.0, 360.0
    row["lon_min_east"] = low
    row["lon_max_east"] = high
    row["lon_span_deg"] = span
    row["pole_inside"] = bool(pole_inside)

    row["mean_emission"] = float(emission.mean())
    row["mean_incidence"] = float(incidence.mean())
    row["min_emission"] = float(emission.min())
    row["max_emission"] = float(emission.max())
    row["median_pixel_km"] = float(np.median(ranges) * geometry.ifov_rad)
    row["dayside_frac"] = float((incidence < 90.0).mean())
    return row


def _empty_geo() -> pd.DataFrame:
    return coerce_geo(
        pd.DataFrame({column: pd.Series(dtype="object") for column in GEO_COLUMNS})
    )


def coerce_geo(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the geo schema and column order before Parquet output."""
    result = df.reindex(columns=GEO_COLUMNS).copy()
    result["product_id"] = result["product_id"].astype("string")
    result["half"] = result["half"].astype("string")
    result["geo_error"] = result["geo_error"].fillna("").astype("string")
    result["orbit_dir"] = pd.to_numeric(result["orbit_dir"], errors="coerce").astype(
        "Int16"
    )
    result["n_on_planet"] = pd.to_numeric(
        result["n_on_planet"], errors="coerce"
    ).astype("Int32")
    result["start_time"] = pd.to_datetime(result["start_time"], errors="coerce")
    if getattr(result["start_time"].dt, "tz", None) is not None:
        result["start_time"] = result["start_time"].dt.tz_convert(None)
    for column in ("geo_ok", "pole_inside"):
        result[column] = result[column].fillna(False).astype("boolean")
    for column in GEO_COLUMNS:
        if column not in GEO_ARROW_TYPES:
            result[column] = pd.to_numeric(result[column], errors="coerce").astype(
                "float64"
            )
    return result


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


def _subsolar_lon_east(et: float, abcorr: str = "LT+S") -> float:
    spoint, _, _ = spiceypy.subslr(
        "NEAR POINT/ELLIPSOID", TARGET, et, TARGET_FRAME, abcorr, OBSERVER
    )
    return float(planetocentric(np.asarray(spoint, dtype=np.float64))[1])


def _frame_rows(record: dict[str, Any], kernels: KernelSet) -> list[dict[str, Any]]:
    """Rows for every half of one frame; a failure never raises."""
    rows: list[dict[str, Any]] = []
    for half in HALVES_BY_BAND[record["band"]]:
        identity = (
            record["product_id"],
            record["orbit_dir"],
            half,
            record["start_time"],
        )
        if pd.isna(record["start_time"]):
            rows.append(
                geo_row(*identity, None, error="ValueError: start_time is missing")
            )
            continue
        try:
            geometry = frame_geometry(record["start_time"], half, kernels)
            subsolar = _subsolar_lon_east(geometry.et)
        except Exception as exc:  # noqa: BLE001 - one bad frame must not abort
            rows.append(geo_row(*identity, None, error=_error_text(exc)))
        else:
            rows.append(
                geo_row(*identity, geometry, subsolar_lon_east=subsolar)
            )
    return rows


def _geo_task(
    task: tuple[str, int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    mirror, orbit, records = task
    kernels = _worker_kernels(mirror, orbit)
    rows: list[dict[str, Any]] = []
    for record in records:
        rows.extend(_frame_rows(record, kernels))
    return rows


def _tasks(
    root: Path, frames: pd.DataFrame, selected: list[int], limit: int | None
) -> list[tuple[str, int, list[dict[str, Any]]]]:
    usable = frames.loc[frames["band"].isin(HALVES_BY_BAND)]
    skipped = len(frames) - len(usable)
    if skipped:
        LOGGER.info("skipping %d frame(s) whose band is not L, M or LM", skipped)
    tasks: list[tuple[str, int, list[dict[str, Any]]]] = []
    for orbit in selected:
        part = usable.loc[usable["orbit_dir"] == orbit].sort_values(
            ["start_time", "product_id"], kind="stable", na_position="last"
        )
        if limit is not None:
            part = part.head(limit)
        records = [
            {
                "product_id": str(row.product_id),
                "orbit_dir": int(orbit),
                "band": str(row.band),
                "start_time": row.start_time,
            }
            for row in part.itertuples(index=False)
        ]
        for start in range(0, len(records), CHUNK_FRAMES):
            tasks.append((str(root), orbit, records[start : start + CHUNK_FRAMES]))
    return tasks


def _write_geo(df: pd.DataFrame, path: Path) -> None:
    arrays = [
        pa.array(df[column], type=GEO_SCHEMA.field(column).type, from_pandas=True)
        for column in GEO_COLUMNS
    ]
    table = pa.Table.from_arrays(arrays, schema=GEO_SCHEMA)
    temporary = path.with_suffix(".parquet.tmp")
    pq.write_table(table, temporary)
    temporary.replace(path)


def build_geo(
    mirror: str | Path | None = None,
    orbits: Iterable[int] | None = None,
    jobs: int | None = None,
    limit: int | None = None,
) -> pd.DataFrame:
    """Compute geometry for the selected orbits and rewrite ``frames_geo``.

    Rows of orbits that were not selected are carried over from the existing
    file unchanged; selected orbits are replaced wholesale.
    """
    root = mirror_root(mirror)
    frames = load_frames(root, orbits)
    if orbits is None:
        selected = sorted(int(value) for value in frames["orbit_dir"].dropna().unique())
    else:
        selected = sorted({int(value) for value in orbits})

    tasks = _tasks(root, frames, selected, limit)
    worker_count = default_geo_jobs() if jobs is None else int(jobs)
    if worker_count < 1:
        raise ValueError("--jobs must be at least 1")
    LOGGER.info(
        "computing geometry for %d orbit(s) in %d task(s) on %d process(es)",
        len(selected),
        len(tasks),
        worker_count,
    )
    if worker_count == 1 or len(tasks) < 2:
        results = [_geo_task(task) for task in tasks]
    else:
        # spawn, never fork: SPICE state is per process and a forked child
        # would inherit a furnsh'd but unusable handle table.
        context = multiprocessing.get_context("spawn")
        with context.Pool(processes=worker_count) as pool:
            results = pool.map(_geo_task, tasks)
    rows = [row for chunk in results for row in chunk]
    new = coerce_geo(pd.DataFrame.from_records(rows)) if rows else _empty_geo()

    output_dir = root / "index"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "frames_geo.parquet"
    existing = (
        coerce_geo(pd.read_parquet(output_path)) if output_path.exists() else _empty_geo()
    )
    kept = existing.loc[~existing["orbit_dir"].isin(selected)]
    combined = pd.concat([kept, new], ignore_index=True)
    combined = combined.sort_values(
        ["orbit_dir", "start_time", "product_id", "half"],
        kind="stable",
        na_position="last",
        ignore_index=True,
    )
    combined = coerce_geo(combined)
    _write_geo(combined, output_path)
    LOGGER.info("wrote %d geo rows to %s", len(combined), output_path)
    return combined


# --------------------------------------------------------------------------
# loading and merging
# --------------------------------------------------------------------------
def load_geo(
    mirror: str | Path | None = None,
    orbits: Iterable[int] | None = None,
) -> pd.DataFrame:
    """Load the geometry table, optionally retaining only selected orbit dirs."""
    path = mirror_root(mirror) / "index" / "frames_geo.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"geometry table not found: {path}; run: jiram-catalog geo --orbits ..."
        )
    geo = coerce_geo(pd.read_parquet(path))
    if orbits is not None:
        selected = sorted({int(value) for value in orbits})
        geo = geo.loc[geo["orbit_dir"].isin(selected)]
    return geo.reset_index(drop=True)


def frames_with_geo(
    mirror: str | Path | None = None,
    orbits: Iterable[int] | None = None,
) -> pd.DataFrame:
    """Index rows joined to their geometry: one row per (frame, band half).

    Index columns come first and keep their names; the geometry columns keep
    theirs too, except the four whose names the label already uses
    (``min_lat``, ``max_lat``, ``sub_sc_lat``, ``sc_altitude_km``), which are
    suffixed ``_geo`` so that label and SPICE values stay side by side.
    """
    root = mirror_root(mirror)
    frames = load_frames(root, orbits)
    geo = load_geo(root, orbits).drop(columns=["orbit_dir", "start_time"])
    clashes = (set(frames.columns) & set(geo.columns)) - {"product_id"}
    geo = geo.rename(columns={column: f"{column}_geo" for column in sorted(clashes)})
    return frames.merge(geo, on="product_id", how="inner", sort=False).reset_index(
        drop=True
    )


# --------------------------------------------------------------------------
# consistency report
# --------------------------------------------------------------------------
def great_circle_deg(
    lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray
) -> np.ndarray:
    """Great-circle separation in degrees, inputs in degrees."""
    first, second = np.radians(lat1), np.radians(lat2)
    cosine = np.sin(first) * np.sin(second) + np.cos(first) * np.cos(second) * np.cos(
        np.radians(np.asarray(lon1, dtype=np.float64) - np.asarray(lon2, dtype=np.float64))
    )
    return np.degrees(np.arccos(np.clip(cosine, -1.0, 1.0)))


def _label_comparison(frames: pd.DataFrame, geo: pd.DataFrame) -> pd.DataFrame:
    """Differences between SPICE geometry and the label, where both exist."""
    labels = frames.loc[
        frames["geom_band"].isin(["L", "M"]) & frames["center_lat"].notna(),
        [
            "product_id",
            "geom_band",
            "center_lat",
            "center_lon",
            "emission_angle",
            "min_lat",
            "target_name",
        ],
    ].rename(columns={"min_lat": "label_min_lat"})
    merged = geo.merge(labels, on="product_id", how="inner")
    merged = merged.loc[merged["geom_band"] == merged["half"]]
    return pd.DataFrame(
        {
            "orbit_dir": merged["orbit_dir"],
            "target_name": merged["target_name"],
            "centre_deg": great_circle_deg(
                merged["bore_lat"].to_numpy(),
                merged["bore_lon_east"].to_numpy(),
                merged["center_lat"].to_numpy(),
                merged["center_lon"].to_numpy(),
            ),
            "emission_deg": (merged["bore_emission"] - merged["emission_angle"]).abs(),
            "min_lat_deg": (merged["min_lat"] - merged["label_min_lat"]).abs(),
        }
    )


_COMPARISONS: tuple[tuple[str, str], ...] = (
    ("centre_deg", "boresight against label centre, great-circle distance (deg)"),
    ("emission_deg", "boresight emission against label EMISSION_ANGLE (deg)"),
    ("min_lat_deg", "frame minimum latitude against label MINIMUM_LATITUDE (deg)"),
)


def _statistic_line(label: str, values: pd.Series) -> str:
    usable = values.dropna()
    if usable.empty:
        return f"- {label}: no comparable rows"
    return (
        f"- {label}: median {usable.median():.6f}, "
        f"95th percentile {usable.quantile(0.95):.6f}, n = {len(usable)}"
    )


def geo_report_text(
    frames: pd.DataFrame, geo: pd.DataFrame, orbits: Iterable[int]
) -> str:
    """Format the consistency report for the orbits this run computed."""
    selected = sorted({int(value) for value in orbits})
    failures = int((~geo["geo_ok"].fillna(False)).sum())
    comparison = _label_comparison(frames, geo)
    off_target = int(
        (comparison["target_name"].astype("string").str.upper() != "JUPITER").sum()
    )
    lines = [
        "# JIRAM geometry consistency report",
        "",
        f"Orbits: {', '.join(str(orbit) for orbit in selected) or 'none'}",
        f"Rows (one per frame and band half): {len(geo)}",
        f"Rows with geo_ok = False: {failures}",
        f"Rows comparable with a label centre: {len(comparison)}",
        f"... of which the label's target is not Jupiter: {off_target}",
        "",
        "A label centre for another body (a satellite encounter) leaves the",
        "Jupiter boresight off the ellipsoid, so those rows drop out of every",
        "statistic below; each line reports the number of rows it used.",
        "",
        "## SPICE geometry against the archive labels",
        "",
    ]
    lines.extend(
        _statistic_line(label, comparison[column]) for column, label in _COMPARISONS
    )
    lines.extend(["", "## Per orbit", ""])
    header = [
        "orbit",
        "rows",
        "not ok",
        "compared",
        "centre p50",
        "centre p95",
        "emission p50",
        "min_lat p50",
    ]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for orbit in selected:
        part = geo.loc[geo["orbit_dir"] == orbit]
        same = comparison.loc[comparison["orbit_dir"] == orbit]

        def cell(column: str, quantile: float) -> str:
            values = same[column].dropna()
            return "-" if values.empty else f"{values.quantile(quantile):.6f}"

        lines.append(
            "| "
            + " | ".join(
                [
                    str(orbit),
                    str(len(part)),
                    str(int((~part["geo_ok"].fillna(False)).sum())),
                    str(len(same)),
                    cell("centre_deg", 0.5),
                    cell("centre_deg", 0.95),
                    cell("emission_deg", 0.5),
                    cell("min_lat_deg", 0.5),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Footprint coverage", ""])
    on_planet = geo["on_planet_frac"].dropna()
    lines.append(f"- rows with any on-planet pixel: {int((on_planet > 0).sum())}")
    lines.append(f"- rows fully on the planet: {int((on_planet == 1.0).sum())}")
    lines.append(f"- rows with pole_inside: {int(geo['pole_inside'].fillna(False).sum())}")
    return "\n".join(lines) + "\n"
