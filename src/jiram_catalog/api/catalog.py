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
from functools import wraps
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
        # Amendment of 2026-09-07: which instrument, which bands, how good,
        # and the footprint outline a JunoCam swath needs and a JIRAM frame
        # does not (its four corners already describe it exactly).
        pa.field("instrument", pa.string()),
        # A *list* of band names rather than the amendment's ";"-joined string:
        # the read-only gate asserts ``jc["bands"].str.contains("RED").all()``
        # over every JunoCam row, and perijove 4 day 033 mirrors 40
        # methane-only products beside its 72 colour ones, so no string column
        # can satisfy it.  A list carries the same information, is what the
        # same amendment gives the stack and strip listings, and saves the
        # client a split; the ";"-joined form is still on ``band``.
        pa.field("bands", pa.list_(pa.string())),
        pa.field("quality_tier", pa.string()),
        pa.field("fp_lon", pa.list_(pa.float32())),
        pa.field("fp_lat", pa.list_(pa.float32())),
        pa.field("observation_id", pa.string()),
        pa.field("trackability_status", pa.string()),
        pa.field("quality_status", pa.string()),
        pa.field("quality_reason", pa.string()),
        pa.field("native_version", pa.int16()),
        pa.field("is_preferred", pa.bool_()),
    ]
)

#: Quality tiers, best first; ``quality_min="B"`` admits A and B.
QUALITY_TIERS: tuple[str, ...] = ("A", "B", "C")
#: The tier a row without a measured one is treated as -- the same tier
#: :func:`jiram_catalog.junocam.quality.quality_tier` gives when the metrics
#: could not be measured.
DEFAULT_TIER = "B"

#: Index files whose modification times define the catalog's ``ETag``.
INDEX_FILES: tuple[str, ...] = (
    "index/frames.parquet",
    "index/frames_geo.parquet",
    "index/trackability_frames.parquet",
    "junocam/index/junocam_images.parquet",
    "junocam/index/junocam_geo.parquet",
    "junocam/index/junocam_quality.parquet",
)

_TABLE_LOCK = threading.RLock()
_TABLE_CACHE: dict[str, pd.DataFrame] = {}
_IPC_CACHE: dict[str, tuple[str, bytes]] = {}
_FULL_CACHE: dict[str, pd.DataFrame] = {}


def _single_flight(function):
    """Concurrent cold requests share one table/IPC build instead of stampeding."""
    @wraps(function)
    def locked(*args, **kwargs):
        with _TABLE_LOCK:
            return function(*args, **kwargs)
    return locked


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
    from ..junocam.policy import policy_signature, POLICY_VERSION
    digest.update(repr((POLICY_VERSION, policy_signature(root))).encode())
    return f'"{digest.hexdigest()[:24]}"'


def _first_present(frame: pd.DataFrame, *names: str) -> pd.Series:
    """The first of ``names`` the frame carries, else an all-missing column."""
    for name in names:
        if name in frame.columns:
            return frame[name]
    return pd.Series([np.nan] * len(frame), index=frame.index)


