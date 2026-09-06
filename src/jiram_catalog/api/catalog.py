"""The frame catalog: one Arrow table, the filters, and the summary.

The whole point of the v2 front end is that the catalog crosses the wire
once.  47,600 rows of twenty-odd typed columns is about 4 MB of Arrow and
under a second to build, so the server builds it on the first request,
keeps it, and answers every later request from memory with an ``ETag``;
the browser filters and re-bins locally and only asks the server again
when the mirror's index files have changed.

The summary endpoint exists anyway, for a client that wants the counts
without the table (a link, a report, a smoke test), and it applies the
same missing-value rule the browser does: a threshold drops a row only
when the row's value is known and fails it.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
from fastapi import APIRouter, HTTPException, Query, Request, Response

from ..config import mirror_root
from ..geo import frames_with_geo
from . import data
from .arrow import ARROW_MEDIA_TYPE, epoch_ms, frame_to_ipc
from .data import LAT_BAND_NAMES

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

#: The contract's schema, in the contract's order.
CATALOG_SCHEMA = pa.schema(
    [
        pa.field("product_id", pa.string()),
        pa.field("half", pa.string()),
        pa.field("band", pa.string()),
        pa.field("orbit", pa.int16()),
        pa.field("seq_id", pa.string()),
        pa.field("start_time_ms", pa.int64()),
        pa.field("bore_lat", pa.float32()),
        pa.field("bore_lon_east", pa.float32()),
        pa.field("bore_emission", pa.float32()),
        pa.field("on_planet_frac", pa.float32()),
        pa.field("median_pixel_km", pa.float32()),
        pa.field("dayside_frac", pa.float32()),
        pa.field("min_lat", pa.float32()),
        pa.field("max_lat", pa.float32()),
        pa.field("lon_span_deg", pa.float32()),
        pa.field("pole_inside", pa.bool_()),
        pa.field("c1_lat", pa.float32()),
        pa.field("c1_lon", pa.float32()),
        pa.field("c2_lat", pa.float32()),
        pa.field("c2_lon", pa.float32()),
        pa.field("c3_lat", pa.float32()),
        pa.field("c3_lon", pa.float32()),
        pa.field("c4_lat", pa.float32()),
        pa.field("c4_lon", pa.float32()),
        pa.field("has_partner", pa.bool_()),
        pa.field("best_dt_s", pa.float32()),
    ]
)

#: Index files whose modification times define the catalog's ``ETag``.
INDEX_FILES: tuple[str, ...] = (
    "index/frames.parquet",
    "index/frames_geo.parquet",
    "index/trackability_frames.parquet",
)

_TABLE_LOCK = threading.Lock()
_TABLE_CACHE: dict[str, pd.DataFrame] = {}
_IPC_CACHE: dict[str, tuple[str, bytes]] = {}
_FULL_CACHE: dict[str, pd.DataFrame] = {}


# ---------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------
def index_etag(mirror: str | Path | None = None) -> str:
    """A weak identity for the catalog: the index files' sizes and mtimes.

    Cheaper than hashing 33 MB of parquet on every conditional request,
    and it changes for exactly the events that change the table -- a
    rebuilt geometry table, a newly written trackability table.
    """
    root = mirror_root(mirror)
    digest = hashlib.sha256()
    for relative in INDEX_FILES:
        path = root / relative
        try:
            stat = path.stat()
        except OSError:
            digest.update(f"{relative}:absent".encode())
        else:
            digest.update(f"{relative}:{stat.st_mtime_ns}:{stat.st_size}".encode())
    return f'"{digest.hexdigest()[:24]}"'


def _first_present(frame: pd.DataFrame, *names: str) -> pd.Series:
    """The first of ``names`` the frame carries, else an all-missing column."""
    for name in names:
        if name in frame.columns:
            return frame[name]
    return pd.Series([np.nan] * len(frame), index=frame.index)


def catalog_frame(mirror: str | Path | None = None) -> pd.DataFrame:
    """The contract's rows and columns, built once per mirror and kept.

    Rows are the ``geo_ok`` frames that see the planet, one per band
    half.  ``min_lat``/``max_lat`` are the geometry engine's, not the
    label's: the labels carry no geometry after orbit 38, so the label
    columns are missing for most of the archive while the SPICE ones are
    complete.
    """
    root = mirror_root(mirror)
    key = str(root)
    with _TABLE_LOCK:
        cached = _TABLE_CACHE.get(key)
    if cached is not None:
        return cached

    frames = frames_with_geo(root)
    mask = (
        frames["geo_ok"].fillna(False).to_numpy(dtype=bool)
        & (frames["on_planet_frac"].to_numpy(dtype=np.float64, na_value=np.nan) > 0.0)
    )
    source = frames.loc[mask].reset_index(drop=True)

    table = pd.DataFrame(index=range(len(source)))
    for name in ("product_id", "half", "band", "seq_id"):
        table[name] = source[name].astype(object).where(source[name].notna(), None)
    table["orbit"] = pd.to_numeric(source["orbit_dir"], errors="coerce").fillna(0).astype("int64")
    table["start_time"] = pd.to_datetime(source["start_time"])
    table["start_time_ms"] = epoch_ms(source["start_time"])
    for name in (
        "bore_lat",
        "bore_lon_east",
        "bore_emission",
        "on_planet_frac",
        "median_pixel_km",
        "dayside_frac",
        "lon_span_deg",
        "c1_lat",
        "c1_lon",
        "c2_lat",
        "c2_lon",
        "c3_lat",
        "c3_lon",
        "c4_lat",
        "c4_lon",
    ):
        table[name] = pd.to_numeric(_first_present(source, name), errors="coerce")
    table["min_lat"] = pd.to_numeric(_first_present(source, "min_lat_geo", "min_lat"), errors="coerce")
    table["max_lat"] = pd.to_numeric(_first_present(source, "max_lat_geo", "max_lat"), errors="coerce")
    table["pole_inside"] = source["pole_inside"].fillna(False).astype(bool) if "pole_inside" in source else False
    table["lat_band"] = data.lat_band(table["bore_lat"].to_numpy(dtype=np.float64))
    table["month"] = table["start_time"].dt.strftime("%Y-%m")

    table["has_partner"] = False
    table["best_dt_s"] = np.nan
    table["trackable_30"] = False
    revisits = data.trackability_table(root)
    if revisits is not None:
        columns = ["product_id", "half"] + [
            name for name in ("has_partner", "best_dt_s", "trackable_30") if name in revisits.columns
        ]
        join = revisits[columns].copy()
        join["product_id"] = join["product_id"].astype(object)
        join["half"] = join["half"].astype(object)
        table = table.drop(columns=[name for name in ("has_partner", "best_dt_s", "trackable_30") if name in columns])
        table = table.merge(join, on=["product_id", "half"], how="left")
        for name in ("has_partner", "trackable_30"):
            if name in table.columns:
                table[name] = table[name].fillna(False).astype(bool)
        if "best_dt_s" in table.columns:
            table["best_dt_s"] = pd.to_numeric(table["best_dt_s"], errors="coerce")

    with _TABLE_LOCK:
        _TABLE_CACHE[key] = table
    LOGGER.info("catalog table: %d rows from %s", len(table), root)
    return table


def catalog_ipc(mirror: str | Path | None = None) -> tuple[str, bytes]:
    """The Arrow IPC stream and its ``ETag``, built once and kept."""
    root = mirror_root(mirror)
    key = str(root)
    etag = index_etag(root)
    with _TABLE_LOCK:
        cached = _IPC_CACHE.get(key)
    if cached is not None and cached[0] == etag:
        return cached
    payload = frame_to_ipc(catalog_frame(root), CATALOG_SCHEMA)
    with _TABLE_LOCK:
        _IPC_CACHE[key] = (etag, payload)
    LOGGER.info("catalog Arrow stream: %d bytes", len(payload))
    return etag, payload


def full_frame(mirror: str | Path | None = None) -> pd.DataFrame:
    """``frames_with_geo`` itself, cached for the per-frame detail view.

    Kept separately from :func:`catalog_frame` and populated only when a
    detail request arrives, so a server that only ever serves the map
    never pays for the wide table.
    """
    root = mirror_root(mirror)
    key = str(root)
    with _TABLE_LOCK:
        cached = _FULL_CACHE.get(key)
    if cached is not None:
        return cached
    frame = frames_with_geo(root)
    with _TABLE_LOCK:
        _FULL_CACHE[key] = frame
    return frame


def clear_caches() -> None:
    """Drop every cached table (used by the offline tests)."""
    with _TABLE_LOCK:
        _TABLE_CACHE.clear()
        _IPC_CACHE.clear()
        _FULL_CACHE.clear()


# ---------------------------------------------------------------------------
# filters and summary
# ---------------------------------------------------------------------------
def _keep_below(table: pd.DataFrame, name: str, threshold: float | None) -> np.ndarray:
    """``value <= threshold``, keeping the rows whose value is missing."""
    if threshold is None or name not in table.columns:
        return np.ones(len(table), dtype=bool)
    values = pd.to_numeric(table[name], errors="coerce").to_numpy(dtype=np.float64, na_value=np.nan)
    return ~(values > float(threshold))


def _keep_above(table: pd.DataFrame, name: str, threshold: float | None) -> np.ndarray:
    """``value >= threshold``, keeping the rows whose value is missing."""
    if threshold is None or name not in table.columns:
        return np.ones(len(table), dtype=bool)
    values = pd.to_numeric(table[name], errors="coerce").to_numpy(dtype=np.float64, na_value=np.nan)
    return ~(values < float(threshold))


def apply_filters(table: pd.DataFrame, **filters: Any) -> pd.DataFrame:
    """The catalog under the contract's query parameters.

    Thresholds keep rows whose value is unknown -- the geometry engine
    fails on about a quarter of the frames' emission angles, and dropping
    those the moment a slider moves would silently shrink the map.  Two
    exceptions the contract names: ``on_planet_min``, where a missing
    fraction excludes the row, and the latitude window, where a frame
    whose boresight misses the planet has no latitude at all and so
    cannot be inside a latitude band -- a quarter of the archive would
    otherwise answer every latitude query.
    """
    if table.empty:
        return table
    keep = np.ones(len(table), dtype=bool)

    orbit = pd.to_numeric(table["orbit"], errors="coerce").to_numpy(dtype=np.float64, na_value=np.nan)
    if filters.get("orbit_min") is not None:
        keep &= ~(orbit < float(filters["orbit_min"]))
    if filters.get("orbit_max") is not None:
        keep &= ~(orbit > float(filters["orbit_max"]))

    times = pd.to_datetime(table["start_time"])
    if filters.get("time_min") is not None:
        keep &= (times >= pd.Timestamp(filters["time_min"])).to_numpy()
    if filters.get("time_max") is not None:
        keep &= (times <= pd.Timestamp(filters["time_max"])).to_numpy()

    if filters.get("half"):
        keep &= table["half"].astype(str).str.upper().to_numpy() == str(filters["half"]).upper()

    keep &= _keep_below(table, "median_pixel_km", filters.get("pixel_max_km"))
    keep &= _keep_below(table, "bore_emission", filters.get("emission_max"))

    if filters.get("on_planet_min") is not None:
        values = pd.to_numeric(table["on_planet_frac"], errors="coerce").to_numpy(
            dtype=np.float64, na_value=np.nan
        )
        with np.errstate(invalid="ignore"):
            keep &= values >= float(filters["on_planet_min"])

    if filters.get("dayside_only"):
        keep &= _keep_above(table, "dayside_frac", float(np.nextafter(0.0, 1.0)))

    if filters.get("lat_min") is not None or filters.get("lat_max") is not None:
        latitudes = pd.to_numeric(table["bore_lat"], errors="coerce").to_numpy(
            dtype=np.float64, na_value=np.nan
        )
        with np.errstate(invalid="ignore"):
            if filters.get("lat_min") is not None:
                keep &= latitudes >= float(filters["lat_min"])
            if filters.get("lat_max") is not None:
                keep &= latitudes <= float(filters["lat_max"])

    if filters.get("revisit_only"):
        column = "trackable_30" if "trackable_30" in table.columns else "has_partner"
        if column in table.columns:
            keep &= table[column].fillna(False).to_numpy(dtype=bool)

    return table.loc[keep]


def summarise(table: pd.DataFrame) -> dict[str, Any]:
    """Counts overall and by latitude band, orbit and month."""
    bands = pd.Series(table["lat_band"] if "lat_band" in table.columns else [], dtype=object)
    counts = bands.value_counts()
    by_band = [
        {"band": name, "n": int(counts.get(name, 0))}
        for name in LAT_BAND_NAMES
        if int(counts.get(name, 0)) > 0
    ]
    orbits = pd.to_numeric(table["orbit"], errors="coerce").dropna().astype(int)
    by_orbit = [
        {"orbit": int(orbit), "n": int(n)} for orbit, n in orbits.value_counts().sort_index().items()
    ]
    months = table["month"].dropna().astype(str) if "month" in table.columns else pd.Series([], dtype=str)
    by_month = [
        {"month": str(month), "n": int(n)} for month, n in months.value_counts().sort_index().items()
    ]
    return {"n": int(len(table)), "by_lat_band": by_band, "by_orbit": by_orbit, "by_month": by_month}


def json_safe(value: Any) -> Any:
    """A pandas cell as something ``json`` can encode; ``NaN`` becomes null."""
    if value is None or value is pd.NaT or value is pd.NA:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return None if not np.isfinite(float(value)) else float(value)
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        stamp = pd.Timestamp(value)
        return None if pd.isna(stamp) else stamp.isoformat()
    if isinstance(value, np.ndarray):
        return [json_safe(item) for item in value.tolist()]
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    return value if isinstance(value, (str, int, float, bool)) else str(value)


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@router.get("/frames.arrow")
def frames_arrow(request: Request) -> Response:
    """The whole catalog as an Arrow IPC stream, conditional on the ``ETag``."""
    mirror = request.app.state.mirror
    etag, payload = catalog_ipc(mirror)
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return Response(
        content=payload,
        media_type=ARROW_MEDIA_TYPE,
        headers={"ETag": etag, "Cache-Control": "no-cache"},
    )


@router.get("/summary")
def summary(
    request: Request,
    orbit_min: int | None = None,
    orbit_max: int | None = None,
    time_min: str | None = None,
    time_max: str | None = None,
    half: str | None = Query(default=None, pattern="^[LMlm]$"),
    pixel_max_km: float | None = None,
    emission_max: float | None = None,
    on_planet_min: float | None = None,
    dayside_only: bool = False,
    lat_min: float | None = None,
    lat_max: float | None = None,
    revisit_only: bool = False,
) -> dict[str, Any]:
    """Counts of the filtered catalog, binned three ways."""
    table = catalog_frame(request.app.state.mirror)
    try:
        selected = apply_filters(
            table,
            orbit_min=orbit_min,
            orbit_max=orbit_max,
            time_min=time_min,
            time_max=time_max,
            half=half,
            pixel_max_km=pixel_max_km,
            emission_max=emission_max,
            on_planet_min=on_planet_min,
            dayside_only=dayside_only,
            lat_min=lat_min,
            lat_max=lat_max,
            revisit_only=revisit_only,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"bad filter: {exc}") from exc
    return summarise(selected)


@router.get("/frame/{product_id}")
def frame_detail(request: Request, product_id: str) -> dict[str, Any]:
    """Every column the index and the geometry table carry for one product."""
    frame = full_frame(request.app.state.mirror)
    rows = frame.loc[frame["product_id"].astype(str) == str(product_id)]
    if rows.empty:
        raise HTTPException(status_code=404, detail=f"unknown product_id: {product_id}")
    halves = sorted({str(value) for value in rows["half"].dropna().tolist()})
    record = {name: json_safe(rows.iloc[0][name]) for name in rows.columns}
    record["halves"] = halves
    return record
