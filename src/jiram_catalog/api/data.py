"""Loaders, caches and table transforms behind the HTTP service.

Every table the API answers is read once per process and shared read-only,
so the loaders here are memoised on the mirror path.  Frame stacks are
opened lazily and never loaded: a frame stack is 2 GB and the viewer
reads one time step at a time.  The only thing written under the mirror
is ``<mirror>/gui_cache/``, which holds per-strip statistics as NetCDF,
the saved selections and the job records.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

from ..config import mirror_root
from ..geo import frames_with_geo
from ..stacks import read_stack
from ..stats2d import strip_statistics
from ..strips import load_index, read_strip

#: Latitude band edges and names, degrees; the same seven bands the
#: trackability table uses (half-open ``[lo, hi)`` except the last).
LAT_BAND_EDGES: tuple[float, ...] = (-90.0, -60.0, -30.0, -10.0, 10.0, 30.0, 60.0, 90.0)
LAT_BAND_NAMES: tuple[str, ...] = (
    "S polar",
    "S mid",
    "S low",
    "equator",
    "N low",
    "N mid",
    "N polar",
)

LOGGER = logging.getLogger(__name__)

#: Columns of ``frames_with_geo`` the GUI keeps; everything else is dropped
#: before the table is cached, which is what makes 113,000 rows cheap.
CATALOG_COLUMNS: tuple[str, ...] = (
    "product_id",
    "orbit_dir",
    "half",
    "band",
    "seq_id",
    "start_time",
    "bore_lat",
    "bore_lon_east",
    "bore_emission",
    "on_planet_frac",
    "median_pixel_km",
    "dayside_frac",
    "pole_inside",
    "min_lat",
    "max_lat",
    "geo_ok",
)

#: Columns joined from the trackability table when it exists.
TRACKABILITY_COLUMNS: tuple[str, ...] = ("has_partner", "best_dt_s", "trackable_30")

_TEXT_COLUMNS = ("product_id", "half", "band", "seq_id")

_CATALOG_CACHE: dict[str, pd.DataFrame] = {}
_JUNOCAM_CACHE: dict[str, pd.DataFrame] = {}
_STRIPS_CACHE: dict[str, pd.DataFrame] = {}
_TRACKABILITY_CACHE: dict[str, pd.DataFrame | None] = {}
_STACK_CACHE: dict[str, xr.Dataset] = {}
_STRIP_CACHE: dict[str, xr.Dataset] = {}
_STATS_CACHE: dict[str, xr.Dataset] = {}
_STRETCH_CACHE: dict[tuple[str, float, float], tuple[float, float]] = {}


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------
def gui_cache_dir(mirror: str | Path | None = None) -> Path:
    """``<mirror>/gui_cache``, created; the only place the GUI writes."""
    path = mirror_root(mirror) / "gui_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def export_dir(mirror: str | Path | None = None) -> Path:
    """Default destination of movies, PNGs and goflow datasets."""
    path = gui_cache_dir(mirror) / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def stack_paths(mirror: str | Path | None = None) -> list[Path]:
    """Region stacks: ``<mirror>/regions/<region>/*.nc``, region level only.

    The goflow export directories underneath a region hold their own
    ``realization.nc`` files, which are not stacks; one level of glob
    keeps them out without a name test.
    """
    root = mirror_root(mirror) / "regions"
    if not root.is_dir():
        return []
    return sorted(path for path in root.glob("*/*.nc") if path.is_file())


