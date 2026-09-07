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

import hashlib
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
from .io_guard import NetCDFRoute

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stacks", tags=["stacks"], route_class=NetCDFRoute)

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


def norm_default_of(dataset: xr.Dataset) -> str:
    """The normalisation this product is meant to be read with."""
    return data.strip_norm_default(dataset)


def norm_options_of(dataset: xr.Dataset) -> list[str]:
    """Which normalisations this product can answer for; see ``images``.

    A product whose default is ``none`` is offered none of them.  That is
    JIRAM, and it is not a shortcut: a thermal camera's night side is the
    measurement, so dividing it by the cosine of a solar angle would not be an
    option the viewer should put in front of anyone -- and the metadata would
    then have to carry a set of limits for a picture nobody should ask for.
    """
    if norm_default_of(dataset).lower() == "none":
        return ["none"]
    return images.norm_options("incidence" in dataset, "emission" in dataset)


def meta_norms(dataset: xr.Dataset) -> tuple[str, ...]:
    """The norms ``meta.stretch`` carries a set of limits for.

    Every option the product can answer for, at its default parameter, so
    that the viewer's illumination selector can re-initialise its sliders
    from the metadata it already has rather than guessing or asking again.
    That costs nothing extra: :func:`data.normalised_stretch` answers all of
    them from one strided read, and the whole set lands in the meta cache
    together.

    A product whose default is ``none`` is a JIRAM product, and it keeps the
    single unnormalised pair it has always sent -- reading a JIRAM strip's
    metadata must not start computing Minnaert corrections for an instrument
    that measures its own thermal emission.
    """
    default = norm_default_of(dataset)
    if default.lower() == "none":
        return ("none",)
    return tuple(norm_options_of(dataset))


def stretch_entry_key(label: str, band: str) -> str:
    """Where one ``(norm, band)`` pair lives in the meta cache file.

    The unnormalised entry keeps the bare band name it has always had -- an
    empty string for a band-less stack -- so a cache file written before the
    amendment is read rather than thrown away.
    """
    return band if label == "none" else f"{label}|{band}"


def stretch_of(
    mirror: str | Path | None,
    identifier: str,
    dataset: xr.Dataset,
    path: Path,
    band: str | None = None,
    norm: str = "none",
) -> dict[str, float]:
    """The 1st and 99th percentiles, computed once and cached on disk.

    The subsample is :func:`data.stack_stretch`'s: a few time steps,
    strided to a couple of million values.  Reading every pixel of every
    step would cost a gigabyte of I/O for a number the eye cannot tell
    apart from this one.

    A multi-band stack gets one stretch per band -- three colour strips of one
    camera differ in throughput by tens of per cent, so a shared stretch would
    tint the composite -- and they share one cache file keyed by band name.

    Every normalisation gets its own pair, under a key that carries its name:
    dividing by ``cos(i)`` moves the whole histogram, so the limits of the raw
    image are the wrong limits for the corrected one.  The band-less, unnormalised
    case keeps the bare band key it always had, so a cache file written before
    the amendment is still read rather than silently recomputed.
    """
    cache = meta_cache_path(mirror, identifier)
    try:
        stat = path.stat()
        signature = f"{stat.st_mtime_ns}:{stat.st_size}"
    except OSError:
        signature = ""
    if instrument_of(dataset).lower() == "junocam":
        from ..junocam.policy import policy_signature
        signature += ":" + str(policy_signature(mirror_root(mirror)))
        signature += ":" + str(dataset.attrs.get("withheld_or_superseded_steps", 0))
    name, parameter = images.parse_norm(norm)
    label = images.norm_label(name, parameter)
    band_key = "" if band is None else str(band).strip().upper()
    key = stretch_entry_key(label, band_key)
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
    if band_key:
        index = band_index(dataset, band_key)
        plane = dataset.isel(band=index) if index is not None else dataset
    if not band_key and name == "none":
        # The JIRAM path, untouched: every finite pixel of a few strided time
        # steps, no mask and no model.
        low, high = data.stack_stretch(plane, key=f"{identifier}::{key}::{signature}")
        found = {label: (low, high)}
    else:
        # One read of the band answers for every norm the metadata offers, so
        # a miss on one of them fills the rest of the cache file too.
        found = data.normalised_stretch(
            plane,
            key=f"{identifier}::{band_key}::{signature}",
            norms=tuple({label, *meta_norms(dataset)}),
        )
    stretch = {"p1": float(found[label][0]), "p99": float(found[label][1])}
    for other, (low, high) in found.items():
        entries[stretch_entry_key(other, band_key)] = {"p1": float(low), "p99": float(high)}
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
    """``{p1, p99}`` for a band-less stack, ``{norm: {band: {p1, p99}}}`` for a banded one.

    The extra level is the amendment's: a banded product is a JunoCam product,
    a JunoCam product is displayed normalised, and the limits of the raw image
    are not the limits of the normalised one.  A band-less stack is JIRAM and
    keeps the one pair it has always sent, so nothing that reads the old shape
    has to learn the new one to keep working.
    """
    names = band_names(dataset)
    if not names:
        return stretch_of(mirror, identifier, dataset, path)
    return {
        norm: {
            name: stretch_of(mirror, identifier, dataset, path, name, norm)
            for name in names
        }
        for norm in meta_norms(dataset)
    }


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
        except (OSError, ValueError, HTTPException) as exc:
            LOGGER.warning("cannot open %s: %s", path, exc)
            continue
        if not dataset.sizes.get("time", 0):
            continue
        movie = movie_path(path) if instrument_of(dataset).lower() != "junocam" else None
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
    band: str | None = None
    norm: str = "none"


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
    band: str | None = None
    norm: str = "none"


