"""Region stacks: listing, metadata, one frame as a PNG, and the jobs.

A frame stack is two gigabytes and the browser wants one time step of it
at a few hundred pixels a side, so nothing here loads a stack: the
dataset is opened lazily once and kept (:func:`gui.data.open_stack`), one
plane is read when a frame is asked for, and the display stretch comes
from a strided subsample of a few steps that is computed once and written
next to the mirror's other caches.

The three slow operations -- render a movie, build a stack, export a
goflow dataset -- are the existing module functions, submitted to the job
pool unchanged.  The server adds no numerics of its own.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
import xarray as xr
from fastapi import APIRouter, HTTPException, Query, Request, Response
from pydantic import BaseModel

from ..config import mirror_root
from ..gui import data as gui_data
from . import images

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stacks", tags=["stacks"])

#: Graticule spacing, degrees; the GUI v1 Poles tab draws the same one.
DLAT = 2.0
DLON = 30.0

#: Per-time coordinates a stack may carry, in the order the contract lists.
PER_TIME_COORDS: tuple[str, ...] = ("product_id", "seq_id", "orbit", "n_frames", "bore_emission")

#: Suffixes a rendered movie may have, best first.
MOVIE_SUFFIXES: tuple[str, ...] = (".mp4", ".gif")


# ---------------------------------------------------------------------------
# identity and paths
# ---------------------------------------------------------------------------
def stack_id(path: Path) -> str:
    """``<region>/<file stem>`` -- the identity the contract uses."""
    return f"{Path(path).parent.name}/{Path(path).stem}"


def stack_index(mirror: str | Path | None = None) -> dict[str, Path]:
    """Every ``<mirror>/regions/<region>/*.nc``, by identifier."""
    root = mirror_root(mirror)
    return {stack_id(path): path for path in gui_data.stack_paths(root)}


def resolve(mirror: str | Path | None, identifier: str) -> Path:
    """The file behind an identifier, or 404."""
    index = stack_index(mirror)
    path = index.get(str(identifier))
    if path is None:
        raise HTTPException(status_code=404, detail=f"unknown stack: {identifier}")
    return path


def movie_path(path: Path) -> Path | None:
    """The rendered movie beside a stack, if one exists."""
    for suffix in MOVIE_SUFFIXES:
        candidate = Path(path).with_suffix(suffix)
        if candidate.exists():
            return candidate
    return None


def cache_key(identifier: str) -> str:
    """An identifier flattened into one filename component."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "__", str(identifier))


def meta_cache_path(mirror: str | Path | None, identifier: str) -> Path:
    return gui_data.gui_cache_dir(mirror) / f"meta_{cache_key(identifier)}.json"


# ---------------------------------------------------------------------------
# metadata
# ---------------------------------------------------------------------------
def _scalar(value: Any) -> Any:
    """A NetCDF attribute or coordinate value as something JSON can hold."""
    if isinstance(value, (np.generic,)):
        value = value.item()
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        stamp = pd.Timestamp(value)
        return None if pd.isna(stamp) else stamp.isoformat()
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_scalar(item) for item in np.asarray(value).tolist()]
    return str(value)


def attrs_of(dataset: xr.Dataset) -> dict[str, Any]:
    return {str(name): _scalar(value) for name, value in dataset.attrs.items()}


def linestrings(paths: list[np.ndarray], properties: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A list of ``(N, 2)`` km polylines as a GeoJSON ``FeatureCollection``.

    GeoJSON is being used as a container for plane coordinates, not for
    longitude and latitude: the values are kilometres on the stack's own
    projection, which is the frame the image is served in, so the front
    end can draw lines and image in one coordinate system.
    """
    features = []
    for index, path in enumerate(paths):
        coordinates = np.asarray(path, dtype=np.float64)
        if coordinates.ndim != 2 or coordinates.shape[0] < 2:
            continue
        feature_properties = dict(properties[index]) if properties and index < len(properties) else {}
        features.append(
            {
                "type": "Feature",
                "properties": feature_properties,
                "geometry": {"type": "LineString", "coordinates": coordinates.tolist()},
            }
        )
    return {"type": "FeatureCollection", "features": features, "units": "km"}


def graticule_geojson(dataset: xr.Dataset, key: str) -> dict[str, Any]:
    """Parallels every 2 deg and meridians every 30 deg, in km."""
    try:
        paths = gui_data.graticule_for(dataset, key, dlat=DLAT, dlon=DLON)
    except (ValueError, KeyError) as exc:
        LOGGER.warning("no graticule for %s: %s", key, exc)
        paths = []
    return linestrings(paths)


def stretch_of(
    mirror: str | Path | None, identifier: str, dataset: xr.Dataset, path: Path
) -> dict[str, float]:
    """The 1st and 99th percentiles, computed once and cached on disk.

    The subsample is :func:`gui.data.stack_stretch`'s: a few time steps,
    strided to a couple of million values.  Reading every pixel of every
    step would cost a gigabyte of I/O for a number the eye cannot tell
    apart from this one.
    """
    cache = meta_cache_path(mirror, identifier)
    try:
        stat = path.stat()
        signature = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        signature = ""
    if cache.exists():
        try:
            stored = json.loads(cache.read_text(encoding="utf-8"))
            if stored.get("signature") == signature and "stretch" in stored:
                return {"p1": float(stored["stretch"]["p1"]), "p99": float(stored["stretch"]["p99"])}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            LOGGER.warning("unreadable stretch cache %s: %s", cache, exc)
    low, high = gui_data.stack_stretch(dataset, key=str(identifier))
    stretch = {"p1": float(low), "p99": float(high)}
    try:
        cache.write_text(json.dumps({"signature": signature, "stretch": stretch}), encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("cannot write %s: %s", cache, exc)
    return stretch


def per_time_records(dataset: xr.Dataset) -> list[dict[str, Any]]:
    """One record per time step, with whatever per-time coordinates exist.

    A frame stack carries ``product_id`` and ``bore_emission``; a sequence
    composite carries ``n_frames`` instead, because its steps are averages
    of several frames.  Neither is required.
    """
    steps = int(dataset.sizes.get("time", 0))
    records: list[dict[str, Any]] = [{"i": index} for index in range(steps)]
    for name in PER_TIME_COORDS:
        if name not in dataset.coords and name not in dataset.variables:
            continue
        variable = dataset[name]
        if variable.dims != ("time",):
            continue
        values = np.asarray(variable.values)
        for index in range(min(steps, values.shape[0])):
            records[index][name] = _scalar(values[index])
    return records


def listing(mirror: str | Path | None = None) -> list[dict[str, Any]]:
    """The stacks the mirror holds, with the sizes the browser shows."""
    entries: list[dict[str, Any]] = []
    for identifier, path in sorted(stack_index(mirror).items()):
        try:
            dataset = gui_data.open_stack(path)
        except (OSError, ValueError) as exc:
            LOGGER.warning("cannot open %s: %s", path, exc)
            continue
        movie = movie_path(path)
        entries.append(
            {
                "id": identifier,
                "region": str(dataset.attrs.get("region", Path(path).parent.name)),
                "band": _scalar(dataset.attrs.get("band")),
                "level": _scalar(dataset.attrs.get("level")),
                "path": str(path),
                "n_time": int(dataset.sizes.get("time", 0)),
                "shape": [int(dataset.sizes.get("y", 0)), int(dataset.sizes.get("x", 0))],
                "km_per_px": _scalar(dataset.attrs.get("km_per_px")),
                "size_bytes": int(path.stat().st_size) if path.exists() else 0,
                "has_movie": movie is not None,
                "movie_path": str(movie) if movie is not None else None,
            }
        )
    return entries


# ---------------------------------------------------------------------------
# byte ranges
# ---------------------------------------------------------------------------
def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """``bytes=a-b`` as an inclusive ``(first, last)``, or ``None``.

    Written out rather than delegated to ``FileResponse`` because the
    contract requires a 206 with a ``Content-Range`` for the movie player
    and the exact bytes asked for, and a helper that silently answers 200
    would pass a naive test and stall a ``<video>`` element.
    """
    if not header or size <= 0:
        return None
    match = re.fullmatch(r"\s*bytes\s*=\s*(\d*)\s*-\s*(\d*)\s*", str(header))
    if match is None:
        return None
    first_text, last_text = match.groups()
    if first_text == "" and last_text == "":
        return None
    if first_text == "":
        length = min(int(last_text), size)
        return (size - length, size - 1) if length > 0 else None
    first = int(first_text)
    last = int(last_text) if last_text else size - 1
    last = min(last, size - 1)
    if first > last or first >= size:
        return None
    return first, last


def range_response(path: Path, header: str | None, media_type: str) -> Response:
    """The whole file, or the requested slice of it as a 206."""
    size = path.stat().st_size
    window = parse_range(header, size)
    if window is None:
        return Response(
            content=path.read_bytes(),
            media_type=media_type,
            headers={"Accept-Ranges": "bytes", "Content-Length": str(size)},
        )
    first, last = window
    with path.open("rb") as handle:
        handle.seek(first)
        payload = handle.read(last - first + 1)
    return Response(
        content=payload,
        status_code=206,
        media_type=media_type,
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes {first}-{last}/{size}",
            "Content-Length": str(len(payload)),
        },
    )


# ---------------------------------------------------------------------------
# job bodies
# ---------------------------------------------------------------------------
class MovieRequest(BaseModel):
    fps: float | None = None
    pct: list[float] | None = None
    cmap: str | None = None


class BuildRequest(BaseModel):
    region: str
    band: str
    level: str = "frame"
    orbits: list[int] | None = None
    selection_id: str | None = None
    max_emission: float | None = None


class ExportRequest(BaseModel):
    out_dir: str | None = None
    dt_tol: float | None = None
    min_frames: int | None = None
    crop_to_valid: bool | None = None


def register_jobs(manager: Any, mirror: str | Path | None) -> None:
    """Bind the three slow operations to job kinds on ``manager``."""
    root = mirror_root(mirror)

    def movie(progress: Callable[..., None], *, stack: str, fps: float, pct: list[float], cmap: str) -> dict[str, Any]:
        from ..movie import write_movie

        path = resolve(root, stack)
        progress(0.05, f"rendering {stack}")
        dataset = gui_data.open_stack(path)
        target = Path(path).with_suffix(".mp4")
        summary = write_movie(
            dataset, target, fps=float(fps), percentiles=(float(pct[0]), float(pct[1])), cmap=str(cmap)
        )
        progress(1.0, f"wrote {target.name}")
        return {"path": str(summary["path"]), "frames": int(summary["frames"])}

    def build(
        progress: Callable[..., None],
        *,
        region: str,
        band: str,
        level: str,
        orbits: list[int] | None,
        selection_id: str | None,
        max_emission: float | None,
    ) -> dict[str, Any]:
        from .. import stacks as stacks_module
        from .selections import load_one

        progress(0.02, "selecting frames")
        selected = stacks_module.select_frames(
            root,
            region,
            orbits,
            band,
            **({} if max_emission is None else {"max_emission": float(max_emission)}),
        )
        if selection_id:
            wanted = set(load_one(root, selection_id).get("product_ids", []))
            selected = selected.loc[selected["product_id"].astype(str).isin(wanted)]
        if selected.empty:
            raise ValueError("no frame of that region, band and orbit set survives the cuts")
        progress(0.1, f"reprojecting {len(selected)} frame(s)")
        # jobs=1: build_stack spawns its own ``spawn`` process pool above
        # one, and a spawned pool inside a server worker thread would
        # re-import and re-serve the app in every child.
        dataset = stacks_module.build_stack(root, region, selected, band, jobs=1)
        if str(level).lower() == "sequence":
            progress(0.85, "compositing sequences")
            dataset = stacks_module.composite_sequences(dataset)
        token = "all" if not orbits else ",".join(str(int(value)) for value in sorted(set(orbits)))
        target = stacks_module.stack_output_path(root, region, band, token, str(level).lower())
        progress(0.92, f"writing {target.name}")
        stacks_module.write_stack(dataset, target)
        progress(1.0, "done")
        return {"stack_id": stack_id(target), "path": str(target)}

    def export(
        progress: Callable[..., None],
        *,
        stack: str,
        out_dir: str | None,
        dt_tol: float | None,
        min_frames: int | None,
        crop_to_valid: bool | None,
    ) -> dict[str, Any]:
        from ..export_goflow import export_stack

        path = resolve(root, stack)
        dataset = gui_data.open_stack(path)
        destination = Path(out_dir) if out_dir else gui_data.export_dir(root) / f"goflow_{cache_key(stack)}"
        progress(0.1, f"exporting to {destination}")
        options: dict[str, Any] = {}
        if dt_tol is not None:
            options["dt_tol"] = float(dt_tol)
        if min_frames is not None:
            options["min_frames"] = int(min_frames)
        if crop_to_valid is not None:
            options["crop_to_valid"] = bool(crop_to_valid)
        manifest = export_stack(dataset, destination, source=path, **options)
        progress(1.0, "done")
        return {"out_dir": str(destination), "n_realizations": int(manifest.get("n_realizations", 0))}

    manager.register("movie", movie)
    manager.register("stack_build", build)
    manager.register("goflow_export", export)


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
@router.get("")
def get_stacks(request: Request) -> list[dict[str, Any]]:
    return listing(request.app.state.mirror)


@router.post("/build")
def post_build(request: Request, body: BuildRequest) -> dict[str, str]:
    record = request.app.state.jobs.submit(
        "stack_build",
        {
            "region": body.region,
            "band": body.band,
            "level": body.level,
            "orbits": body.orbits,
            "selection_id": body.selection_id,
            "max_emission": body.max_emission,
        },
    )
    return {"job_id": record["id"]}


@router.get("/{identifier:path}/meta")
def get_meta(request: Request, identifier: str) -> dict[str, Any]:
    mirror = request.app.state.mirror
    path = resolve(mirror, identifier)
    dataset = gui_data.open_stack(path)
    x_km = np.asarray(dataset["x_km"].values, dtype=np.float64)
    y_km = np.asarray(dataset["y_km"].values, dtype=np.float64)
    times = (
        pd.DatetimeIndex(pd.to_datetime(np.asarray(dataset["time"].values)))
        if "time" in dataset.coords
        else pd.DatetimeIndex([])
    )
    return {
        "id": identifier,
        "region": str(dataset.attrs.get("region", Path(path).parent.name)),
        "band": _scalar(dataset.attrs.get("band")),
        "level": _scalar(dataset.attrs.get("level")),
        "km_per_px": _scalar(dataset.attrs.get("km_per_px")),
        "x_km": [float(x_km.min()), float(x_km.max())],
        "y_km": [float(y_km.min()), float(y_km.max())],
        "shape": [int(dataset.sizes.get("y", 0)), int(dataset.sizes.get("x", 0))],
        "times": [pd.Timestamp(value).isoformat() for value in times],
        "per_time": per_time_records(dataset),
        "stretch": stretch_of(mirror, identifier, dataset, path),
        "graticule": graticule_geojson(dataset, f"stack::{identifier}"),
        "attrs": attrs_of(dataset),
    }


@router.get("/{identifier:path}/frame/{index}/emission.png")
def get_emission_png(
    request: Request, identifier: str, index: int, max_px: int = Query(default=images.DEFAULT_MAX_PX, ge=16, le=8000)
) -> Response:
    dataset = gui_data.open_stack(resolve(request.app.state.mirror, identifier))
    plane, valid = _plane(dataset, "emission", index)
    payload, stride, shape = images.emission_png(plane, valid, max_px=max_px)
    return _png_response(dataset, payload, stride, shape)


@router.get("/{identifier:path}/frame/{index}.png")
def get_frame_png(
    request: Request,
    identifier: str,
    index: int,
    vmin: float | None = None,
    vmax: float | None = None,
    max_px: int = Query(default=images.DEFAULT_MAX_PX, ge=16, le=8000),
) -> Response:
    mirror = request.app.state.mirror
    path = resolve(mirror, identifier)
    dataset = gui_data.open_stack(path)
    stretch = stretch_of(mirror, identifier, dataset, path)
    plane, valid = _plane(dataset, "image", index)
    payload, stride, shape = images.plane_png(
        plane,
        valid,
        vmin=stretch["p1"] if vmin is None else vmin,
        vmax=stretch["p99"] if vmax is None else vmax,
        max_px=max_px,
    )
    return _png_response(dataset, payload, stride, shape)


@router.get("/{identifier:path}/movie")
def get_movie(request: Request, identifier: str) -> Response:
    path = resolve(request.app.state.mirror, identifier)
    movie = movie_path(path)
    if movie is None:
        raise HTTPException(status_code=404, detail=f"no movie beside {identifier}")
    media = "video/mp4" if movie.suffix.lower() == ".mp4" else "image/gif"
    return range_response(movie, request.headers.get("range"), media)


@router.post("/{identifier:path}/movie")
def post_movie(request: Request, identifier: str, body: MovieRequest) -> dict[str, str]:
    resolve(request.app.state.mirror, identifier)
    percentiles = body.pct if body.pct and len(body.pct) == 2 else [1.0, 99.0]
    record = request.app.state.jobs.submit(
        "movie",
        {
            "stack": identifier,
            "fps": 4.0 if body.fps is None else float(body.fps),
            "pct": [float(percentiles[0]), float(percentiles[1])],
            "cmap": body.cmap or "gray",
        },
    )
    return {"job_id": record["id"]}


@router.post("/{identifier:path}/export")
def post_export(request: Request, identifier: str, body: ExportRequest) -> dict[str, str]:
    resolve(request.app.state.mirror, identifier)
    record = request.app.state.jobs.submit(
        "goflow_export",
        {
            "stack": identifier,
            "out_dir": body.out_dir,
            "dt_tol": body.dt_tol,
            "min_frames": body.min_frames,
            "crop_to_valid": body.crop_to_valid,
        },
    )
    return {"job_id": record["id"]}


# ---------------------------------------------------------------------------
# helpers the routes share
# ---------------------------------------------------------------------------
def _plane(dataset: xr.Dataset, name: str, index: int) -> tuple[np.ndarray, np.ndarray | None]:
    """One time step of ``name`` and its validity mask."""
    if name not in dataset:
        raise HTTPException(status_code=404, detail=f"the stack has no {name!r} variable")
    steps = int(dataset.sizes.get("time", 1))
    if not 0 <= int(index) < steps:
        raise HTTPException(status_code=404, detail=f"time index {index} outside 0..{steps - 1}")
    variable = dataset[name]
    plane = variable.isel(time=int(index)) if "time" in variable.dims else variable
    valid = None
    if "valid" in dataset:
        mask = dataset["valid"]
        valid = np.asarray((mask.isel(time=int(index)) if "time" in mask.dims else mask).values, dtype=bool)
    return np.asarray(plane.values), valid


def _png_response(dataset: xr.Dataset, payload: bytes, stride: int, shape: tuple[int, int]) -> Response:
    bounds = images.bounds_header(dataset["x_km"].values, dataset["y_km"].values, stride, shape)
    return Response(content=payload, media_type="image/png", headers=images.image_headers(shape, stride, bounds))