# ---------------------------------------------------------------------------
# latitude bands (the seven of the trackability table)
# ---------------------------------------------------------------------------
def lat_band(lat: Any) -> np.ndarray:
    """Band name per latitude; ``None`` where the latitude is NaN."""
    values = np.asarray(lat, dtype=np.float64)
    edges = np.asarray(LAT_BAND_EDGES[1:-1], dtype=np.float64)
    names = np.asarray(LAT_BAND_NAMES, dtype=object)
    labels = names[np.clip(np.digitize(values, edges, right=False), 0, len(names) - 1)]
    return np.where(np.isnan(values), None, labels)


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------
def catalog_table(mirror: str | Path | None = None) -> pd.DataFrame:
    """Frames that see the planet, reduced to the columns the GUI draws.

    Rows are ``geo_ok`` and ``on_planet_frac > 0`` -- about 47,600 of the
    113,000 (frame, band half) rows.  Derived columns: ``orbit`` (int),
    ``lat_band``, ``month``, ``year``, and, when the trackability table
    exists, its revisit columns.
    """
    root = mirror_root(mirror)
    key = str(root)
    cached = _CATALOG_CACHE.get(key)
    if cached is not None:
        return cached

    frames = frames_with_geo(root)
    keep = [name for name in CATALOG_COLUMNS if name in frames.columns]
    missing = set(CATALOG_COLUMNS) - set(keep)
    if missing:
        LOGGER.warning("geometry table is missing %s", ", ".join(sorted(missing)))
    mask = (
        frames["geo_ok"].fillna(False).to_numpy(dtype=bool)
        & (frames["on_planet_frac"].to_numpy(dtype=np.float64) > 0.0)
    )
    table = frames.loc[mask, keep].reset_index(drop=True).copy()
    for name in _TEXT_COLUMNS:
        if name in table.columns:
            table[name] = table[name].astype(str).astype(object)
    table["orbit"] = table["orbit_dir"].astype("int64")
    table["start_time"] = pd.to_datetime(table["start_time"])
    table["lat_band"] = lat_band(table["bore_lat"].to_numpy(dtype=np.float64))
    table["month"] = table["start_time"].dt.to_period("M").dt.to_timestamp()
    table["year"] = table["start_time"].dt.year.astype("int64")

    revisits = trackability_table(root)
    if revisits is not None:
        columns = ["product_id", "half"] + [
            name for name in TRACKABILITY_COLUMNS if name in revisits.columns
        ]
        join = revisits[columns].copy()
        join["product_id"] = join["product_id"].astype(str).astype(object)
        join["half"] = join["half"].astype(str).astype(object)
        table = table.merge(join, on=["product_id", "half"], how="left")
        for name in ("has_partner", "trackable_30"):
            if name in table.columns:
                table[name] = table[name].fillna(False).astype(bool)

    _CATALOG_CACHE[key] = table
    return table


#: Columns a JunoCam catalog row carries that a JIRAM one does not, and the
#: value a JIRAM row takes for each.  ``quality_tier`` is ``A`` for JIRAM
#: because the tier is a JunoCam radiometric judgement (streaks, saturation,
#: the CCD's damage epochs) and the JIRAM rows must not be hidden by a filter
#: that has nothing to say about them.
JIRAM_INSTRUMENT_DEFAULTS: dict[str, Any] = {"instrument": "JIRAM", "quality_tier": "A"}


def junocam_catalog(mirror: str | Path | None = None) -> pd.DataFrame:
    """JunoCam images as catalog rows, in the frame catalog's own columns.

    One row per placed RDR image -- the unit of the JunoCam archive is the
    swath, not the framelet -- with ``half`` empty because a JunoCam product
    has no detector half, the four corner columns NaN because a swath's
    footprint is a ribbon rather than a quadrilateral (the outline is in
    ``fp_lon``/``fp_lat`` instead), and the revisit columns empty because
    trackability is a JIRAM product.

    An empty frame comes back when the JunoCam tables are absent, so a mirror
    that has never run the JunoCam layer answers the amended contract with
    JIRAM rows alone rather than with an error.
    """
    root = mirror_root(mirror)
    key = str(root)
    cached = _JUNOCAM_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        from ..junocam.geo import load_geo
        from ..junocam.quality import load_quality

        # The geometry table already carries the band list and the start time,
        # so the image index is not needed here; only the tier is joined.
        geo = load_geo(root)
        quality = load_quality(root)[["product_id", "quality_tier"]]
    except (FileNotFoundError, OSError, KeyError, ImportError) as exc:
        LOGGER.info("no JunoCam catalog rows: %s", exc)
        table = _empty_junocam()
        _JUNOCAM_CACHE[key] = table
        return table

    geo = geo.loc[geo["geo_ok"].fillna(False).astype(bool)].reset_index(drop=True)
    merged = geo.merge(quality, on="product_id", how="left")
    table = pd.DataFrame(index=range(len(merged)))
    table["product_id"] = merged["product_id"].astype(str).astype(object)
    table["half"] = ""
    table["band"] = merged["bands"].astype(str).astype(object)
    table["orbit"] = pd.to_numeric(merged["orbit"], errors="coerce").fillna(0).astype("int64")
    table["seq_id"] = merged["product_id"].astype(str).astype(object)
    table["start_time"] = pd.to_datetime(merged["start_time"])
    for name in (
        "bore_lat",
        "bore_lon_east",
        "bore_emission",
        "on_planet_frac",
        "median_pixel_km",
        "dayside_frac",
        "min_lat",
        "max_lat",
        "lon_span_deg",
    ):
        table[name] = pd.to_numeric(merged[name], errors="coerce")
    table["pole_inside"] = merged["pole_inside"].fillna(False).astype(bool)
    for index in range(1, 5):
        table[f"c{index}_lat"] = np.nan
        table[f"c{index}_lon"] = np.nan
    table["has_partner"] = False
    table["trackable_30"] = False
    table["best_dt_s"] = np.nan
    table["instrument"] = "JunoCam"
    # ``band`` is the ";"-joined filter list, ``bands`` the same names as a
    # list; see the note on ``CATALOG_SCHEMA`` for why the catalog's column is
    # a list rather than the amendment's string.
    table["bands"] = [
        np.array(
            [part.strip().upper() for part in str(value).split(";") if part.strip()],
            dtype=object,
        )
        for value in merged["bands"]
    ]
    table["quality_tier"] = (
        merged["quality_tier"].where(merged["quality_tier"].notna(), "B").astype(str).astype(object)
    )
    table["fp_lon"] = [np.asarray(value, dtype=np.float32) for value in merged["fp_lon"]]
    table["fp_lat"] = [np.asarray(value, dtype=np.float32) for value in merged["fp_lat"]]
    table["lat_band"] = lat_band(table["bore_lat"].to_numpy(dtype=np.float64))
    table["month"] = table["start_time"].dt.strftime("%Y-%m")
    _JUNOCAM_CACHE[key] = table
    LOGGER.info("JunoCam catalog rows: %d from %s", len(table), root)
    return table