def research_movie_path(root: Path, path: Path, dataset: xr.Dataset,
                        band: str | None, norm: str) -> Path:
    """Policy/settings-specific movies cannot reveal an older unfiltered cube."""
    from ..science import source_ids
    signature = json.dumps({
        "source": str(path), "mtime_ns": path.stat().st_mtime_ns,
        "size": path.stat().st_size, "sources": source_ids(dataset),
        "band": band, "norm": norm, "policy": "failure-exclusion-v1",
    }, sort_keys=True)
    key = hashlib.sha256(signature.encode()).hexdigest()[:24]
    return data.gui_cache_dir(root) / "research" / f"movie_{key}.mp4"


def register_jobs(manager: Any, mirror: str | Path | None) -> None:
    """Bind the three slow operations to job kinds on ``manager``."""
    root = mirror_root(mirror)

    def movie(progress: Callable[..., None], *, stack: str, fps: float, pct: list[float], cmap: str,
              band: str | None = None, norm: str = "none") -> dict[str, Any]:
        from ..movie import write_movie
        from ..science import select_physical_band

        path = resolve(root, stack)
        progress(0.05, f"rendering {stack}")
        dataset = data.open_stack(path)
        target = research_movie_path(root, path, dataset, band, norm)
        dataset = select_physical_band(dataset, band)
        summary = write_movie(
            dataset, target, fps=float(fps), percentiles=(float(pct[0]), float(pct[1])), cmap=str(cmap),
            norm=norm,
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
        band: str | None = None,
        norm: str = "none",
    ) -> dict[str, Any]:
        from ..export_goflow import export_stack

        path = resolve(root, stack)
        dataset = data.open_stack(path)
        setting = f"_{cache_key(band or '')}_{cache_key(norm)}" if band or norm != "none" else ""
        identity = research_movie_path(root, path, dataset, band, norm).stem
        settings = hashlib.sha256(json.dumps([identity, dt_tol, min_frames, crop_to_valid]).encode()).hexdigest()[:16]
        destination = Path(out_dir) if out_dir else data.export_dir(root) / f"goflow_{cache_key(stack)}{setting}_{settings}"
        if out_dir and destination.exists() and any(destination.iterdir()):
            raise ValueError("choose an empty export directory; existing exports are preserved")
        progress(0.1, f"exporting to {destination}")
        options: dict[str, Any] = {"band": band, "norm": norm}
        if dt_tol is not None:
            options["dt_tol"] = float(dt_tol)
        if min_frames is not None:
            options["min_frames"] = int(min_frames)
        if crop_to_valid is not None:
            options["crop_to_valid"] = bool(crop_to_valid)
        manifest = export_stack(dataset, destination, source=path, **options)
        progress(1.0, "done")
        return {"out_dir": str(destination), "n_realizations": int(manifest.get("n_realizations", 0))}

    from .io_guard import serialized_io
    manager.register("movie", serialized_io(movie))
    manager.register("stack_build", serialized_io(build))
    manager.register("goflow_export", serialized_io(export))


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
        "norm_default": norm_default_of(dataset),
        "norm_options": norm_options_of(dataset),
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
    norm: str | None = None,
    stretch: str = Query(default="linear", pattern="^(linear|asinh)$"),
    max_px: int = Query(default=images.DEFAULT_MAX_PX, ge=16, le=8000),
) -> Response:
    """One time step of a multi-band stack as a colour composite.

    Each channel is stretched on its own limits, defaulting to that band's
    entry in ``meta.stretch`` for the requested normalisation: the three
    JunoCam colour strips differ in throughput by tens of per cent and share a
    stretch only at the cost of a tint that is the camera's, not the planet's.

    The normalisation is per band as well, because the bands are read a
    fraction of a second and a fraction of a degree apart and therefore see
    the terminator in slightly different places.
    """
    mirror = request.app.state.mirror
    path = resolve(mirror, identifier)
    dataset = data.open_stack(path)
    norm_name, parameter, label = resolve_norm(dataset, norm)
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
    for position, channel in enumerate(channels):
        plane, valid = _plane(dataset, "image", index, channel)
        if not planes:
            stride = images.stride_for(plane.shape, max_px)
        cut = (slice(None, None, stride), slice(None, None, stride))
        sun = _angle(dataset, "incidence", index, channel)
        view = _angle(dataset, "emission", index, channel)
        sub, keep = images.normalise_plane(
            np.asarray(plane)[cut],
            incidence=None if sun is None else sun[cut],
            emission=None if view is None else view[cut],
            valid=None if valid is None else np.asarray(valid, dtype=bool)[cut],
            norm=norm_name,
            parameter=parameter,
        )
        band_stretch = stretch_of(mirror, identifier, dataset, path, channel, label)
        low = band_stretch["p1"] if limits[position][0] is None else float(limits[position][0])
        high = band_stretch["p99"] if limits[position][1] is None else float(limits[position][1])
        grey, finite = images.stretch_to_uint8(sub, low, high, stretch)
        finite = finite & keep
        # Intersection, not union: a composite pixel is a colour only where
        # all three channels measured something.  The three colour strips
        # cross the terminator a second apart and therefore mask slightly
        # different fringes of it, and a union would paint that fringe in one
        # channel against two zeros -- a red or blue rim around the night
        # side that is an artefact of the mask, not of the planet.
        mask = finite if mask is None else (mask & finite)
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
    norm: str | None = None,
    stretch: str = Query(default="linear", pattern="^(linear|asinh)$"),
    max_px: int = Query(default=images.DEFAULT_MAX_PX, ge=16, le=8000),
) -> Response:
    mirror = request.app.state.mirror
    path = resolve(mirror, identifier)
    dataset = data.open_stack(path)
    name, parameter, label = resolve_norm(dataset, norm)
    # The plane first: it is what refuses a banded stack asked for without a
    # band, and computing a stretch over three colours before saying so would
    # be a slow way to answer 400.
    plane, valid = _plane(dataset, "image", index, band)
    limits = stretch_of(
        mirror, identifier, dataset, path, band if band_names(dataset) else None, label
    )
    payload, stride, shape = images.plane_png(
        plane,
        valid,
        vmin=limits["p1"] if vmin is None else vmin,
        vmax=limits["p99"] if vmax is None else vmax,
        max_px=max_px,
        mode=stretch,
        incidence=_angle(dataset, "incidence", index, band),
        emission=_angle(dataset, "emission", index, band),
        norm=name,
        parameter=parameter,
    )
    return _png_response(dataset, payload, stride, shape)


