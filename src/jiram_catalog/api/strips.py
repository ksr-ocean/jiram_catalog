"""The strip library: the index as Arrow, one strip's metadata, image, stats.

A strip is a few megabytes rather than a few gigabytes, so the shortcuts
the stack endpoints need are not needed here -- but the code is the same
code, because a strip and a stack time step are the same thing to the
viewer: a plane on a kilometre grid with a validity mask.  What a strip
adds is the local-time clock, contoured every two hours, which is what
turns "where did the spacecraft look" into "at what hour of the Jovian
day", and the turbulence statistics, which are cached under the mirror by
:func:`data.strip_stats` and therefore computed once ever.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import xarray as xr
from fastapi import APIRouter, HTTPException, Query, Request, Response

from . import data, images
from .arrow import ARROW_MEDIA_TYPE, generic_ipc
from .stacks import DLAT, DLON, attrs_of, band_names, graticule_geojson, linestrings

LOGGER = logging.getLogger(__name__)

router = APIRouter(tags=["strips"])

#: Local-time contours, hours.
LOCAL_TIME_STEP_H = 2.0

#: Names the contract gives the statistics Dataset's axes.
STATS_NAMES: dict[str, str] = {"k": "k", "kx": "k_x", "ky": "k_y", "r": "r_m"}
STATS_VARIABLES: tuple[str, ...] = ("E", "P_x", "P_y", "S2", "S3")


def open_strip(mirror: Any, strip_id: str) -> xr.Dataset:
    """One strip by identifier, or 404."""
    try:
        return data.open_strip(mirror, str(strip_id))
    except (ValueError, KeyError, FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail=f"unknown strip: {strip_id}") from exc


def local_time_geojson(dataset: xr.Dataset, key: str) -> dict[str, Any]:
    """Contours of the local-time clock every two hours, in km.

    Contoured with a period of 24 h, so the midnight line comes out as
    one curve rather than as two halves with the seam drawn between them.
    """
    if "local_time_h" not in dataset.coords and "local_time_h" not in dataset.variables:
        return linestrings([])
    levels = np.arange(0.0, 24.0, LOCAL_TIME_STEP_H)
    try:
        field = np.asarray(dataset["local_time_h"].values, dtype=np.float64)
        paths = data.contour_paths(
            field,
            np.asarray(dataset["x_km"].values, dtype=np.float64),
            np.asarray(dataset["y_km"].values, dtype=np.float64),
            levels,
            period=24.0,
        )
    except (ValueError, KeyError) as exc:
        LOGGER.warning("no local-time contours for %s: %s", key, exc)
        return linestrings([])
    return linestrings(paths)


def band_plane(dataset: xr.Dataset, band: str | None) -> tuple[xr.Dataset, str | None]:
    """The strip reduced to one band, and the band's name.

    A JIRAM strip has no band axis and comes back unchanged.  A JunoCam strip
    does, and -- unlike a stack, where the contract makes ``band`` required --
    a strip defaults to its first band, because a strip is one image and its
    bands are one scene in three colours rather than three products.
    """
    names = band_names(dataset)
    if not names:
        return dataset, None
    wanted = names[0] if band is None else str(band).strip().upper()
    upper = [name.upper() for name in names]
    if wanted.upper() not in upper:
        raise HTTPException(
            status_code=404, detail=f"unknown band {band!r}; the strip carries {names}"
        )
    return dataset.isel(band=upper.index(wanted.upper())), names[upper.index(wanted.upper())]


def stretch_of(dataset: xr.Dataset, key: str) -> dict[str, float]:
    """The strip's display limits, from the same subsampler the stacks use."""
    low, high = data.stack_stretch(dataset, key=key)
    return {"p1": float(low), "p99": float(high)}


def _finite_list(values: Any) -> list[float | None]:
    """A float array as JSON: every non-finite value becomes ``null``."""
    array = np.asarray(values, dtype=np.float64).ravel()
    return [None if not np.isfinite(value) else float(value) for value in array]