def _empty_junocam() -> pd.DataFrame:
    columns = [
        "product_id", "half", "band", "orbit", "seq_id", "start_time", "bore_lat",
        "bore_lon_east", "bore_emission", "on_planet_frac", "median_pixel_km",
        "dayside_frac", "min_lat", "max_lat", "lon_span_deg", "pole_inside",
        "c1_lat", "c1_lon", "c2_lat", "c2_lon", "c3_lat", "c3_lon", "c4_lat",
        "c4_lon", "has_partner", "trackable_30", "best_dt_s", "instrument",
        "bands", "quality_tier", "fp_lon", "fp_lat", "lat_band", "month",
    ]
    return pd.DataFrame({name: pd.Series(dtype="object") for name in columns})


def junocam_count(mirror: str | Path | None = None) -> int:
    """How many JunoCam images the catalog carries (0 without the tables)."""
    return int(len(junocam_catalog(mirror)))


def strips_table(mirror: str | Path | None = None) -> pd.DataFrame:
    """The strip library index with real timestamps and derived columns."""
    root = mirror_root(mirror)
    key = str(root)
    cached = _STRIPS_CACHE.get(key)
    if cached is not None:
        return cached
    table = _coerce_strips(load_index(root))
    _STRIPS_CACHE[key] = table
    return table


def _coerce_strips(table: pd.DataFrame) -> pd.DataFrame:
    """Timestamps as timestamps, ids as text, plus ``lat_band`` and ``year``."""
    table = table.copy()
    for name in ("time_start", "time_end", "time_mid"):
        if name in table.columns:
            table[name] = pd.to_datetime(table[name], errors="coerce")
    for name in ("strip_id", "band", "seq_id", "path"):
        if name in table.columns:
            table[name] = table[name].astype(str).astype(object)
    for name in ("orbit", "n_frames", "rows", "cols"):
        if name in table.columns:
            table[name] = table[name].astype("int64")
    if "center_lat" in table.columns:
        table["lat_band"] = lat_band(table["center_lat"].to_numpy(dtype=np.float64))
    if "time_mid" in table.columns:
        table["year"] = table["time_mid"].dt.year.astype("int64")
    return table


def trackability_table(mirror: str | Path | None = None) -> pd.DataFrame | None:
    """``index/trackability_frames.parquet`` if it exists, else ``None``.

    The revisit filter is hidden when this returns ``None``; the table is
    another agent's product and the GUI must work without it.
    """
    root = mirror_root(mirror)
    key = str(root)
    if key in _TRACKABILITY_CACHE:
        return _TRACKABILITY_CACHE[key]
    path = root / "index" / "trackability_frames.parquet"
    table: pd.DataFrame | None = None
    if path.exists():
        try:
            table = pd.read_parquet(path)
        except Exception as exc:  # a half-written table must not break the GUI
            LOGGER.warning("cannot read %s: %s", path, exc)
            table = None
    _TRACKABILITY_CACHE[key] = table
    return table


def has_trackability(mirror: str | Path | None = None) -> bool:
    """Whether the revisit filter has a table to stand on."""
    table = trackability_table(mirror)
    return table is not None and "trackable_30" in table.columns