@router.get("/{identifier:path}/movie")
def get_movie(request: Request, identifier: str, band: str | None = None, norm: str = "none") -> Response:
    from ..science import normalise_frame, select_physical_band
    root = request.app.state.mirror
    path = resolve(root, identifier)
    dataset = data.open_stack(path)
    try:
        # An absent physical band on a multiband cube has no video meaning.
        selected = select_physical_band(dataset, band)
        if not selected.sizes.get("time", 0):
            raise ValueError("no eligible observations to render")
        normalise_frame(selected.isel(time=0, y=slice(0, 2), x=slice(0, 2)), norm)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    movie = research_movie_path(root, path, dataset, band, norm)
    if not movie.exists():
        # Historical JIRAM movies have no failed JunoCam images to bypass.
        movie = movie_path(path) if instrument_of(dataset).lower() != "junocam" and norm == "none" else None
    if movie is None:
        raise HTTPException(status_code=404, detail=f"no movie beside {identifier}")
    media = "video/mp4" if movie.suffix.lower() == ".mp4" else "image/gif"
    response = range_response(movie, request.headers.get("range"), media)
    if instrument_of(dataset).lower() == "junocam":
        response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/{identifier:path}/movie")
def post_movie(request: Request, identifier: str, body: MovieRequest) -> dict[str, str]:
    from ..science import normalise_frame, select_physical_band
    path = resolve(request.app.state.mirror, identifier)
    dataset = data.open_stack(path)
    try:
        selected = select_physical_band(dataset, body.band)
        if not selected.sizes.get("time", 0):
            raise ValueError("no eligible observations to render")
        normalise_frame(selected.isel(time=0, y=slice(0, 2), x=slice(0, 2)), body.norm)
        if body.fps is not None and (not np.isfinite(body.fps) or body.fps <= 0):
            raise ValueError("fps must be positive and finite")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    percentiles = body.pct if body.pct and len(body.pct) == 2 else [1.0, 99.0]
    record = request.app.state.jobs.submit(
        "movie",
        {
            "stack": identifier,
            "fps": 4.0 if body.fps is None else float(body.fps),
            "pct": [float(percentiles[0]), float(percentiles[1])],
            "cmap": body.cmap or "gray",
            "band": body.band,
            "norm": body.norm,
        },
    )
    return {"job_id": record["id"]}