def statistics_payload(statistics: xr.Dataset) -> dict[str, Any]:
    """The statistics Dataset under the contract's names."""
    payload: dict[str, Any] = {}
    for source, name in STATS_NAMES.items():
        if source in statistics.coords:
            payload[name] = _finite_list(statistics[source].values)
        else:
            payload[name] = []
    for name in STATS_VARIABLES:
        payload[name] = _finite_list(statistics[name].values) if name in statistics else []
    payload["attrs"] = {str(key): _attr(value) for key, value in statistics.attrs.items()}
    return payload


def _attr(value: Any) -> Any:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@router.get("/api/strips.arrow")
def strips_arrow(request: Request) -> Response:
    """The strip index as an Arrow IPC stream; datetimes as ``<name>_ms``."""
    table = data.strips_table(request.app.state.mirror)
    return Response(
        content=generic_ipc(table),
        media_type=ARROW_MEDIA_TYPE,
        headers={"Cache-Control": "no-cache"},
    )


@router.get("/api/strips/{strip_id}/meta")
def strip_meta(request: Request, strip_id: str) -> dict[str, Any]:
    dataset = open_strip(request.app.state.mirror, strip_id)
    x_km = np.asarray(dataset["x_km"].values, dtype=np.float64)
    y_km = np.asarray(dataset["y_km"].values, dtype=np.float64)
    key = f"strip::{strip_id}"
    names = band_names(dataset)
    return {
        "id": strip_id,
        "attrs": attrs_of(dataset),
        "instrument": str(dataset.attrs.get("instrument", "JIRAM")),
        "bands": names,
        "x_km": [float(x_km.min()), float(x_km.max())],
        "y_km": [float(y_km.min()), float(y_km.max())],
        "shape": [int(dataset.sizes.get("y", 0)), int(dataset.sizes.get("x", 0))],
        "stretch": (
            {name: stretch_of(band_plane(dataset, name)[0], f"{key}::{name}") for name in names}
            if names
            else stretch_of(dataset, key)
        ),
        "graticule": graticule_geojson(dataset, key),
        "local_time_contours": local_time_geojson(dataset, key),
    }


@router.get("/api/strips/{strip_id}/image.png")
def strip_image(
    request: Request,
    strip_id: str,
    band: str | None = None,
    vmin: float | None = None,
    vmax: float | None = None,
    max_px: int = Query(default=images.DEFAULT_MAX_PX, ge=16, le=8000),
) -> Response:
    dataset = open_strip(request.app.state.mirror, strip_id)
    if "image" not in dataset:
        raise HTTPException(status_code=404, detail=f"strip {strip_id} has no image")
    dataset, selected = band_plane(dataset, band)
    key = f"strip::{strip_id}" + (f"::{selected}" if selected else "")
    stretch = stretch_of(dataset, key)
    valid = np.asarray(dataset["valid"].values, dtype=bool) if "valid" in dataset else None
    payload, stride, shape = images.plane_png(
        np.asarray(dataset["image"].values),
        valid,
        vmin=stretch["p1"] if vmin is None else vmin,
        vmax=stretch["p99"] if vmax is None else vmax,
        max_px=max_px,
    )
    bounds = images.bounds_header(dataset["x_km"].values, dataset["y_km"].values, stride, shape)
    return Response(
        content=payload, media_type="image/png", headers=images.image_headers(shape, stride, bounds)
    )


@router.get("/api/strips/{strip_id}/stats")
def strip_stats(request: Request, strip_id: str, band: str | None = None) -> dict[str, Any]:
    """Spectra and structure functions, computed once and cached on disk.

    ``band`` picks one band of a multi-band strip and defaults to its first;
    each band's statistics get their own cache file.
    """
    mirror = request.app.state.mirror
    try:
        statistics = data.strip_stats(mirror, str(strip_id), band)
    except (ValueError, KeyError, FileNotFoundError, OSError) as exc:
        raise HTTPException(status_code=404, detail=f"unknown strip: {strip_id}") from exc
    return statistics_payload(statistics)


#: Re-exported so a reader can see the strip graticule is the stacks'.
__all__ = ["router", "DLAT", "DLON", "statistics_payload", "local_time_geojson"]
