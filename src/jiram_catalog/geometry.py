"""Vectorised SPICE geometry for JIRAM camera frames.

The engine reproduces, per pixel, what :func:`spiceypy.sincpt` and
:func:`spiceypy.illumf` return for a single ray, but without a Python loop
over the 55296 pixels of a 128x432 frame: the instrument-frame pixel
directions are rotated into ``IAU_JUPITER`` as one array, the ellipsoid
intercept is solved analytically, and the illumination angles follow from
the intercepts.

Stellar aberration is the one place where the vectorised path has to mirror
NAIF's bookkeeping exactly. A pixel ray is an *apparent* direction, and so is
the ``srfvec`` that :func:`spiceypy.sincpt` reports, so both are turned into
geometric directions with the same inverse correction before the ray is
intersected with the ellipsoid; correcting only one of the two leaves a
systematic offset of order ``|v|/c`` (about 7 arcsec, or 4 km on Jupiter's
surface at perijove range).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import spiceypy

__all__ = [
    "KernelSet",
    "FrameGeometry",
    "LM_HALF_ORDER",
    "aberrate",
    "band_geometry",
    "ellipsoid_intercept",
    "frame_geometry",
    "pixel_directions",
    "planetocentric",
    "surface_normal",
    "vector_angle_deg",
]

TARGET = "JUPITER"
TARGET_FRAME = "IAU_JUPITER"
OBSERVER = "JUNO"
SUN = "SUN"

#: Band letter -> (SPICE frame name, NAIF instrument id).
BANDS: dict[str, tuple[str, int]] = {
    "L": ("JUNO_JIRAM_I_LBAND", -61411),
    "M": ("JUNO_JIRAM_I_MBAND", -61412),
}

#: Full imager frame -> (SPICE frame name, NAIF instrument id). The 266-line
#: detector carries the L band on lines 1-128 (+X side) and the M band on
#: lines 139-266, ten unused gap lines between them.
IMAGER: tuple[str, int] = ("JUNO_JIRAM_I", -61410)

#: Storage order of the two 128-line halves inside a 256-line ("LM") product,
#: first stored row first. The bright half of an image tracks the M-band
#: footprint and the detector puts the L band on lines 1-128; the prescribed
#: vote itself came out just under its own threshold, so this value stands on
#: that evidence and was accepted by the lead on 2026-09-05 ("Lead decision"
#: in ``docs/reports/lm_half_order.md``).
LM_HALF_ORDER: tuple[str, str] = ("L", "M")

#: Static kernels required by every frame, in furnsh order.
REQUIRED_STATIC: tuple[tuple[str, str], ...] = (
    ("lsk", "naif0012.tls"),
    ("pck", "pck00010.tpc"),
    ("fk", "juno_v12.tf"),
    ("ik", "juno_jiram_v02.ti"),
)
SCLK_PATTERN = "JNO_SCLKSCET.*.tsc"
#: Static ephemerides loaded only when already mirrored.
OPTIONAL_STATIC_SPKS: tuple[str, ...] = ("de442s.bsp", "jup380s.bsp", "jup388s.bsp")
#: Prefixes of the per-orbit reconstructed/predicted trajectory SPKs.
SPK_PREFIXES: tuple[str, ...] = ("spk_rec_", "juno_rec_", "spk_pre_", "juno_pre_")

_EPOCH_RE = re.compile(
    r"^(?P<head>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2})(?:\.(?P<frac>\d+))?$"
)


# --------------------------------------------------------------------------
# epoch handling
# --------------------------------------------------------------------------
def normalise_epoch(epoch: str | datetime | np.datetime64 | pd.Timestamp) -> str:
    """Return ``YYYY-MM-DDTHH:MM:SS.ffffff`` for any accepted epoch input.

    Fractional seconds are neither rounded nor truncated: shorter strings are
    zero-padded and anything finer than a microsecond is rejected.
    """
    if isinstance(epoch, str):
        text = epoch.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1]
        match = _EPOCH_RE.match(text)
        if match is None:
            raise ValueError(f"bad epoch: {epoch!r}")
        fraction = match.group("frac") or ""
        if len(fraction) > 6:
            raise ValueError(f"epoch finer than a microsecond: {epoch!r}")
        head = match.group("head").replace(" ", "T")
        try:
            datetime.fromisoformat(head)
        except ValueError as exc:
            raise ValueError(f"bad epoch: {epoch!r}") from exc
        return f"{head}.{fraction.ljust(6, '0')}"

    if isinstance(epoch, (pd.Timestamp, np.datetime64, datetime)):
        stamp = pd.Timestamp(epoch)
    else:
        raise TypeError(f"unsupported epoch type: {type(epoch).__name__}")
    if stamp.tz is not None:
        stamp = stamp.tz_convert("UTC").tz_localize(None)
    if int(getattr(stamp, "nanosecond", 0)) != 0:
        raise ValueError(f"epoch finer than a microsecond: {epoch!r}")
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.%f")


# --------------------------------------------------------------------------
# small vector helpers (all pure numpy, no kernels needed)
# --------------------------------------------------------------------------
def _unit(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=-1, keepdims=True)
    return np.divide(vectors, norms, out=np.full_like(vectors, np.nan), where=norms > 0)


def vector_angle_deg(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Angle between two (stacks of) vectors, in degrees."""
    first = np.asarray(first, dtype=np.float64)
    second = np.asarray(second, dtype=np.float64)
    cross = np.linalg.norm(np.cross(first, second), axis=-1)
    dot = np.sum(first * second, axis=-1)
    return np.degrees(np.arctan2(cross, dot))