@router.post("/{identifier:path}/export")
def post_export(request: Request, identifier: str, body: ExportRequest) -> dict[str, str]:
    from ..science import stack_readiness
    root = request.app.state.mirror
    path = resolve(root, identifier)
    ready = stack_readiness(data.open_stack(path), band=body.band, norm=body.norm,
                            dt_tol=0.05 if body.dt_tol is None else body.dt_tol,
                            min_frames=3 if body.min_frames is None else body.min_frames)
    if not ready["ready"]:
        raise HTTPException(status_code=400, detail={"message": "stack is not ready for export", "readiness": ready})
    if body.out_dir and not Path(body.out_dir).resolve().is_relative_to(data.export_dir(root).resolve()):
        raise HTTPException(status_code=400, detail="GUI exports must stay under the mirror exports directory")
    if body.out_dir and Path(body.out_dir).exists() and (not Path(body.out_dir).is_dir() or any(Path(body.out_dir).iterdir())):
        raise HTTPException(status_code=400, detail="choose an empty export directory; existing exports are preserved")
    record = request.app.state.jobs.submit(
        "goflow_export",
        {
            "stack": identifier,
            "out_dir": body.out_dir,
            "dt_tol": body.dt_tol,
            "min_frames": body.min_frames,
            "crop_to_valid": body.crop_to_valid,
            "band": body.band,
            "norm": body.norm,
        },
    )
    return {"job_id": record["id"]}


# ---------------------------------------------------------------------------
# helpers the routes share
# ---------------------------------------------------------------------------
def resolve_norm(dataset: xr.Dataset, norm: str | None) -> tuple[str, float, str]:
    """``(name, parameter, label)`` of a request's ``norm``, or a 400.

    An absent ``norm`` is the product's own default rather than ``none``: a
    JunoCam frame asked for with no opinion should arrive looking like a
    picture of Jupiter, and the viewer that does have an opinion says so.
    """
    text = norm_default_of(dataset) if norm is None or str(norm).strip() == "" else norm
    try:
        name, parameter = images.parse_norm(text)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return name, parameter, images.norm_label(name, parameter)


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


def _angle(
    dataset: xr.Dataset, name: str, index: int, band: str | None = None
) -> np.ndarray | None:
    """One time step of ``emission`` or ``incidence``, or ``None`` if absent.

    Absent is the ordinary case for a JIRAM stack and for any JunoCam stack
    built before the amendment, so it is answered with ``None`` rather than
    with a 404: the normalisation degrades to a no-op and the frame still
    arrives.
    """
    if name not in dataset:
        return None
    variable = dataset[name]
    plane = variable.isel(time=int(index)) if "time" in variable.dims else variable
    if "band" in plane.dims:
        position = band_index(dataset, band) if band is not None else None
        plane = plane.isel(band=position if position is not None else 0)
    return np.asarray(plane.values)


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
    headers = images.image_headers(shape, stride, bounds)
    if instrument_of(dataset).lower() == "junocam":
        headers["Cache-Control"] = "no-store"
    return Response(content=payload, media_type="image/png", headers=headers)