# ---------------------------------------------------------------------------
# datasets
# ---------------------------------------------------------------------------
def open_stack(path: str | Path) -> xr.Dataset:
    """Open a region stack lazily and keep the handle; never ``.load()``."""
    key = str(Path(path))
    cached = _STACK_CACHE.get(key)
    if cached is not None:
        return cached
    dataset = read_stack(key)
    _STACK_CACHE[key] = dataset
    return dataset


def open_strip(mirror: str | Path | None, strip: str) -> xr.Dataset:
    """Open a strip by ``strip_id`` (a few MB; still opened lazily)."""
    key = f"{mirror_root(mirror)}::{strip}"
    cached = _STRIP_CACHE.get(key)
    if cached is not None:
        return cached
    dataset = read_strip(mirror, strip)
    _STRIP_CACHE[key] = dataset
    return dataset


def strip_stats(
    mirror: str | Path | None, strip: str, band: str | None = None
) -> xr.Dataset:
    """Statistics of one strip, computed once and cached on disk.

    The cache is ``<mirror>/gui_cache/stats_<strip_id>.nc`` -- the same
    Dataset :func:`stats2d.strip_statistics` returns, so the file is also
    what a later population run would read.  A multi-band strip has one
    spectrum per band and therefore one cache file per band, named
    ``stats_<strip_id>__<band>.nc``; the band-less name stays what it was, so
    every JIRAM cache already on disk is still found.
    """
    root = mirror_root(mirror)
    suffix = "" if band is None else f"__{str(band).upper()}"
    key = f"{root}::{strip}{suffix}"
    cached = _STATS_CACHE.get(key)
    if cached is not None:
        return cached
    path = gui_cache_dir(root) / f"stats_{strip}{suffix}.nc"
    if path.exists():
        try:
            with xr.open_dataset(path, engine="netcdf4") as stored:
                statistics = stored.load()
            _STATS_CACHE[key] = statistics
            return statistics
        except Exception as exc:
            LOGGER.warning("unreadable statistics cache %s: %s", path, exc)
    statistics = strip_statistics(open_strip(root, strip), band=band)
    temporary = path.with_suffix(".nc.tmp")
    statistics.to_netcdf(temporary, engine="netcdf4")
    os.replace(temporary, path)
    _STATS_CACHE[key] = statistics
    return statistics