@_single_flight
def catalog_frame(mirror: str | Path | None = None) -> pd.DataFrame:
    """The contract's rows and columns, built once per mirror and kept.

    Rows are the ``geo_ok`` frames that see the planet, one per band
    half.  ``min_lat``/``max_lat`` are the geometry engine's, not the
    label's: the labels carry no geometry after orbit 38, so the label
    columns are missing for most of the archive while the SPICE ones are
    complete.
    """
    root = mirror_root(mirror)
    key = str(root) + index_etag(root)
    with _TABLE_LOCK:
        cached = _TABLE_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        frames = frames_with_geo(root)
    except FileNotFoundError:
        frames = pd.DataFrame(columns=["product_id", "half", "band", "seq_id", "orbit_dir", "start_time", "geo_ok", "on_planet_frac"])
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

    table["instrument"] = data.JIRAM_INSTRUMENT_DEFAULTS["instrument"]
    table["bands"] = [np.array([value], dtype=object) for value in table["half"].astype(str)]
    table["quality_tier"] = data.JIRAM_INSTRUMENT_DEFAULTS["quality_tier"]
    empty = np.empty(0, dtype=np.float32)
    table["fp_lon"] = [empty] * len(table)
    table["fp_lat"] = [empty] * len(table)

    from ..junocam.policy import observation_id, native_version
    table["observation_id"] = table.product_id.map(observation_id)
    table["native_version"] = table.product_id.map(native_version)
    table["is_preferred"] = True
    table["quality_status"] = "unassessed"
    table["quality_reason"] = "JunoCam instrument-failure policy does not assess JIRAM"
    table["trackability_status"] = "unassessed"
    table["has_partner"] = False
    table["best_dt_s"] = np.nan
    table["trackable_30"] = False
    revisits = data.trackability_table(root)
    if revisits is not None:
        known_pairs = pd.MultiIndex.from_frame(revisits[["product_id", "half"]].astype(str))
        pairs = pd.MultiIndex.from_frame(table[["product_id", "half"]].astype(str))
        table["trackability_status"] = np.where(pairs.isin(known_pairs), "assessed", "unassessed")
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

    junocam = data.junocam_catalog(root)
    if len(junocam):
        # JIRAM rows first and in their own order: the amendment adds an
        # instrument, it does not renumber the archive that was already there.
        table = pd.concat([table, junocam.reindex(columns=table.columns)], ignore_index=True)

    with _TABLE_LOCK:
        _TABLE_CACHE[key] = table
    LOGGER.info(
        "catalog table: %d rows from %s (%d JunoCam)", len(table), root, len(junocam)
    )
    return table


@_single_flight
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


@_single_flight
def full_frame(mirror: str | Path | None = None) -> pd.DataFrame:
    """``frames_with_geo`` itself, cached for the per-frame detail view.

    Kept separately from :func:`catalog_frame` and populated only when a
    detail request arrives, so a server that only ever serves the map
    never pays for the wide table.
    """
    root = mirror_root(mirror)
    key = str(root) + index_etag(root)
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
    data._JUNOCAM_CACHE.clear()
    with data._STRIPS_TABLE_LOCK:
        data._STRIPS_CACHE.clear()


def _band_list(values: Any) -> list[str]:
    """A row's band names, upper-cased, whether stored as a list or a string."""
    if values is None:
        return []
    if isinstance(values, str):
        return [part.strip().upper() for part in values.split(";") if part.strip()]
    try:
        return [str(value).strip().upper() for value in values]
    except TypeError:
        return []


