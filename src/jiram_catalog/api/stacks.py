"""Region stacks: listing, metadata, one frame as a PNG, and the jobs.

A frame stack is two gigabytes and the browser wants one time step of it
at a few hundred pixels a side, so nothing here loads a stack: the
dataset is opened lazily once and kept (:func:`data.open_stack`), one
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
from pydantic import BaseModel, field_validator, model_validator

from ..config import mirror_root
from . import data, images

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stacks", tags=["stacks"])

#: Graticule spacing, degrees; the Poles view draws the same one.
DLAT = 2.0
DLON = 30.0

#: Per-time coordinates a stack may carry, in the order the contract lists.
#: ``seq_index``/``seq_n`` come from a cumulative stack, where a step is one
#: frame's worth of a sweep rather than a whole one.
PER_TIME_COORDS: tuple[str, ...] = (
    "product_id",
    "seq_id",
    "orbit",
    "n_frames",
    "bore_emission",
    "seq_index",
    "seq_n",
    # JunoCam only: the tier the quality table gave the image and the
    # limb-fitted offset that was applied to its epochs (NaN where the image
    # had too little sunlit limb to fit one).
    "quality_tier",
    "dt_refined_s",
)

#: Which bands a colour composite puts on which channel, when the stack has
#: them; a stack without all three falls back to its first three bands.
RGB_BANDS: tuple[str, ...] = ("RED", "GREEN", "BLUE")

#: Suffixes a rendered movie may have, best first.
MOVIE_SUFFIXES: tuple[str, ...] = (".mp4", ".gif")

#: The three ways to watch a region, in the order the listing and the mode
#: selector offer them: the snapshot per sweep first because it is what the
#: velocity model eats, then the sweep filling in, then the raw frames.
LEVEL_ORDER: tuple[str, ...] = ("sequence", "cumulative", "frame")

#: What the browser calls each level.
LEVEL_LABELS: dict[str, str] = {
    "frame": "Instrument frames",
    "sequence": "Region snapshots",
    "cumulative": "Accumulating sweep",
}

#: ``<band>_orbits<token>_<level>``, the stem :func:`stacks.stack_output_path`
#: writes.  The orbit token is greedy-free on the right so that a token with
#: an underscore in it (``4_5_6``) still leaves the level behind.
STEM_PATTERN = re.compile(r"(?P<band>[A-Za-z]+)_orbits(?P<orbits>.+)_(?P<level>[A-Za-z]+)")


# ---------------------------------------------------------------------------
# identity and paths
# ---------------------------------------------------------------------------
def stack_id(path: Path) -> str:
    """``<region>/<file stem>`` -- the identity the contract uses."""
    return f"{Path(path).parent.name}/{Path(path).stem}"


def stack_index(mirror: str | Path | None = None) -> dict[str, Path]:
    """Every ``<mirror>/regions/<region>/*.nc``, by identifier."""
    root = mirror_root(mirror)
    return {stack_id(path): path for path in data.stack_paths(root)}


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
    return data.gui_cache_dir(mirror) / f"meta_{cache_key(identifier)}.json"


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


def band_names(dataset: xr.Dataset) -> list[str]:
    """The stack's band names, or an empty list for a band-less stack.

    The names come from the ``band`` coordinate rather than from the ``bands``
    attribute, because the coordinate is what an index into the array means.
    """
    if "band" not in dataset.dims:
        return []
    if "band" in dataset.coords:
        return [str(value) for value in np.asarray(dataset["band"].values)]
    return [str(index) for index in range(int(dataset.sizes["band"]))]


def instrument_of(dataset: xr.Dataset) -> str:
    """The instrument a stack came from; a file written before the amendment
    carries no such attribute and can only be JIRAM."""
    return str(dataset.attrs.get("instrument", "JIRAM"))


def band_index(dataset: xr.Dataset, band: str | None) -> int | None:
    """Position of ``band`` in the stack, or ``None`` for a band-less stack.

    A multi-band stack has no default band -- the contract makes ``band``
    required for it -- because serving one of three colours as though it were
    "the" image is exactly the confusion the band axis exists to prevent.
    """
    names = band_names(dataset)
    if not names:
        return None
    if band is None:
        raise HTTPException(
            status_code=400,
            detail=f"this stack has several bands; pass band=<one of {', '.join(names)}>",
        )
    wanted = str(band).strip().upper()
    upper = [name.upper() for name in names]
    if wanted not in upper:
        raise HTTPException(
            status_code=404, detail=f"unknown band {band!r}; the stack carries {names}"
        )
    return upper.index(wanted)


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
        paths = data.graticule_for(dataset, key, dlat=DLAT, dlon=DLON)
    except (ValueError, KeyError) as exc:
        LOGGER.warning("no graticule for %s: %s", key, exc)
        paths = []
    return linestrings(paths)


def stretch_of(
    mirror: str | Path | None,
    identifier: str,
    dataset: xr.Dataset,
    path: Path,
    band: str | None = None,
) -> dict[str, float]:
    """The 1st and 99th percentiles, computed once and cached on disk.

    The subsample is :func:`data.stack_stretch`'s: a few time steps,
    strided to a couple of million values.  Reading every pixel of every
    step would cost a gigabyte of I/O for a number the eye cannot tell
    apart from this one.

    A multi-band stack gets one stretch per band -- three colour strips of one
    camera differ in throughput by tens of per cent, so a shared stretch would
    tint the composite -- and they share one cache file keyed by band name.
    """
    cache = meta_cache_path(mirror, identifier)
    try:
        stat = path.stat()
        signature = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        signature = ""
    key = "" if band is None else str(band).strip().upper()
    entries: dict[str, dict[str, float]] = {}
    if cache.exists():
        try:
            stored = json.loads(cache.read_text(encoding="utf-8"))
            if stored.get("signature") == signature:
                entries = {str(k): dict(v) for k, v in (stored.get("bands") or {}).items()}
                if "stretch" in stored and "" not in entries:
                    entries[""] = dict(stored["stretch"])
                if key in entries:
                    return {
                        "p1": float(entries[key]["p1"]),
                        "p99": float(entries[key]["p99"]),
                    }
        except (OSError, ValueError, KeyError, TypeError) as exc:
            LOGGER.warning("unreadable stretch cache %s: %s", cache, exc)
    plane = dataset
    if key:
        index = band_index(dataset, key)
        plane = dataset.isel(band=index) if index is not None else dataset
    low, high = data.stack_stretch(plane, key=f"{identifier}::{key}")
    stretch = {"p1": float(low), "p99": float(high)}
    entries[key] = stretch
    payload: dict[str, Any] = {"signature": signature, "bands": entries}
    if "" in entries:
        payload["stretch"] = entries[""]
    try:
        cache.write_text(json.dumps(payload), encoding="utf-8")
    except OSError as exc:
        LOGGER.warning("cannot write %s: %s", cache, exc)
    return stretch


def stretch_for_meta(
    mirror: str | Path | None, identifier: str, dataset: xr.Dataset, path: Path
) -> dict[str, Any]:
    """``{p1, p99}`` for a band-less stack, ``{band: {p1, p99}}`` for a banded one."""
    names = band_names(dataset)
    if not names:
        return stretch_of(mirror, identifier, dataset, path)
    return {name: stretch_of(mirror, identifier, dataset, path, name) for name in names}


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


def level_label(level: Any) -> str:
    """The browser's name for a level; an unknown level names itself."""
    text = "" if level is None else str(level)
    return LEVEL_LABELS.get(text, text)


def parse_stem(stem: str) -> dict[str, str] | None:
    """``M_orbits4_frame`` -> band, orbit token and level, or ``None``.

    Only the three known levels count: a file written with ``--out`` to some
    other name has no orbit token the API can trust, and guessing one would
    make two unrelated stacks each other's siblings.
    """
    match = STEM_PATTERN.fullmatch(str(stem))
    if match is None or match.group("level").lower() not in LEVEL_LABELS:
        return None
    return {
        "band": match.group("band").upper(),
        "orbits": match.group("orbits").lower(),
        "level": match.group("level").lower(),
    }


def sibling_key(identifier: str, entry: dict[str, Any]) -> tuple[str, str, str] | None:
    """What makes two stacks two views of the same thing.

    The region is the directory the file sits in rather than the ``region``
    attribute, because that is what the identifier is built from: two stacks
    are siblings only if a viewer can reach one from the other by name.
    """
    parsed = parse_stem(Path(identifier).name)
    if parsed is None:
        return None
    band = str(entry.get("band") or parsed["band"]).upper()
    return (str(Path(identifier).parent), band, parsed["orbits"])


def listing_order(entry: dict[str, Any]) -> tuple[Any, ...]:
    """Region, band, then sequence before cumulative before frame."""
    level = str(entry.get("level") or "")
    rank = LEVEL_ORDER.index(level) if level in LEVEL_ORDER else len(LEVEL_ORDER)
    return (str(entry.get("region") or ""), str(entry.get("band") or ""), rank, str(entry["id"]))


def attach_siblings(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every entry the ids of the other levels of the same stack.

    A stack lists itself under its own level, so the mode selector can read
    ``siblings[mode]`` for all three buttons instead of special-casing the one
    it is already showing.
    """
    families: dict[tuple[str, str, str], dict[str, str]] = {}
    for entry in entries:
        key = sibling_key(str(entry["id"]), entry)
        level = str(entry.get("level") or "")
        if key is None or level not in LEVEL_LABELS:
            continue
        families.setdefault(key, {}).setdefault(level, str(entry["id"]))
    for entry in entries:
        key = sibling_key(str(entry["id"]), entry)
        entry["siblings"] = dict(families.get(key, {})) if key is not None else {}
    return entries


def listing(mirror: str | Path | None = None) -> list[dict[str, Any]]:
    """The stacks the mirror holds, with the sizes the browser shows."""
    entries: list[dict[str, Any]] = []
    for identifier, path in sorted(stack_index(mirror).items()):
        try:
            dataset = data.open_stack(path)
        except (OSError, ValueError) as exc:
            LOGGER.warning("cannot open %s: %s", path, exc)
            continue
        movie = movie_path(path)
        level = _scalar(dataset.attrs.get("level"))
        entries.append(
            {
                "id": identifier,
                "region": str(dataset.attrs.get("region", Path(path).parent.name)),
                "instrument": instrument_of(dataset),
                "band": _scalar(dataset.attrs.get("band")),
                "bands": band_names(dataset) or [str(dataset.attrs.get("band", ""))],
                "level": level,
                "label": level_label(level),
                "path": str(path),
                "n_time": int(dataset.sizes.get("time", 0)),
                "shape": [int(dataset.sizes.get("y", 0)), int(dataset.sizes.get("x", 0))],
                "km_per_px": _scalar(dataset.attrs.get("km_per_px")),
                "size_bytes": int(path.stat().st_size) if path.exists() else 0,
                "has_movie": movie is not None,
                "movie_path": str(movie) if movie is not None else None,
            }
        )
    return sorted(attach_siblings(entries), key=listing_order)


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
    band: str = ""
    level: str = "frame"
    orbits: list[int] | None = None
    selection_id: str | None = None
    max_emission: float | None = None
    instrument: str = "JIRAM"
    bands: list[str] | None = None
    quality_min: str = "A"

    @field_validator("level")
    @classmethod
    def _known_level(cls, value: str) -> str:
        """Reject a level the builder has no rule for, before the job starts.

        A job that fails eight minutes into a reprojection because its level
        was a typo is a worse answer than a 422 on the request.
        """
        text = str(value).strip().lower()
        if text not in LEVEL_LABELS:
            raise ValueError(f"level must be one of {', '.join(LEVEL_ORDER)}")
        return text

    @model_validator(mode="after")
    def _band_or_bands(self) -> "BuildRequest":
        """A JIRAM build names one band; a JunoCam build names a band list.

        Rejected here rather than in the job, for the same reason the level is:
        a 422 on the request beats a failure eight minutes into a reprojection.
        """
        if str(self.instrument).strip().lower() == "junocam":
            return self
        if not str(self.band).strip():
            raise ValueError("band is required for a JIRAM build")
        return self


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
        dataset = data.open_stack(path)
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
        instrument: str = "JIRAM",
        bands: list[str] | None = None,
        quality_min: str = "A",
    ) -> dict[str, Any]:
        from .. import stacks as stacks_module
        from .selections import load_one

        if str(instrument).lower() == "junocam":
            return _build_junocam(
                progress,
                root=root,
                region=region,
                orbits=orbits,
                bands=bands,
                quality_min=quality_min,
                max_emission=max_emission,
                selection_id=selection_id,
            )
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
        wanted_level = str(level).lower()
        if wanted_level == "sequence":
            progress(0.85, "compositing sequences")
            dataset = stacks_module.composite_sequences(dataset)
        elif wanted_level == "cumulative":
            progress(0.85, "accumulating sweeps")
            dataset = stacks_module.accumulate_sequences(dataset)
        token = "all" if not orbits else ",".join(str(int(value)) for value in sorted(set(orbits)))
        target = stacks_module.stack_output_path(root, region, band, token, wanted_level)
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
        dataset = data.open_stack(path)
        destination = Path(out_dir) if out_dir else data.export_dir(root) / f"goflow_{cache_key(stack)}"
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


def _build_junocam(
    progress: Callable[..., None],
    *,
    root: Path,
    region: str,
    orbits: list[int] | None,
    bands: list[str] | None,
    quality_min: str,
    max_emission: float | None,
    selection_id: str | None,
) -> dict[str, Any]:
    """The JunoCam arm of the build job: same shape, other instrument."""
    from ..junocam import stacks as junocam_stacks
    from ..stacks import write_stack
    from .selections import load_one

    names = [str(name).upper() for name in (bands or list(RGB_BANDS))]
    progress(0.02, "selecting images")
    selected = junocam_stacks.select_images(
        root,
        region,
        orbits,
        quality_min=quality_min,
        bands=names,
        **({} if max_emission is None else {"max_emission": float(max_emission)}),
    )
    if selection_id:
        wanted = set(load_one(root, selection_id).get("product_ids", []))
        selected = selected.loc[selected["product_id"].astype(str).isin(wanted)]
    if selected.empty:
        raise ValueError("no JunoCam image of that region, band set and orbit set survives the cuts")
    progress(0.1, f"reprojecting {len(selected)} image(s)")
    # jobs=1: build_stack spawns its own ``spawn`` pool above one, and a
    # spawned pool inside a server worker thread would re-import the app.
    dataset = junocam_stacks.build_stack(root, region, selected, names, jobs=1)
    token = "all" if not orbits else ",".join(str(int(value)) for value in sorted(set(orbits)))
    target = junocam_stacks.stack_output_path(root, region, names, token, "frame")
    progress(0.92, f"writing {target.name}")
    write_stack(dataset, target)
    progress(1.0, "done")
    return {"stack_id": stack_id(target), "path": str(target)}


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
            "instrument": body.instrument,
            "bands": body.bands,
            "quality_min": body.quality_min,
        },
    )
    return {"job_id": record["id"]}


@router.get("/{identifier:path}/meta")
def get_meta(request: Request, identifier: str) -> dict[str, Any]:
    mirror = request.app.state.mirror
    path = resolve(mirror, identifier)
    dataset = data.open_stack(path)
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
        "instrument": instrument_of(dataset),
        "band": _scalar(dataset.attrs.get("band")),
        "bands": band_names(dataset),
        "level": _scalar(dataset.attrs.get("level")),
        "km_per_px": _scalar(dataset.attrs.get("km_per_px")),
        "x_km": [float(x_km.min()), float(x_km.max())],
        "y_km": [float(y_km.min()), float(y_km.max())],
        "shape": [int(dataset.sizes.get("y", 0)), int(dataset.sizes.get("x", 0))],
        "times": [pd.Timestamp(value).isoformat() for value in times],
        "per_time": per_time_records(dataset),
        "stretch": stretch_for_meta(mirror, identifier, dataset, path),
        "graticule": graticule_geojson(dataset, f"stack::{identifier}"),
        "attrs": attrs_of(dataset),
    }


@router.get("/{identifier:path}/frame/{index}/emission.png")
def get_emission_png(
    request: Request,
    identifier: str,
    index: int,
    band: str | None = None,
    max_px: int = Query(default=images.DEFAULT_MAX_PX, ge=16, le=8000),
) -> Response:
    dataset = data.open_stack(resolve(request.app.state.mirror, identifier))
    plane, valid = _plane(dataset, "emission", index, band)
    payload, stride, shape = images.emission_png(plane, valid, max_px=max_px)
    return _png_response(dataset, payload, stride, shape)


@router.get("/{identifier:path}/frame/{index}/rgb.png")
def get_rgb_png(
    request: Request,
    identifier: str,
    index: int,
    vmin_r: float | None = None,
    vmax_r: float | None = None,
    vmin_g: float | None = None,
    vmax_g: float | None = None,
    vmin_b: float | None = None,
    vmax_b: float | None = None,
    max_px: int = Query(default=images.DEFAULT_MAX_PX, ge=16, le=8000),
) -> Response:
    """One time step of a multi-band stack as a colour composite.

    Each channel is stretched on its own limits, defaulting to that band's
    entry in ``meta.stretch``: the three JunoCam colour strips differ in
    throughput by tens of per cent and share a stretch only at the cost of a
    tint that is the camera's, not the planet's.
    """
    mirror = request.app.state.mirror
    path = resolve(mirror, identifier)
    dataset = data.open_stack(path)
    names = band_names(dataset)
    if not names:
        raise HTTPException(status_code=404, detail=f"{identifier} has no band dimension")
    upper = [name.upper() for name in names]
    channels = (
        list(RGB_BANDS)
        if all(name in upper for name in RGB_BANDS)
        else [names[position] for position in range(min(3, len(names)))]
    )
    limits = (
        (vmin_r, vmax_r),
        (vmin_g, vmax_g),
        (vmin_b, vmax_b),
    )
    planes: list[np.ndarray] = []
    stretched: list[np.ndarray] = []
    mask: np.ndarray | None = None
    stride = 1
    for position, name in enumerate(channels):
        plane, valid = _plane(dataset, "image", index, name)
        if not planes:
            stride = images.stride_for(plane.shape, max_px)
        sub = np.asarray(plane)[::stride, ::stride]
        stretch = stretch_of(mirror, identifier, dataset, path, name)
        low = stretch["p1"] if limits[position][0] is None else float(limits[position][0])
        high = stretch["p99"] if limits[position][1] is None else float(limits[position][1])
        grey, finite = images.stretch_to_uint8(sub, low, high)
        if valid is not None:
            finite = finite & np.asarray(valid, dtype=bool)[::stride, ::stride]
        mask = finite if mask is None else (mask | finite)
        planes.append(sub)
        stretched.append(grey)
    payload = _rgba_png(stretched, mask)
    shape = (int(stretched[0].shape[0]), int(stretched[0].shape[1]))
    return _png_response(dataset, payload, stride, shape)


@router.get("/{identifier:path}/frame/{index}.png")
def get_frame_png(
    request: Request,
    identifier: str,
    index: int,
    band: str | None = None,
    vmin: float | None = None,
    vmax: float | None = None,
    max_px: int = Query(default=images.DEFAULT_MAX_PX, ge=16, le=8000),
) -> Response:
    mirror = request.app.state.mirror
    path = resolve(mirror, identifier)
    dataset = data.open_stack(path)
    stretch = stretch_of(mirror, identifier, dataset, path, band if band_names(dataset) else None)
    plane, valid = _plane(dataset, "image", index, band)
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
def _plane(
    dataset: xr.Dataset, name: str, index: int, band: str | None = None
) -> tuple[np.ndarray, np.ndarray | None]:
    """One time step of ``name``, in one band, and its validity mask.

    ``valid`` has no band axis even where the image does -- it says the map
    pixel was painted at all -- so it is selected on time alone.
    """
    if name not in dataset:
        raise HTTPException(status_code=404, detail=f"the stack has no {name!r} variable")
    steps = int(dataset.sizes.get("time", 1))
    if not 0 <= int(index) < steps:
        raise HTTPException(status_code=404, detail=f"time index {index} outside 0..{steps - 1}")
    position = band_index(dataset, band)
    variable = dataset[name]
    plane = variable.isel(time=int(index)) if "time" in variable.dims else variable
    if position is not None and "band" in plane.dims:
        plane = plane.isel(band=position)
    valid = None
    if "valid" in dataset:
        mask = dataset["valid"]
        if "time" in mask.dims:
            mask = mask.isel(time=int(index))
        if position is not None and "band" in mask.dims:
            mask = mask.isel(band=position)
        valid = np.asarray(mask.values, dtype=bool)
    return np.asarray(plane.values), valid


def _rgba_png(channels: list[np.ndarray], mask: np.ndarray | None) -> bytes:
    """Three stretched 8-bit planes plus a validity mask as one RGBA PNG."""
    import imageio.v3 as iio

    rows, cols = channels[0].shape
    rgba = np.zeros((rows, cols, 4), dtype=np.uint8)
    for position in range(3):
        rgba[..., position] = channels[min(position, len(channels) - 1)]
    rgba[..., 3] = 255 if mask is None else np.where(mask, 255, 0).astype(np.uint8)
    return bytes(iio.imwrite("<bytes>", rgba, extension=".png"))


def _png_response(dataset: xr.Dataset, payload: bytes, stride: int, shape: tuple[int, int]) -> Response:
    bounds = images.bounds_header(dataset["x_km"].values, dataset["y_km"].values, stride, shape)
    return Response(content=payload, media_type="image/png", headers=images.image_headers(shape, stride, bounds))