def planetocentric(spoint: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Planetocentric latitude and east longitude in degrees, longitude in [0, 360)."""
    spoint = np.asarray(spoint, dtype=np.float64)
    x, y, z = spoint[..., 0], spoint[..., 1], spoint[..., 2]
    lat = np.degrees(np.arctan2(z, np.hypot(x, y)))
    lon = np.degrees(np.arctan2(y, x)) % 360.0
    return lat, lon


def surface_normal(spoint: np.ndarray, radii: Sequence[float]) -> np.ndarray:
    """Outward unit normal of the triaxial ellipsoid at ``spoint``."""
    radii = np.asarray(radii, dtype=np.float64).reshape(3)
    return _unit(np.asarray(spoint, dtype=np.float64) / radii**2)


def aberrate(dirs: np.ndarray, v: Sequence[float], inverse: bool = False) -> np.ndarray:
    """Apply stellar aberration to ``dirs`` for observer velocity ``v`` (km/s).

    ``inverse=False`` reproduces :func:`spiceypy.stelab` (geometric ->
    apparent) and ``inverse=True`` reproduces :func:`spiceypy.stlabx`
    (apparent -> geometric), each to machine precision. Both rotate by
    ``asin(|u x v/c|)`` about ``u x v/c``, so the round trip is only exact to
    order ``(v/c)**2`` -- exactly as in NAIF's own pair.
    """
    directions = np.asarray(dirs, dtype=np.float64)
    velocity = np.asarray(v, dtype=np.float64).reshape(3)
    scaled = (-velocity if inverse else velocity) / spiceypy.clight()
    axis_raw = np.cross(_unit(directions), scaled)
    sine = np.linalg.norm(axis_raw, axis=-1, keepdims=True)
    axis = np.divide(axis_raw, sine, out=np.zeros_like(axis_raw), where=sine > 0)
    angle = np.arcsin(np.clip(sine, -1.0, 1.0))
    cosine = np.cos(angle)
    return (
        directions * cosine
        + np.cross(axis, directions) * np.sin(angle)
        + axis * np.sum(axis * directions, axis=-1, keepdims=True) * (1.0 - cosine)
    )


def ellipsoid_intercept(
    obspos: Sequence[float], dirs: np.ndarray, radii: Sequence[float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Intersect rays from ``obspos`` with the triaxial ellipsoid ``radii``.

    Returns ``(spoint, t, hit)``; ``spoint`` and ``t`` are NaN where the ray
    misses the ellipsoid or the intercept lies behind the observer.
    """
    origin = np.asarray(obspos, dtype=np.float64).reshape(3)
    directions = np.asarray(dirs, dtype=np.float64)
    semi_axes = np.asarray(radii, dtype=np.float64).reshape(3)

    scaled_origin = origin / semi_axes
    scaled_dirs = directions / semi_axes
    quad = np.sum(scaled_dirs * scaled_dirs, axis=-1)
    half_linear = np.sum(scaled_dirs * scaled_origin, axis=-1)
    constant = float(scaled_origin @ scaled_origin) - 1.0

    discriminant = half_linear**2 - quad * constant
    hit = discriminant >= 0.0
    root = np.sqrt(np.where(hit, discriminant, 0.0))
    with np.errstate(invalid="ignore", divide="ignore"):
        near = (-half_linear - root) / quad
        far = (-half_linear + root) / quad
    distance = np.where(near > 0.0, near, far)
    hit = hit & (distance > 0.0)
    distance = np.where(hit, distance, np.nan)
    spoint = origin + distance[..., None] * directions
    return spoint, distance, hit


def pixel_directions(lines: int, samples: int, ifov_rad: float) -> np.ndarray:
    """Unit pixel-centre directions in the band frame, indexed ``[line-1, sample-1]``.

    Pinhole model: ``x`` grows toward line 1 (+X, the spin direction) and ``y``
    toward sample 1 (+Y), so ``[0, 0]`` is the +X/+Y detector corner.
    """
    if lines < 1 or samples < 1:
        raise ValueError("lines and samples must be positive")
    line_index = np.arange(1, lines + 1, dtype=np.float64)
    sample_index = np.arange(1, samples + 1, dtype=np.float64)
    x = ifov_rad * (lines / 2.0 + 0.5 - line_index)
    y = ifov_rad * (samples / 2.0 + 0.5 - sample_index)
    dirs = np.empty((lines, samples, 3), dtype=np.float64)
    dirs[..., 0] = x[:, None]
    dirs[..., 1] = y[None, :]
    dirs[..., 2] = 1.0
    dirs /= np.linalg.norm(dirs, axis=-1, keepdims=True)
    return dirs


# --------------------------------------------------------------------------
# kernel bookkeeping
# --------------------------------------------------------------------------
def _sclk_rank(path: Path) -> tuple[int, str]:
    match = re.fullmatch(r"JNO_SCLKSCET\.(\d+)\.tsc", path.name)
    return (int(match.group(1)) if match else -1, path.name)


def classify_kernel(name: str) -> str | None:
    """Return ``"ck"``/``"spk"`` for a per-orbit kernel name, else ``None``."""
    if name.endswith(".bc"):
        return "ck"
    if name.endswith(".bsp") and name.startswith(SPK_PREFIXES):
        return "spk"
    return None


def spk_aliases(name: str) -> list[str]:
    """Names under which a reconstructed/predicted SPK may be mirrored."""
    aliases = [name]
    for prefix, other in (("spk_rec_", "juno_rec_"), ("spk_pre_", "juno_pre_")):
        if name.startswith(prefix):
            aliases.append(other + name[len(prefix) :])
        if name.startswith(other):
            aliases.append(prefix + name[len(other) :])
    return list(dict.fromkeys(aliases))


def orbit_kernel_names(frames: pd.DataFrame) -> dict[str, list[str]]:
    """Collect the CK and SPK names the archive used, from parsed rows only."""
    found: dict[str, set[str]] = {"ck": set(), "spk": set()}
    if "spice_kernels" not in frames.columns:
        return {"ck": [], "spk": []}
    rows = frames
    if "parse_ok" in frames.columns:
        rows = frames.loc[frames["parse_ok"].fillna(False).astype(bool)]
    for entry in rows["spice_kernels"].dropna():
        for name in str(entry).split(";"):
            name = name.strip()
            kind = classify_kernel(name) if name else None
            if kind is not None:
                found[kind].add(name)
    return {"ck": sorted(found["ck"]), "spk": sorted(found["spk"])}


class KernelSet:
    """A furnsh'd list of kernel paths that can be unloaded again."""

    def __init__(self, paths: Sequence[str | Path]) -> None:
        self.paths: list[Path] = [Path(path) for path in paths]
        self._loaded = False
        for path in self.paths:
            spiceypy.furnsh(str(path))
        self._loaded = True

    def unload(self) -> None:
        """Unload exactly the paths this set loaded, in reverse order."""
        if not self._loaded:
            return
        for path in reversed(self.paths):
            spiceypy.unload(str(path))
        self._loaded = False

    def __enter__(self) -> "KernelSet":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        self.unload()
        return False

    def __repr__(self) -> str:
        return f"KernelSet({len(self.paths)} kernels, loaded={self._loaded})"

    @classmethod
    def static_paths(cls, mirror: str | Path) -> list[Path]:
        """Mission-static kernels in furnsh order (optional SPKs only if present)."""
        spice = Path(mirror) / "spice"
        paths: list[Path] = []
        for kind, name in REQUIRED_STATIC:
            path = spice / kind / name
            if not path.is_file():
                raise FileNotFoundError(f"missing SPICE kernel: {path}")
            paths.append(path)
        clocks = sorted(spice.glob(f"sclk/{SCLK_PATTERN}"), key=_sclk_rank)
        if not clocks:
            raise FileNotFoundError(f"missing SPICE kernel: {spice / 'sclk' / SCLK_PATTERN}")
        paths.append(clocks[-1])
        for name in OPTIONAL_STATIC_SPKS:
            path = spice / "spk" / name
            if path.is_file() and path.stat().st_size > 0:
                paths.append(path)
        return paths

    @classmethod
    def for_orbits(
        cls,
        mirror: str | Path,
        orbits: Iterable[int],
        frames: pd.DataFrame | None = None,
    ) -> "KernelSet":
        """Load the static set plus every CK/SPK the selected orbits' labels name."""
        root = Path(mirror)
        selected = list(orbits)
        if frames is None:
            from .index import load_frames

            frames = load_frames(root, selected)
        names = orbit_kernel_names(frames)

        resolved: dict[str, list[Path]] = {"spk": [], "ck": []}
        missing: list[str] = []
        for kind in ("spk", "ck"):
            for name in names[kind]:
                candidates = spk_aliases(name) if kind == "spk" else [name]
                for candidate in candidates:
                    path = root / "spice" / kind / candidate
                    if path.is_file() and path.stat().st_size > 0:
                        resolved[kind].append(path)
                        break
                else:
                    missing.append(name)
        if missing:
            raise FileNotFoundError(
                f"missing SPICE kernels: {', '.join(sorted(missing))}; "
                f"run: jiram-catalog kernels --orbits ..."
            )
        return cls(cls.static_paths(root) + resolved["spk"] + resolved["ck"])


# --------------------------------------------------------------------------
# the engine
# --------------------------------------------------------------------------
@dataclass
class FrameGeometry:
    """Per-pixel and summary geometry of one JIRAM camera frame."""

    epoch: str
    et: float
    trgepc: float
    band: str
    frame: str
    ifov_rad: float
    lines: int
    samples: int
    lat: np.ndarray
    lon_east: np.ndarray
    range_km: np.ndarray
    emission: np.ndarray
    incidence: np.ndarray
    phase: np.ndarray
    on_planet: np.ndarray
    boresight: dict
    corners: np.ndarray
    sub_sc_lat: float
    sub_sc_lon_east: float
    sc_altitude_km: float
    obspos_km: np.ndarray
    sunpos_km: np.ndarray
    #: Band letters of the stacked halves, first stored row first ("LM" only).
    halves: tuple[str, ...] = ()
    #: 0-based ``[start, stop)`` row range of each half of ``halves``.
    half_rows: tuple[tuple[int, int], ...] = ()


def _probe_directions(dirs: np.ndarray) -> list[np.ndarray]:
    """Corner then edge-midpoint pixel directions, used when the boresight misses."""
    lines, samples = dirs.shape[0], dirs.shape[1]
    mid_line, mid_sample = lines // 2, samples // 2
    return [
        dirs[0, 0],
        dirs[0, -1],
        dirs[-1, 0],
        dirs[-1, -1],
        dirs[0, mid_sample],
        dirs[-1, mid_sample],
        dirs[mid_line, 0],
        dirs[mid_line, -1],
    ]


def _observer_reference(
    et: float, frame: str, dirs: np.ndarray, abcorr: str
) -> tuple[float, np.ndarray, np.ndarray, tuple[np.ndarray, np.ndarray] | None]:
    """Target epoch and the apparent observer-to-target geometry.

    Returns ``(trgepc, base_point, apparent_vec, boresight_hit)``; the observer
    sits at ``base_point`` minus the *geometric* counterpart of ``apparent_vec``
    (both in ``IAU_JUPITER`` at ``trgepc``).
    """
    boresight = np.array([0.0, 0.0, 1.0])
    probes = [boresight, *_probe_directions(dirs)]
    for index, probe in enumerate(probes):
        try:
            spoint, trgepc, srfvec = spiceypy.sincpt(
                "ELLIPSOID", TARGET, et, TARGET_FRAME, abcorr, OBSERVER, frame, probe
            )
        except spiceypy.utils.exceptions.NotFoundError:
            continue
        spoint = np.asarray(spoint, dtype=np.float64)
        srfvec = np.asarray(srfvec, dtype=np.float64)
        hit = (spoint, srfvec) if index == 0 else None
        return float(trgepc), spoint, srfvec, hit

    position, light_time = spiceypy.spkpos(TARGET, et, TARGET_FRAME, abcorr, OBSERVER)
    return (
        float(et - light_time),
        np.zeros(3),
        np.asarray(position, dtype=np.float64),
        None,
    )


def _to_geometric(
    vec_body: np.ndarray, velocity: np.ndarray | None, rot_j2000_to_body: np.ndarray
) -> np.ndarray:
    """Undo stellar aberration on an apparent body-frame direction."""
    if velocity is None:
        return vec_body
    in_j2000 = vec_body @ rot_j2000_to_body
    return aberrate(in_j2000, velocity, inverse=True) @ rot_j2000_to_body.T


def band_geometry(
    epoch: str | datetime | np.datetime64 | pd.Timestamp,
    band: str,
    kernels: KernelSet,
    *,
    lines: int = 128,
    samples: int = 432,
    abcorr: str = "LT+S",
) -> FrameGeometry:
    """Compute the full per-pixel geometry of one single-band JIRAM frame.

    SPICE state is global, so ``kernels`` serves only to assert that a kernel
    set is furnsh'd; the epoch is the label ``START_TIME`` exactly, never
    offset by part of the exposure.
    """
    if band not in BANDS:
        raise ValueError(f"band must be 'L' or 'M', not {band!r}")
    if spiceypy.ktotal("ALL") <= 0:
        raise RuntimeError(f"no SPICE kernels are loaded (kernels={kernels!r})")

    iso = normalise_epoch(epoch)
    et = float(spiceypy.str2et(iso))
    frame, ins_id = BANDS[band]
    ifov_rad = float(spiceypy.gdpool(f"INS{ins_id}_IFOV", 0, 2)[0])
    dirs = pixel_directions(lines, samples, ifov_rad)

    trgepc, base_point, apparent_vec, boresight_hit = _observer_reference(
        et, frame, dirs, abcorr
    )

    velocity: np.ndarray | None = None
    if "S" in abcorr.upper():
        state, _ = spiceypy.spkezr(
            OBSERVER, et, "J2000", "NONE", "SOLAR SYSTEM BARYCENTER"
        )
        velocity = np.asarray(state, dtype=np.float64)[3:6]
    body_epoch = et if abcorr.upper() == "NONE" else trgepc
    rot_j2000_to_body = np.asarray(spiceypy.pxform("J2000", TARGET_FRAME, body_epoch))

    # Both the pixel rays and the observer-to-target vector SPICE reports are
    # *apparent* directions; each is turned into its geometric counterpart with
    # the inverse stellar-aberration correction before they are combined.
    obspos = base_point - _to_geometric(apparent_vec, velocity, rot_j2000_to_body)
    rays = dirs @ np.asarray(spiceypy.pxform(frame, "J2000", et)).T
    if velocity is not None:
        rays = aberrate(rays, velocity, inverse=True)
    rays = rays @ rot_j2000_to_body.T

    radii = np.asarray(spiceypy.bodvrd(TARGET, "RADII", 3)[1], dtype=np.float64)
    spoint, _, on_planet = ellipsoid_intercept(obspos, rays, radii)

    lat, lon_east = planetocentric(spoint)
    to_observer = obspos - spoint
    range_km = np.linalg.norm(to_observer, axis=-1)
    normal = surface_normal(spoint, radii)
    sunpos = np.asarray(
        spiceypy.spkpos(SUN, trgepc, TARGET_FRAME, abcorr, TARGET)[0], dtype=np.float64
    )
    to_sun = sunpos - spoint
    emission = vector_angle_deg(normal, to_observer)
    incidence = vector_angle_deg(normal, to_sun)
    phase = vector_angle_deg(to_observer, to_sun)
    for array in (lat, lon_east, range_km, emission, incidence, phase):
        array[~on_planet] = np.nan

    if boresight_hit is not None:
        bore_spoint, bore_srfvec = boresight_hit
        bore_lat, bore_lon = planetocentric(bore_spoint)
        _, _, bore_phase, bore_inc, bore_em, *_ = spiceypy.illumf(
            "ELLIPSOID", TARGET, SUN, et, TARGET_FRAME, abcorr, OBSERVER, bore_spoint
        )
        boresight = {
            "lat": float(bore_lat),
            "lon_east": float(bore_lon),
            "range_km": float(np.linalg.norm(bore_srfvec)),
            "emission": float(np.degrees(bore_em)),
            "incidence": float(np.degrees(bore_inc)),
            "phase": float(np.degrees(bore_phase)),
        }
    else:
        boresight = dict.fromkeys(
            ("lat", "lon_east", "range_km", "emission", "incidence", "phase"), np.nan
        )

    corners = np.array(
        [
            [lat[0, 0], lon_east[0, 0]],
            [lat[0, -1], lon_east[0, -1]],
            [lat[-1, 0], lon_east[-1, 0]],
            [lat[-1, -1], lon_east[-1, -1]],
        ],
        dtype=np.float64,
    )

    sub_spoint, _, sub_srfvec = spiceypy.subpnt(
        "NEAR POINT/ELLIPSOID", TARGET, et, TARGET_FRAME, abcorr, OBSERVER
    )
    sub_lat, sub_lon = planetocentric(np.asarray(sub_spoint))

    return FrameGeometry(
        epoch=iso,
        et=et,
        trgepc=trgepc,
        band=band,
        frame=frame,
        ifov_rad=ifov_rad,
        lines=lines,
        samples=samples,
        lat=lat,
        lon_east=lon_east,
        range_km=range_km,
        emission=emission,
        incidence=incidence,
        phase=phase,
        on_planet=on_planet,
        boresight=boresight,
        corners=corners,
        sub_sc_lat=float(sub_lat),
        sub_sc_lon_east=float(sub_lon),
        sc_altitude_km=float(np.linalg.norm(sub_srfvec)),
        obspos_km=np.asarray(obspos, dtype=np.float64),
        sunpos_km=sunpos,
    )


# --------------------------------------------------------------------------
# stacked 256-line ("LM") products
# --------------------------------------------------------------------------
#: Keys of :attr:`FrameGeometry.boresight`.
BORESIGHT_KEYS: tuple[str, ...] = (
    "lat",
    "lon_east",
    "range_km",
    "emission",
    "incidence",
    "phase",
)
#: Per-pixel arrays stacked when the two halves are joined.
_PIXEL_ARRAYS: tuple[str, ...] = (
    "lat",
    "lon_east",
    "range_km",
    "emission",
    "incidence",
    "phase",
    "on_planet",
)


def _imager_ifov() -> float:
    """IFOV of the full imager (``INS-61410_IFOV``) in radians per pixel."""
    return float(spiceypy.gdpool(f"INS{IMAGER[1]}_IFOV", 0, 2)[0])


def _imager_boresight(et: float, abcorr: str) -> dict:
    """Surface intercept of the imager frame's +Z, all-NaN when it misses.

    Computed exactly as the band boresight is: ``sincpt`` for the +Z axis of
    ``JUNO_JIRAM_I`` and ``illumf`` at the intercept.
    """
    try:
        spoint, _, srfvec = spiceypy.sincpt(
            "ELLIPSOID",
            TARGET,
            et,
            TARGET_FRAME,
            abcorr,
            OBSERVER,
            IMAGER[0],
            [0.0, 0.0, 1.0],
        )
    except spiceypy.utils.exceptions.NotFoundError:
        return dict.fromkeys(BORESIGHT_KEYS, np.nan)
    lat, lon = planetocentric(np.asarray(spoint, dtype=np.float64))
    _, _, phase, incidence, emission, *_ = spiceypy.illumf(
        "ELLIPSOID", TARGET, SUN, et, TARGET_FRAME, abcorr, OBSERVER, spoint
    )
    return {
        "lat": float(lat),
        "lon_east": float(lon),
        "range_km": float(np.linalg.norm(np.asarray(srfvec, dtype=np.float64))),
        "emission": float(np.degrees(emission)),
        "incidence": float(np.degrees(incidence)),
        "phase": float(np.degrees(phase)),
    }


def _stacked_geometry(
    epoch: str | datetime | np.datetime64 | pd.Timestamp,
    kernels: KernelSet,
    *,
    lines: int,
    samples: int,
    abcorr: str,
) -> FrameGeometry:
    """Geometry of a 256-line dual-band product, halves in ``LM_HALF_ORDER``.

    The two halves are geometrically independent frames: each is computed for
    its own band frame and the results are stacked in storage order, which is
    the only thing the file layout adds.
    """
    parts = {
        band: band_geometry(
            epoch, band, kernels, lines=lines, samples=samples, abcorr=abcorr
        )
        for band in LM_HALF_ORDER
    }
    ordered = [parts[band] for band in LM_HALF_ORDER]
    first = ordered[0]
    stacked = {
        name: np.vstack([getattr(half, name) for half in ordered])
        for name in _PIXEL_ARRAYS
    }
    half_rows: list[tuple[int, int]] = []
    offset = 0
    for half in ordered:
        half_rows.append((offset, offset + int(half.lines)))
        offset += int(half.lines)

    lat, lon_east = stacked["lat"], stacked["lon_east"]
    corners = np.array(
        [
            [lat[0, 0], lon_east[0, 0]],
            [lat[0, -1], lon_east[0, -1]],
            [lat[-1, 0], lon_east[-1, 0]],
            [lat[-1, -1], lon_east[-1, -1]],
        ],
        dtype=np.float64,
    )
    return FrameGeometry(
        epoch=first.epoch,
        et=first.et,
        trgepc=first.trgepc,
        band="LM",
        frame=IMAGER[0],
        ifov_rad=_imager_ifov(),
        lines=offset,
        samples=int(first.samples),
        boresight=_imager_boresight(first.et, abcorr),
        corners=corners,
        sub_sc_lat=first.sub_sc_lat,
        sub_sc_lon_east=first.sub_sc_lon_east,
        sc_altitude_km=first.sc_altitude_km,
        obspos_km=first.obspos_km,
        sunpos_km=first.sunpos_km,
        halves=tuple(LM_HALF_ORDER),
        half_rows=tuple(half_rows),
        **stacked,
    )


def frame_geometry(
    epoch: str | datetime | np.datetime64 | pd.Timestamp,
    band: str,
    kernels: KernelSet,
    *,
    lines: int = 128,
    samples: int = 432,
    abcorr: str = "LT+S",
) -> FrameGeometry:
    """Per-pixel geometry of one JIRAM camera frame.

    ``band`` is ``"L"`` or ``"M"`` for a single-band frame, or ``"LM"`` for a
    256-line dual-band product, whose result is the vertical concatenation of
    the two band frames in :data:`LM_HALF_ORDER` order.
    """
    if band == "LM":
        return _stacked_geometry(
            epoch, kernels, lines=lines, samples=samples, abcorr=abcorr
        )
    return band_geometry(
        epoch, band, kernels, lines=lines, samples=samples, abcorr=abcorr
    )