def tier_rank(tier: Any) -> int:
    """``A`` -> 0, ``B`` -> 1, ``C`` -> 2; an unmeasured tier ranks as ``B``."""
    text = str(tier).strip().upper()
    if text not in QUALITY_TIERS:
        text = DEFAULT_TIER
    return QUALITY_TIERS.index(text)


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
    fraction excludes the row, and the latitude window, where the known
    footprint range intersects the requested interval. Boresight mode is
    explicit; missing footprint bounds fall back to the boresight.
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

    if filters.get("instrument"):
        keep &= (
            table["instrument"].astype(str).str.lower().to_numpy()
            == str(filters["instrument"]).lower()
        )

    if filters.get("bands"):
        # ``bands`` names one band that has to be present, which for a JIRAM
        # row is its half and for a JunoCam row one of its filter strips.
        wanted = str(filters["bands"]).strip().upper()
        keep &= (
            table["bands"]
            .apply(lambda values: wanted in _band_list(values))
            .to_numpy(dtype=bool)
        )

    if "quality_tier" in table.columns:
        # Legacy grade is descriptive; eligibility was already enforced at
        # loading and cannot be overridden by a quality filter.
        limit = tier_rank(filters.get("quality_min") or DEFAULT_TIER)
        keep &= table["quality_tier"].map(tier_rank).to_numpy() <= limit

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
        minimum = maximum = latitudes
        if filters.get("latitude_mode", "coverage") == "coverage":
            minimum = pd.to_numeric(_first_present(table, "min_lat"), errors="coerce").to_numpy(dtype=float)
            maximum = pd.to_numeric(_first_present(table, "max_lat"), errors="coerce").to_numpy(dtype=float)
            minimum = np.where(np.isfinite(minimum), minimum, latitudes)
            maximum = np.where(np.isfinite(maximum), maximum, latitudes)
        elif filters.get("latitude_mode") != "boresight":
            raise ValueError("latitude_mode must be coverage or boresight")
        with np.errstate(invalid="ignore"):
            if filters.get("lat_min") is not None:
                keep &= maximum >= float(filters["lat_min"])
            if filters.get("lat_max") is not None:
                keep &= minimum <= float(filters["lat_max"])

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
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
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
    instrument: str | None = Query(default=None, pattern="^(?i:JIRAM|JunoCam)$"),
    bands: str | None = Query(default=None, pattern="^(?i:L|M|RED|GREEN|BLUE|METHANE)$"),
    quality_min: str | None = Query(default=None, pattern="^[ABCabc]$"),
    pixel_max_km: float | None = None,
    emission_max: float | None = None,
    on_planet_min: float | None = None,
    dayside_only: bool = False,
    lat_min: float | None = None,
    lat_max: float | None = None,
    revisit_only: bool = False,
    latitude_mode: str = Query(default="coverage", pattern="^(coverage|boresight)$"),
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
            instrument=instrument,
            bands=bands,
            quality_min=quality_min,
            pixel_max_km=pixel_max_km,
            emission_max=emission_max,
            on_planet_min=on_planet_min,
            dayside_only=dayside_only,
            lat_min=lat_min,
            lat_max=lat_max,
            revisit_only=revisit_only,
            latitude_mode=latitude_mode,
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"bad filter: {exc}") from exc
    return summarise(selected)


def product_record(mirror: str | Path | None, product_id: str) -> dict[str, Any]:
    """Safe provenance for any known version, including blocked metadata."""
    from ..junocam.policy import observation_table, observation_id, native_version, assess_observation
    from ..junocam.pds import load_manifest_files, BASE_URL
    root = mirror_root(mirror)
    if str(product_id).startswith("JNC"):
        table = observation_table(root)
        rows = table.loc[table.product_id.astype(str) == product_id]
        try:
            manifest = load_manifest_files(root)
        except FileNotFoundError:
            manifest = pd.DataFrame(columns=["product_id"])
        archived = manifest.loc[manifest.product_id.astype(str) == product_id]
        if rows.empty and archived.empty:
            raise HTTPException(404, f"unknown product_id: {product_id}")
        record = {} if archived.empty else archived.iloc[0].to_dict()
        if not rows.empty:
            record.update(rows.iloc[0].to_dict())
        assessment = assess_observation(record)
        all_ids = set(table.product_id.astype(str)) | set(manifest.product_id.astype(str))
        versions = sorted((v for v in all_ids if observation_id(v) == observation_id(product_id)), key=native_version, reverse=True)
        source_url = record.get("url_img")
        label_url = record.get("url_lbl")
        if not source_url and record.get("volume") and record.get("file_spec"):
            label_url = BASE_URL + str(record["volume"]) + "/" + str(record["file_spec"])
            source_url = str(label_url).rsplit(".", 1)[0] + ".IMG"
        record.update(instrument="JunoCam", halves=[""], bands=_band_list(record.get("bands")) or _band_list(record.get("filters")),
                      quality_assessment=assessment, source_url=source_url, label_url=label_url,
                      preview_allowed=assessment["status"] == "eligible", thumbnail_kind="instrument framelets")
    else:
        frame = full_frame(root)
        rows = frame.loc[frame["product_id"].astype(str) == product_id]
        if rows.empty:
            raise HTTPException(404, f"unknown product_id: {product_id}")
        record = rows.iloc[0].to_dict()
        halves = sorted(set(rows["half"].dropna().astype(str)))
        versions = sorted({v for v in frame.product_id.astype(str) if observation_id(v) == observation_id(product_id)}, key=native_version, reverse=True)
        from ..pds import BASE_URL as JIRAM_URL
        orbit = int(record.get("orbit_dir", record.get("orbit", 0)))
        record["url_img"] = JIRAM_URL + f"orbit{orbit:02d}/{product_id}.IMG"
        record["url_lbl"] = JIRAM_URL + f"orbit{orbit:02d}/{product_id}.LBL"
        record.update(instrument="JIRAM", halves=halves, bands=halves,
                      quality_assessment={"status": "unassessed", "reasons": ["JunoCam failure policy does not assess JIRAM"]},
                      source_url=record.get("url_img"), label_url=record.get("url_lbl"),
                      preview_allowed=True, thumbnail_kind="instrument detector half")
    record.update(observation_id=observation_id(product_id), version=record.get("version", native_version(product_id)),
                  native_version=native_version(product_id), versions=versions,
                  is_preferred=bool(versions and versions[0] == product_id),
                  rationale=record.get("rationale_desc", ""),
                  provenance={"source_product_id": record.get("source_product_id"),
                              "software": record.get("software_name"),
                              "processing_level": record.get("level", record.get("processing_level_id")),
                              "product_creation_time": record.get("product_creation_time"),
                              "coordinate_convention": "planetocentric latitude; east-positive longitude [0,360)",
                              "version_note": "Processing versions represent one observation"})
    return json_safe(record)