def stack_stretch(
    dataset: xr.Dataset,
    percentiles: tuple[float, float] = (1.0, 99.0),
    *,
    key: str | None = None,
    max_steps: int = 4,
    max_samples: int = 2_000_000,
) -> tuple[float, float]:
    """Display limits from a strided subsample of a few time steps.

    ``movie.stretch_limits`` reads every pixel of every step, which costs
    a gigabyte of I/O on a frame stack; a display stretch cannot tell the
    difference between that and a few hundred thousand samples, and the
    viewer has to answer while the user watches.
    """
    low_p, high_p = (float(value) for value in percentiles)
    identity = (key or str(dataset.attrs.get("region", id(dataset))), low_p, high_p)
    cached = _STRETCH_CACHE.get(identity)
    if cached is not None:
        return cached
    steps = int(dataset.sizes.get("time", 1))
    indices = np.unique(np.linspace(0, steps - 1, min(max_steps, steps)).astype(int))
    per_step = max(1, max_samples // max(len(indices), 1))
    samples: list[np.ndarray] = []
    for index in indices:
        image = dataset["image"]
        plane = image.isel(time=index) if "time" in image.dims else image
        stride = max(1, int(np.ceil(np.sqrt(plane.size / per_step))))
        values = np.asarray(plane[::stride, ::stride].values, dtype=np.float64).ravel()
        samples.append(values[np.isfinite(values)])
    pool = np.concatenate(samples) if samples else np.empty(0)
    if pool.size == 0:
        limits = (0.0, 1.0)
    else:
        low, high = (float(value) for value in np.percentile(pool, (low_p, high_p)))
        limits = (low, high if high > low else low + 1.0)
    _STRETCH_CACHE[identity] = limits
    return limits


# ---------------------------------------------------------------------------
# contours (graticule, local time)
# ---------------------------------------------------------------------------
def contour_paths(
    field: Any,
    x: Any,
    y: Any,
    levels: Any,
    *,
    period: float | None = None,
    min_points: int = 2,
) -> list[np.ndarray]:
    """Contour lines of ``field`` on the ``(y, x)`` grid, as ``(N, 2)`` arrays.

    With ``period`` set the field is treated as an angle: each level is
    contoured on the wrapped difference ``field - level``, masked where
    the difference exceeds a quarter period.  That draws the 0 deg
    meridian as one line instead of two, and never draws the seam, which
    contouring the raw longitude would put across the middle of the map.
    """
    import matplotlib.pyplot as plt

    values = np.asarray(field, dtype=np.float64)
    xs = np.asarray(x, dtype=np.float64)
    ys = np.asarray(y, dtype=np.float64)
    if values.ndim != 2 or values.shape != (ys.size, xs.size):
        raise ValueError(
            f"field {values.shape} does not match the grid ({ys.size}, {xs.size})"
        )
    paths: list[np.ndarray] = []
    figure = plt.figure()
    try:
        axes = figure.add_subplot()
        for level in np.atleast_1d(np.asarray(levels, dtype=np.float64)):
            if period is None:
                plane, target = values, float(level)
            else:
                half = 0.5 * float(period)
                plane = (values - float(level) + half) % float(period) - half
                plane = np.where(np.abs(plane) > 0.5 * half, np.nan, plane)
                target = 0.0
            if not np.isfinite(plane).any():
                continue
            finite = plane[np.isfinite(plane)]
            if finite.min() > target or finite.max() < target:
                continue
            axes.clear()
            contours = axes.contour(xs, ys, plane, levels=[target])
            for path in contours.get_paths():
                for segment in path.to_polygons(closed_only=False):
                    if len(segment) >= min_points:
                        paths.append(np.asarray(segment, dtype=np.float64))
    finally:
        plt.close(figure)
    return paths


def graticule_paths(
    lat: Any,
    lon_east: Any,
    x_km: Any,
    y_km: Any,
    *,
    dlat: float = 2.0,
    dlon: float = 30.0,
    max_side: int = 400,
) -> list[np.ndarray]:
    """Parallels every ``dlat`` and meridians every ``dlon``, in km.

    The coordinate arrays come from the stack or strip itself, so the
    graticule is the projection's own, not an assumed one.  The grids are
    strided down to ``max_side`` first: a 3000 x 3200 canvas contours in
    tens of milliseconds at 400 x 400 and the lines are the same to
    within a pixel.
    """
    latitudes = np.asarray(lat, dtype=np.float64)
    longitudes = np.asarray(lon_east, dtype=np.float64)
    xs = np.asarray(x_km, dtype=np.float64)
    ys = np.asarray(y_km, dtype=np.float64)
    step_y = max(1, int(np.ceil(latitudes.shape[0] / max_side)))
    step_x = max(1, int(np.ceil(latitudes.shape[1] / max_side)))
    latitudes = latitudes[::step_y, ::step_x]
    longitudes = longitudes[::step_y, ::step_x]
    xs, ys = xs[::step_x], ys[::step_y]

    finite = np.isfinite(latitudes)
    if not finite.any():
        return []
    lat_levels = np.arange(
        np.ceil(np.nanmin(latitudes) / dlat) * dlat,
        np.nanmax(latitudes) + 0.5 * dlat,
        dlat,
    )
    lon_levels = np.arange(0.0, 360.0, dlon)
    paths = contour_paths(latitudes, xs, ys, lat_levels)
    paths += contour_paths(longitudes, xs, ys, lon_levels, period=360.0)
    return paths

_GRATICULE_CACHE: dict[tuple[str, float, float], list[np.ndarray]] = {}


def graticule_for(
    dataset: xr.Dataset,
    key: str,
    *,
    dlat: float = 2.0,
    dlon: float = 30.0,
    max_side: int = 400,
) -> list[np.ndarray]:
    """:func:`graticule_paths` for a stack or strip, cached on ``key``.

    The coordinate arrays are read strided, so a 3000 x 3200 canvas costs
    one small read rather than the 38 MB the full ``lat`` array would.
    """
    identity = (str(key), float(dlat), float(dlon))
    cached = _GRATICULE_CACHE.get(identity)
    if cached is not None:
        return cached
    if "lat" not in dataset.coords or "lon_east" not in dataset.coords:
        return []
    lat = dataset["lat"]
    step_y = max(1, int(np.ceil(lat.shape[0] / max_side)))
    step_x = max(1, int(np.ceil(lat.shape[1] / max_side)))
    paths = graticule_paths(
        lat[::step_y, ::step_x].values,
        dataset["lon_east"][::step_y, ::step_x].values,
        np.asarray(dataset["x_km"].values, dtype=np.float64)[::step_x],
        np.asarray(dataset["y_km"].values, dtype=np.float64)[::step_y],
        dlat=dlat,
        dlon=dlon,
        max_side=max_side,
    )
    _GRATICULE_CACHE[identity] = paths
    return paths
