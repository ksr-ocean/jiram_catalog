"""Per-framelet SPICE geometry of a JunoCam image, with limb-fit timing.

This is the JIRAM engine of :mod:`jiram_catalog.geometry` applied to a camera
that works nothing like JIRAM.  A JIRAM frame is one epoch and one detector; a
JunoCam product is a *time series* -- ``n_frames`` readouts of up to four
filter strips, each 128 x 1648, all of one frame sharing an epoch and
differing only in where they sit on the focal plane.  So the unit of geometry
here is the framelet, the arrays are shaped ``(n_frames, n_bands, 128, 1648)``,
and the epoch of frame ``i`` follows the instrument kernel's rule::

    et_i = str2et(START_TIME) + START_TIME_BIAS
           + i * (INTERFRAME_DELAY + INTERFRAME_DELTA) + dt_refined

Everything else is the JIRAM recipe unchanged, and deliberately so: the
observer position and target epoch come from
:func:`jiram_catalog.geometry._observer_reference` with the ``JUNO_JUNOCAM``
boresight, the pixel rays get the same inverse stellar-aberration correction
before they are rotated into ``IAU_JUPITER`` at the *target* epoch, and the
intercept is the same analytic :func:`ellipsoid_intercept`.  Reusing those
functions rather than restating them is what makes the two engines answer the
same question the same way.

**What ``dt_refined`` absorbs.**  It is a single per-image offset fitted to the
observed limb, and it stands in for every constant timing term the label does
not carry.  The exposure midpoint is the largest of them in principle:
``START_TIME`` is the start of frame 0's integration, so a 64-stage methane
framelet integrating for 204.8 ms would want half of that, 102 ms, which is
about 31 pixel rows at Juno's spin rate.  In practice no methane product of
perijove 4 day 033 had enough limb in the field to be fitted, and the colour
products, whose exposures are 3.2 to 9.6 ms, fit offsets of a few
milliseconds -- the same order as the IK's own note about "a possible jitter
of order 20 msec".  Per-frame jitter is *not* absorbed: one offset is fitted
for the whole image, so jitter survives as scatter in the limb residual.

**Memory.**  A 42-frame three-band product carries 26.6 million samples, so the
six float32 angle arrays plus the mask come to about 660 MB.  The engine
therefore works one band of one frame at a time and writes float32 straight
into the output arrays; a caller must still not hold two of these at once.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import spiceypy

from ..geometry import (
    KernelSet,
    _observer_reference,
    _to_geometric,
    aberrate,
    ellipsoid_intercept,
    normalise_epoch,
    planetocentric,
    surface_normal,
    vector_angle_deg,
)
from . import limb as limbmod
from .camera import (
    CAMERA_FRAME,
    FRAMELET_LINES,
    FRAMELET_SAMPLES,
    BandCamera,
    band_cameras,
    frame_epochs,
    load_junocam_ik,
    photoactive_mask,
)
from .images import band_names, read_image

__all__ = [
    "DEFAULT_ABCORR",
    "FrameContext",
    "ImageGeometry",
    "cache_path",
    "frame_context",
    "image_geometry",
    "load_junocam_ik",
    "read_cache",
    "write_cache",
]

TARGET = "JUPITER"
TARGET_FRAME = "IAU_JUPITER"
OBSERVER = "JUNO"
SUN = "SUN"
DEFAULT_ABCORR = "LT+S"

#: Version stamp of the cached model; bump it when the recipe changes so that
#: a stale ``.nc`` is ignored rather than trusted.
CACHE_VERSION = 1


# --------------------------------------------------------------------------
# per-frame SPICE context
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class FrameContext:
    """Everything one frame's epoch fixes, before any pixel is touched."""

    et: float
    trgepc: float
    obspos_km: np.ndarray
    sunpos_km: np.ndarray
    rot_j2000_to_body: np.ndarray
    rot_cam_to_j2000: np.ndarray
    velocity_j2000: np.ndarray | None
    radii_km: np.ndarray