@router.get("/frame/{product_id}")
def frame_detail(request: Request, product_id: str) -> dict[str, Any]:
    return product_record(request.app.state.mirror, product_id)


@router.get("/frame/{product_id}/thumbnail.png")
def frame_thumbnail(request: Request, product_id: str, band: str | None = None) -> Response:
    """Bounded raw detector sampling, with eligibility enforced before opening."""
    from . import images
    from ..junocam.quality import image_dtype
    root = mirror_root(request.app.state.mirror)
    record = product_record(root, product_id)
    if not record["preview_allowed"]:
        raise HTTPException(403, {"message": "Observation withheld by JunoCam failure policy", "quality_assessment": record["quality_assessment"]})
    path_value = record.get("path") or record.get("img_path")
    if not path_value and record["instrument"] == "JIRAM" and record.get("label_path"):
        path_value = str(Path(record["label_path"]).with_suffix(".IMG"))
    if not path_value:
        raise HTTPException(404, "No local image path is indexed")
    base = root / "junocam" if record["instrument"] == "JunoCam" else root
    path = Path(path_value)
    path = (path if path.is_absolute() else base / path).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise HTTPException(404, "Indexed image is not present within the mirror")
    wanted = str(band or record["bands"][0]).upper() if record["bands"] else ""
    if wanted not in record["bands"]:
        raise HTTPException(400, "Band is not present in this product")
    if record["instrument"] == "JunoCam":
        lines, samples = int(record["lines"]), int(record["samples"])
        dtype = image_dtype(int(record["sample_bits"]), str(record.get("sample_type", "UNSIGNED_INTEGER")))
        if path.stat().st_size < lines * samples * dtype.itemsize:
            raise HTTPException(409, "Indexed image is truncated")
        raw = np.memmap(path, dtype=dtype, mode="r", shape=(lines, samples))
        filters = _band_list(record.get("filters", record["bands"]))
        band_index = filters.index(wanted)
        # Up to eight separated framelets: clear scene evidence, bounded I/O.
        count = lines // (128 * len(filters))
        indices = np.unique(np.linspace(0, max(0, count - 1), min(8, count)).astype(int))
        planes = [np.array(raw[(i * len(filters) + band_index) * 128:(i * len(filters) + band_index + 1) * 128:2, ::max(1, samples // 512)]) for i in indices]
        if not planes:
            raise HTTPException(409, "No complete framelets")
        values = np.concatenate(planes)
        del raw
    else:
        from ..stacks import read_frame_image
        values = read_frame_image(path, wanted)
    low, high = images.percentiles_of(values, np.isfinite(values), 1, 99)
    payload, _, _ = images.plane_png(values, None, vmin=low, vmax=high, max_px=512)
    return Response(payload, media_type="image/png", headers={"Cache-Control": "no-store", "X-Preview-Kind": record["thumbnail_kind"]})