def probe_grid(cameras: tuple[BandCamera, ...]) -> np.ndarray:
    """A 3x3 grid of directions spanning every commanded strip.

    :func:`jiram_catalog.geometry._observer_reference` tries the frame's +Z
    first and then the corners and edge midpoints of the array it is handed,
    so the array only has to *contain* a spread of the field of view at the
    eight positions it reads.  Bands are ordered top to bottom on the focal
    plane, which is decreasing ``cy``.
    """
    ordered = sorted(cameras, key=lambda camera: -camera.cy)
    top, middle, bottom = ordered[0], ordered[len(ordered) // 2], ordered[-1]
    x = np.array([23.0, 826.5, float(FRAMELET_SAMPLES - 18)])
    grid = np.empty((3, 3, 3), dtype=np.float64)
    for index, (camera, y) in enumerate(
        ((top, 0.0), (middle, 0.5 * (FRAMELET_LINES - 1)), (bottom, FRAMELET_LINES - 1.0))
    ):
        grid[index] = camera.pixel_to_vector(x, np.full(3, y))
    return grid


def frame_context(
    et: float, probes: np.ndarray, *, abcorr: str = DEFAULT_ABCORR
) -> FrameContext:
    """The LT+S observer/target reference of one frame, JIRAM's recipe verbatim."""
    trgepc, base_point, apparent_vec, _ = _observer_reference(
        float(et), CAMERA_FRAME, probes, abcorr
    )
    velocity = None
    if "S" in abcorr.upper():
        state, _ = spiceypy.spkezr(OBSERVER, et, "J2000", "NONE", "SOLAR SYSTEM BARYCENTER")
        velocity = np.asarray(state, dtype=np.float64)[3:6]
    body_epoch = et if abcorr.upper() == "NONE" else trgepc
    rot_j2000_to_body = np.asarray(spiceypy.pxform("J2000", TARGET_FRAME, body_epoch))
    obspos = base_point - _to_geometric(apparent_vec, velocity, rot_j2000_to_body)
    sunpos = np.asarray(
        spiceypy.spkpos(SUN, trgepc, TARGET_FRAME, abcorr, TARGET)[0], dtype=np.float64
    )
    return FrameContext(
        et=float(et),
        trgepc=float(trgepc),
        obspos_km=np.asarray(obspos, dtype=np.float64),
        sunpos_km=sunpos,
        rot_j2000_to_body=rot_j2000_to_body,
        rot_cam_to_j2000=np.asarray(spiceypy.pxform(CAMERA_FRAME, "J2000", et)),
        velocity_j2000=velocity,
        radii_km=np.asarray(spiceypy.bodvrd(TARGET, "RADII", 3)[1], dtype=np.float64),
    )


def body_rays(dirs: np.ndarray, context: FrameContext) -> np.ndarray:
    """Camera-frame directions -> geometric directions in ``IAU_JUPITER``."""
    rays = np.asarray(dirs, dtype=np.float64) @ context.rot_cam_to_j2000.T
    if context.velocity_j2000 is not None:
        rays = aberrate(rays, context.velocity_j2000, inverse=True)
    return rays @ context.rot_j2000_to_body.T


def intercept_discriminant(rays: np.ndarray, context: FrameContext) -> np.ndarray:
    """Discriminant of the ray-ellipsoid quadratic: non-negative where a ray hits.

    The same quantity :func:`ellipsoid_intercept` tests, kept as a number
    rather than a flag so the limb can be located between two rows instead of
    only at one of them.  Juno is always outside Jupiter here, so both roots
    share the sign of ``-half_linear`` and a non-negative discriminant on a
    forward-looking ray is a real, in-front intercept.
    """
    semi_axes = context.radii_km
    scaled_origin = context.obspos_km / semi_axes
    scaled_dirs = np.asarray(rays, dtype=np.float64) / semi_axes
    quad = np.sum(scaled_dirs * scaled_dirs, axis=-1)
    half_linear = np.sum(scaled_dirs * scaled_origin, axis=-1)
    constant = float(scaled_origin @ scaled_origin) - 1.0
    return half_linear**2 - quad * constant


def _angles(
    rays: np.ndarray, context: FrameContext
) -> tuple[np.ndarray, ...]:
    """Intercept every ray and derive the per-pixel angles of one framelet."""
    radii = context.radii_km
    spoint, _, on_planet = ellipsoid_intercept(context.obspos_km, rays, radii)
    lat, lon_east = planetocentric(spoint)
    to_observer = context.obspos_km - spoint
    range_km = np.linalg.norm(to_observer, axis=-1)
    normal = surface_normal(spoint, radii)
    to_sun = context.sunpos_km - spoint
    emission = vector_angle_deg(normal, to_observer)
    incidence = vector_angle_deg(normal, to_sun)
    phase = vector_angle_deg(to_observer, to_sun)
    for array in (lat, lon_east, range_km, emission, incidence, phase):
        array[~on_planet] = np.nan
    return lat, lon_east, range_km, emission, incidence, phase, on_planet


# --------------------------------------------------------------------------
# the image-level result
# --------------------------------------------------------------------------
@dataclass
class ImageGeometry:
    """Per-pixel geometry of one JunoCam product, framelet by framelet."""

    product_id: str
    bands: tuple[str, ...]
    et: np.ndarray
    trgepc: np.ndarray
    #: The fitted offset, NaN when the image had too little limb to fit.
    dt_refined_s: float
    #: The offset actually folded into ``et`` (0 where the fit was refused).
    dt_applied_s: float
    obspos_km: np.ndarray
    sunpos_km: np.ndarray
    rot_j2000_to_body: np.ndarray
    rot_cam_to_j2000: np.ndarray
    velocity_j2000: np.ndarray
    radii_km: np.ndarray
    lat: np.ndarray
    lon_east: np.ndarray
    range_km: np.ndarray
    emission: np.ndarray
    incidence: np.ndarray
    phase: np.ndarray
    on_planet: np.ndarray
    abcorr: str = DEFAULT_ABCORR
    limb: dict = field(default_factory=dict)
    seconds: float = 0.0

    @property
    def n_frames(self) -> int:
        return int(self.lat.shape[0])

    @property
    def n_bands(self) -> int:
        return int(self.lat.shape[1])

    def cameras(self) -> tuple[BandCamera, ...]:
        return band_cameras(self.bands)

    def frame_context(self, index: int) -> FrameContext:
        """Rebuild frame ``index``'s context from the stored transforms."""
        return FrameContext(
            et=float(self.et[index]),
            trgepc=float(self.trgepc[index]),
            obspos_km=self.obspos_km[index],
            sunpos_km=self.sunpos_km[index],
            rot_j2000_to_body=self.rot_j2000_to_body[index],
            rot_cam_to_j2000=self.rot_cam_to_j2000[index],
            velocity_j2000=self.velocity_j2000[index],
            radii_km=self.radii_km,
        )


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------
def cache_path(product_id: str, mirror: str | Path | None = None) -> Path:
    """``<mirror>/junocam/geometry_cache/<product_id>.nc``."""
    if mirror is None:
        from ..config import mirror_root

        mirror = mirror_root()
    return Path(mirror) / "junocam" / "geometry_cache" / f"{product_id}.nc"


def _signature(row, bands: tuple[str, ...], abcorr: str) -> str:
    """What a cached fit is only valid for."""
    return json.dumps(
        {
            "version": CACHE_VERSION,
            "product_id": str(row["product_id"]),
            "bands": list(bands),
            "n_frames": int(row["n_framelets"]),
            "start_time": normalise_epoch(row["start_time"]),
            "interframe_delay_s": float(row["interframe_delay_s"]),
            "abcorr": abcorr,
        },
        sort_keys=True,
    )


def write_cache(geo: ImageGeometry, row, mirror: str | Path | None = None) -> Path:
    """Store the per-frame transforms and the limb summary next to the mirror."""
    import xarray as xr

    path = cache_path(geo.product_id, mirror)
    path.parent.mkdir(parents=True, exist_ok=True)
    dataset = xr.Dataset(
        {
            "et": ("frame", geo.et),
            "trgepc": ("frame", geo.trgepc),
            "obspos_km": (("frame", "xyz"), geo.obspos_km),
            "sunpos_km": (("frame", "xyz"), geo.sunpos_km),
            "velocity_j2000": (("frame", "xyz"), geo.velocity_j2000),
            "rot_j2000_to_body": (("frame", "row", "col"), geo.rot_j2000_to_body),
            "rot_cam_to_j2000": (("frame", "row", "col"), geo.rot_cam_to_j2000),
            "radii_km": ("xyz", geo.radii_km),
        },
        attrs={
            "signature": _signature(row, geo.bands, geo.abcorr),
            "product_id": geo.product_id,
            "bands": ";".join(geo.bands),
            "abcorr": geo.abcorr,
            "dt_refined_s": float(geo.dt_refined_s),
            "limb": json.dumps(geo.limb, sort_keys=True),
        },
    )
    temporary = path.with_suffix(".nc.tmp")
    dataset.to_netcdf(temporary)
    dataset.close()
    temporary.replace(path)
    return path


def read_cache(row, bands: tuple[str, ...], mirror=None, abcorr: str = DEFAULT_ABCORR) -> dict | None:
    """The cached ``dt_refined_s`` and limb summary, or ``None`` if unusable."""
    path = cache_path(str(row["product_id"]), mirror)
    if not path.is_file():
        return None
    try:
        import xarray as xr

        with xr.open_dataset(path) as dataset:
            attrs = dict(dataset.attrs)
    except Exception:
        return None
    if attrs.get("signature") != _signature(row, bands, abcorr):
        return None
    try:
        summary = json.loads(attrs.get("limb", "{}"))
    except json.JSONDecodeError:
        return None
    summary["dt_refined_s"] = float(attrs.get("dt_refined_s", np.nan))
    return summary


# --------------------------------------------------------------------------
# limb refinement driver
# --------------------------------------------------------------------------
class _LimbProblem:
    """The limb fit of one image: which framelets, which columns, which points.

    Candidate framelets are the ones whose predicted on-planet mask has an
    edge inside it at the nominal timing; at most
    :data:`limb.MAX_LIMB_FRAMELETS` of them, spread evenly through the image
    so that the fit samples the whole readout rather than its first second.
    """

    def __init__(
        self,
        row,
        image: np.ndarray,
        cameras: tuple[BandCamera, ...],
        start_et: float,
        interframe_delay_s: float,
        *,
        abcorr: str = DEFAULT_ABCORR,
    ) -> None:
        self.cameras = cameras
        self.start_et = float(start_et)
        self.interframe_delay_s = float(interframe_delay_s)
        self.abcorr = abcorr
        self.n_frames = int(row["n_framelets"])
        self.probes = probe_grid(cameras)
        self.columns = limbmod.limb_columns(photoactive_mask(FRAMELET_SAMPLES))
        rows = np.arange(FRAMELET_LINES, dtype=np.float64)
        grid_x, grid_y = np.meshgrid(self.columns.astype(np.float64), rows, indexing="xy")
        self.dirs = np.stack(
            [camera.pixel_to_vector(grid_x, grid_y) for camera in cameras], axis=0
        )
        self.image = image
        self._cache: dict[float, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self.framelets: np.ndarray = np.empty((0, 2), dtype=np.intp)
        self.point_framelet = np.empty(0, dtype=np.intp)
        self.point_column = np.empty(0, dtype=np.intp)
        self.point_side = np.empty(0, dtype=np.intp)
        self.strips = np.empty((0, FRAMELET_LINES, 0))

    # -- geometry at a trial offset ---------------------------------------
    def _contexts(self, dt: float) -> list[FrameContext]:
        epochs = frame_epochs(
            self.start_et,
            self.n_frames,
            self.interframe_delay_s,
            band=self.cameras[0].band,
            dt=dt,
        )
        return [frame_context(et, self.probes, abcorr=self.abcorr) for et in epochs]

    def _edges_all(self, dt: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Top/bottom edge rows and the raw discriminant of *every* framelet."""
        contexts = self._contexts(dt)
        discs = np.empty(
            (self.n_frames, len(self.cameras), FRAMELET_LINES, self.columns.size)
        )
        for frame, context in enumerate(contexts):
            for band in range(len(self.cameras)):
                discs[frame, band] = intercept_discriminant(
                    body_rays(self.dirs[band], context), context
                )
        flat = discs.reshape(-1, FRAMELET_LINES, self.columns.size)
        top, bottom = limbmod.edge_rows(flat)
        return top, bottom, flat

    def _edges(self, dt: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Top/bottom edge rows and the on-planet mask of the selected framelets."""
        key = round(float(dt), 12)
        hit = self._cache.get(key)
        if hit is not None:
            return hit
        discs = np.empty((len(self.framelets), FRAMELET_LINES, self.columns.size))
        contexts: dict[int, FrameContext] = {}
        epochs = frame_epochs(
            self.start_et,
            self.n_frames,
            self.interframe_delay_s,
            band=self.cameras[0].band,
            dt=dt,
        )
        for index, (frame, band) in enumerate(self.framelets):
            context = contexts.get(int(frame))
            if context is None:
                context = frame_context(epochs[int(frame)], self.probes, abcorr=self.abcorr)
                contexts[int(frame)] = context
            discs[index] = intercept_discriminant(
                body_rays(self.dirs[int(band)], context), context
            )
        top, bottom = limbmod.edge_rows(discs)
        result = (top, bottom, discs >= 0.0)
        if len(self._cache) > 4:
            self._cache.clear()
        self._cache[key] = result
        return result

    # -- selection ---------------------------------------------------------
    def select(self) -> int:
        """Choose the limb framelets and the candidate points; return their count."""
        top, bottom, _ = self._edges_all(0.0)
        usable_top = np.isfinite(top) & (top >= limbmod.SPACE_ROWS) & (
            top <= FRAMELET_LINES - 1 - limbmod.PLANET_ROWS
        )
        usable_bottom = np.isfinite(bottom) & (bottom <= FRAMELET_LINES - 1 - limbmod.SPACE_ROWS) & (
            bottom >= limbmod.PLANET_ROWS
        )
        counts = usable_top.sum(axis=-1) + usable_bottom.sum(axis=-1)
        candidates = np.flatnonzero(counts > 0)
        if candidates.size == 0:
            return 0
        if candidates.size > limbmod.MAX_LIMB_FRAMELETS:
            take = np.linspace(0, candidates.size - 1, limbmod.MAX_LIMB_FRAMELETS)
            candidates = candidates[np.unique(np.round(take).astype(np.intp))]
        n_bands = len(self.cameras)
        self.framelets = np.stack([candidates // n_bands, candidates % n_bands], axis=1)
        self.strips = np.ascontiguousarray(
            self.image[self.framelets[:, 0], self.framelets[:, 1]][:, :, self.columns],
            dtype=np.float64,
        )

        top, bottom, _ = self._edges(0.0)
        incidence = self._incidence(0.0)
        point_framelet: list[int] = []
        point_column: list[int] = []
        point_side: list[int] = []
        for index in range(self.framelets.shape[0]):
            for side, rows_edge in ((0, top[index]), (1, bottom[index])):
                if side == 0:
                    good = np.isfinite(rows_edge) & (rows_edge >= limbmod.SPACE_ROWS)
                    good &= rows_edge <= FRAMELET_LINES - 1 - limbmod.PLANET_ROWS
                    inner = np.clip(np.ceil(np.nan_to_num(rows_edge)), 0, FRAMELET_LINES - 1)
                else:
                    good = np.isfinite(rows_edge) & (
                        rows_edge <= FRAMELET_LINES - 1 - limbmod.SPACE_ROWS
                    )
                    good &= rows_edge >= limbmod.PLANET_ROWS
                    inner = np.clip(np.floor(np.nan_to_num(rows_edge)), 0, FRAMELET_LINES - 1)
                sunlit = (
                    incidence[index, inner.astype(np.intp), np.arange(self.columns.size)]
                    < limbmod.DAYSIDE_MAX_INCIDENCE_DEG
                )
                keep = np.flatnonzero(good & sunlit)
                point_framelet.extend([index] * keep.size)
                point_column.extend(keep.tolist())
                point_side.extend([side] * keep.size)
        self.point_framelet = np.asarray(point_framelet, dtype=np.intp)
        self.point_column = np.asarray(point_column, dtype=np.intp)
        self.point_side = np.asarray(point_side, dtype=np.intp)
        return int(self.point_framelet.size)

    def _incidence(self, dt: float) -> np.ndarray:
        """Solar incidence on the selected framelets' subsampled columns."""
        epochs = frame_epochs(
            self.start_et,
            self.n_frames,
            self.interframe_delay_s,
            band=self.cameras[0].band,
            dt=dt,
        )
        out = np.full((self.framelets.shape[0], FRAMELET_LINES, self.columns.size), np.nan)
        contexts: dict[int, FrameContext] = {}
        for index, (frame, band) in enumerate(self.framelets):
            context = contexts.get(int(frame))
            if context is None:
                context = frame_context(epochs[int(frame)], self.probes, abcorr=self.abcorr)
                contexts[int(frame)] = context
            rays = body_rays(self.dirs[int(band)], context)
            out[index] = _angles(rays, context)[4]
        return out

    # -- the two callables the fit needs -----------------------------------
    def predict(self, dt: float) -> np.ndarray:
        top, bottom, _ = self._edges(float(dt))
        rows = np.where(
            self.point_side == 0,
            top[self.point_framelet, self.point_column],
            bottom[self.point_framelet, self.point_column],
        )
        return rows

    def detect(self, dt: float) -> np.ndarray:
        _, _, inside = self._edges(float(dt))
        return limbmod.detect_observed_rows(
            self.strips,
            inside,
            self.point_framelet,
            self.point_column,
            self.point_side,
            self.predict(float(dt)),
        )

    def fit(self) -> limbmod.LimbFit:
        count = self.select()
        if count < limbmod.MIN_LIMB_POINTS:
            return limbmod.LimbFit(
                dt_refined_s=float("nan"),
                limb_residual_px_before=float("nan"),
                limb_residual_px_after=float("nan"),
                n_limb_points=count,
                refined=False,
                n_limb_framelets=int(self.framelets.shape[0]),
                reason=f"only {count} candidate limb points",
            )
        result = limbmod.refine_dt(self.predict, self.detect)
        result.n_limb_framelets = int(self.framelets.shape[0])
        return result


# --------------------------------------------------------------------------
# the entry point
# --------------------------------------------------------------------------
def image_geometry(
    row,
    kernels: KernelSet,
    *,
    mirror: str | Path | None = None,
    refine: bool = True,
    image: np.ndarray | None = None,
    dt: float | None = None,
    abcorr: str = DEFAULT_ABCORR,
    cache: bool = True,
) -> ImageGeometry:
    """Full per-framelet geometry of one JunoCam product.

    ``row`` is one row of ``junocam_images.parquet``.  ``kernels`` only asserts
    that a kernel set is furnsh'd -- SPICE state is global -- but the JunoCam
    IK is loaded here because :meth:`KernelSet.static_paths` loads the JIRAM
    one instead.  With ``refine=True`` the start time is fitted to the observed
    limb, reading the image if one is not supplied and reusing a cached fit
    when the cache matches the row; ``dt`` overrides both.
    """
    if spiceypy.ktotal("ALL") <= 0:
        raise RuntimeError(f"no SPICE kernels are loaded (kernels={kernels!r})")
    load_junocam_ik(mirror)

    started = time.perf_counter()
    bands = band_names(row)
    cameras = band_cameras(bands)
    n_frames = int(row["n_framelets"])
    n_bands = len(bands)
    if n_frames < 1:
        raise ValueError(f"{row['product_id']}: no framelets in the index row")
    start_et = float(spiceypy.str2et(normalise_epoch(row["start_time"])))
    interframe = float(row["interframe_delay_s"])
    probes = probe_grid(cameras)

    summary: dict[str, Any] = {}
    fitted = False
    if dt is not None:
        offset = float(dt)
    elif not refine:
        offset = 0.0
    else:
        cached = read_cache(row, bands, mirror, abcorr) if cache else None
        if cached is not None:
            offset = float(cached.get("dt_refined_s", np.nan))
            summary = cached
            if not np.isfinite(offset):
                offset = 0.0
        else:
            data = read_image(row, mirror) if image is None else image
            problem = _LimbProblem(
                row, data, cameras, start_et, interframe, abcorr=abcorr
            )
            result = problem.fit()
            summary = result.summary()
            offset = float(result.dt_refined_s) if result.refined else 0.0
            fitted = True
            del problem
            if image is None:
                del data

    epochs = frame_epochs(start_et, n_frames, interframe, band=bands[0], dt=offset)
    shape = (n_frames, n_bands, FRAMELET_LINES, FRAMELET_SAMPLES)
    lat = np.empty(shape, dtype=np.float32)
    lon_east = np.empty(shape, dtype=np.float32)
    range_km = np.empty(shape, dtype=np.float32)
    emission = np.empty(shape, dtype=np.float32)
    incidence = np.empty(shape, dtype=np.float32)
    phase = np.empty(shape, dtype=np.float32)
    on_planet = np.empty(shape, dtype=bool)

    trgepc = np.empty(n_frames)
    obspos = np.empty((n_frames, 3))
    sunpos = np.empty((n_frames, 3))
    velocity = np.empty((n_frames, 3))
    rot_body = np.empty((n_frames, 3, 3))
    rot_cam = np.empty((n_frames, 3, 3))
    radii = np.asarray(spiceypy.bodvrd(TARGET, "RADII", 3)[1], dtype=np.float64)

    directions = [camera.pixel_directions() for camera in cameras]
    for frame, et in enumerate(epochs):
        context = frame_context(et, probes, abcorr=abcorr)
        trgepc[frame] = context.trgepc
        obspos[frame] = context.obspos_km
        sunpos[frame] = context.sunpos_km
        velocity[frame] = (
            np.zeros(3) if context.velocity_j2000 is None else context.velocity_j2000
        )
        rot_body[frame] = context.rot_j2000_to_body
        rot_cam[frame] = context.rot_cam_to_j2000
        for band in range(n_bands):
            values = _angles(body_rays(directions[band], context), context)
            lat[frame, band] = values[0]
            lon_east[frame, band] = values[1]
            range_km[frame, band] = values[2]
            emission[frame, band] = values[3]
            incidence[frame, band] = values[4]
            phase[frame, band] = values[5]
            on_planet[frame, band] = values[6]

    geo = ImageGeometry(
        product_id=str(row["product_id"]),
        bands=bands,
        et=epochs,
        trgepc=trgepc,
        dt_refined_s=float(summary["dt_refined_s"]) if summary else float(offset),
        dt_applied_s=float(offset),
        obspos_km=obspos,
        sunpos_km=sunpos,
        rot_j2000_to_body=rot_body,
        rot_cam_to_j2000=rot_cam,
        velocity_j2000=velocity,
        radii_km=radii,
        lat=lat,
        lon_east=lon_east,
        range_km=range_km,
        emission=emission,
        incidence=incidence,
        phase=phase,
        on_planet=on_planet,
        abcorr=abcorr,
        limb=summary,
        seconds=time.perf_counter() - started,
    )
    if fitted and cache:
        try:
            write_cache(geo, row, mirror)
        except OSError:  # a read-only mirror must not fail the geometry
            pass
    return geo
