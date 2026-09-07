"""The JunoCam camera model, read out of the instrument kernel.

JunoCam is a pushframe camera: a single CCD carries four filter strips, each
128 detector lines tall and 1648 samples wide, and the spacecraft's 2 RPM spin
sweeps the scene across them.  One "frame" is a simultaneous readout of every
commanded strip; an image is a stack of frames, so a product is a time series
of 128-line framelets rather than one exposure.

Everything numeric here comes from ``juno_junocam_v03.ti`` through
:func:`spiceypy.gdpool` -- focal length, pixel size, the per-band distortion
centre ``(cx, cy)``, ``k1``/``k2``, the start-time bias and the interframe
delta.  Nothing is hard-coded but the detector's own shape (128 x 1648 and the
23 dark columns), which the kernel states in prose rather than in keywords.

Two coordinate conventions meet in this module and it is worth being explicit
about which is which.  The IK's recipe works in *framelet coordinates* whose
origin is the upper-left **corner** of the strip, so the centre of the first
pixel is at (0.5, 0.5) and the band boresights are documented as landing on
"pixel (827.0, 64.0)".  Array indices, by contrast, count pixel *centres* from
zero.  Every public function here takes and returns 0-based centre indices and
converts internally, exactly as the milestone spec prescribes::

    cam = undistort(x + 0.5 - cx, y + 0.5 - cy);  v = (cam_x, cam_y, fl)
    alpha = v_z / fl;  cam = distort(v_x / alpha, v_y / alpha)
    x = cam_x + cx - 0.5;  y = cam_y + cy - 0.5

The distortion centre ``cy`` is what places a band on the focal plane: the four
strips share ``cx`` and differ only in ``cy`` (METHANE 315.48, BLUE 158.48,
GREEN 3.48, RED -151.52), so a pixel in row ``y`` of the RED strip sits at
``y + 151.52`` in the focal-plane ordinate -- 155 pixel-widths away from the
same row of GREEN.  That is what makes band-to-band registration after
reprojection a real test of the timing model: the three strips see one ground
point at three different frames, and only a correct epoch per frame brings them
back on top of each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import spiceypy

__all__ = [
    "BAND_IDS",
    "CAMERA_FRAME",
    "CAMERA_ID",
    "DARK_COLUMNS",
    "FRAMELET_LINES",
    "FRAMELET_SAMPLES",
    "PHOTOACTIVE_COLUMNS",
    "BandCamera",
    "band_cameras",
    "frame_epochs",
    "ik_path",
    "load_junocam_ik",
    "photoactive_mask",
    "timing_parameters",
]

#: The instrument frame every band shares; the FK fixes it to the bus, so the
#: only thing that moves it is the spacecraft attitude in the CK.
CAMERA_FRAME = "JUNO_JUNOCAM"
#: NAIF id of the whole camera (its boresight is exactly the frame's +Z).
CAMERA_ID = -61500
#: ``FILTER_NAME`` value -> NAIF instrument id of that band.
BAND_IDS: dict[str, int] = {
    "BLUE": -61501,
    "GREEN": -61502,
    "RED": -61503,
    "METHANE": -61504,
}

#: Detector geometry the IK states in prose (see its "Apparent FOV Layout").
FRAMELET_LINES = 128
FRAMELET_SAMPLES = 1648
DARK_COLUMNS = 23
PHOTOACTIVE_COLUMNS = 1608

#: Iterations of the IK's fixed-point ``undistort``; the kernel's own comment
#: says "fixed number of iterations for simplicity" and uses five.
UNDISTORT_ITERATIONS = 5

IK_NAME = "juno_junocam_v03.ti"


# --------------------------------------------------------------------------
# kernel access
# --------------------------------------------------------------------------
def ik_path(mirror: str | Path | None = None) -> Path:
    """Path of the JunoCam IK inside the mirror."""
    if mirror is None:
        from ..config import mirror_root

        mirror = mirror_root()
    return Path(mirror) / "spice" / "ik" / IK_NAME


def load_junocam_ik(mirror: str | Path | None = None) -> Path:
    """``furnsh`` the JunoCam IK, once, and return its path.

    The IK is not part of :meth:`KernelSet.static_paths`, which loads the JIRAM
    instrument kernel instead, so JunoCam work has to add it explicitly.
    Loading is idempotent: ``furnsh`` on an already-loaded kernel is a no-op
    for the pool contents, but re-furnshing on every call would still grow the
    loaded-kernel list, so a kernel already in the pool is left alone.
    """
    path = ik_path(mirror)
    if not path.is_file():
        raise FileNotFoundError(f"missing JunoCam instrument kernel: {path}")
    target = str(path)
    for index in range(spiceypy.ktotal("ALL")):
        if spiceypy.kdata(index, "ALL")[0] == target:
            return path
    spiceypy.furnsh(target)
    return path


def _gdpool(name: str) -> float:
    """One scalar IK keyword, with a message that names the missing keyword."""
    try:
        values = spiceypy.gdpool(name, 0, 1)
    except spiceypy.utils.exceptions.SpiceyError as exc:  # pragma: no cover
        raise KeyError(f"IK keyword not in the kernel pool: {name}") from exc
    if len(values) == 0:  # pragma: no cover - gdpool raises first in practice
        raise KeyError(f"IK keyword not in the kernel pool: {name}")
    return float(values[0])


def band_id(band: str) -> int:
    """NAIF instrument id of a ``FILTER_NAME`` value."""
    key = str(band).strip().upper()
    if key not in BAND_IDS:
        raise ValueError(f"unknown JunoCam band {band!r}; known: {sorted(BAND_IDS)}")
    return BAND_IDS[key]


# --------------------------------------------------------------------------
# the per-band camera
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class BandCamera:
    """The IK's camera model for one filter strip.

    ``fl`` is in pixel widths (``FOCAL_LENGTH / PIXEL_SIZE``), so focal-plane
    coordinates and pixel coordinates share a unit and the distortion
    coefficients are dimensionless in that unit.
    """

    band: str
    ins_id: int
    cx: float
    cy: float
    k1: float
    k2: float
    fl: float
    lines: int = FRAMELET_LINES
    samples: int = FRAMELET_SAMPLES

    @classmethod
    def from_kernel(cls, band: str) -> "BandCamera":
        """Read every parameter of one band out of the loaded IK."""
        ins = band_id(band)
        return cls(
            band=str(band).strip().upper(),
            ins_id=ins,
            cx=_gdpool(f"INS{ins}_DISTORTION_X"),
            cy=_gdpool(f"INS{ins}_DISTORTION_Y"),
            k1=_gdpool(f"INS{ins}_DISTORTION_K1"),
            k2=_gdpool(f"INS{ins}_DISTORTION_K2"),
            fl=_gdpool(f"INS{ins}_FOCAL_LENGTH") / _gdpool(f"INS{ins}_PIXEL_SIZE"),
        )

    # -- the IK's two distortion functions ---------------------------------
    def distort(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        """Ideal focal-plane coordinates -> distorted ones (IK ``distort``)."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        r2 = x * x + y * y
        scale = 1.0 + self.k1 * r2 + self.k2 * r2 * r2
        return x * scale, y * scale

    def undistort(self, x, y) -> tuple[np.ndarray, np.ndarray]:
        """Distorted focal-plane coordinates -> ideal ones (IK ``undistort``).

        The kernel's fixed-point iteration, verbatim: five passes, each
        dividing the *original* coordinate by the distortion factor evaluated
        at the current estimate.
        """
        x0 = np.asarray(x, dtype=np.float64)
        y0 = np.asarray(y, dtype=np.float64)
        xd, yd = x0, y0
        for _ in range(UNDISTORT_ITERATIONS):
            r2 = xd * xd + yd * yd
            scale = 1.0 + self.k1 * r2 + self.k2 * r2 * r2
            xd = x0 / scale
            yd = y0 / scale
        return xd, yd

    # -- pixel <-> direction ----------------------------------------------
    def pixel_to_vector(self, x, y) -> np.ndarray:
        """0-based pixel centres ``(x, y)`` -> unit directions in ``JUNO_JUNOCAM``."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        cam_x, cam_y = self.undistort(x + 0.5 - self.cx, y + 0.5 - self.cy)
        vectors = np.stack([cam_x, cam_y, np.full(cam_x.shape, self.fl)], axis=-1)
        return vectors / np.linalg.norm(vectors, axis=-1, keepdims=True)

    def project(self, vectors) -> tuple[np.ndarray, np.ndarray]:
        """Directions in ``JUNO_JUNOCAM`` -> 0-based pixel centres ``(x, y)``.

        Rays in the rear hemisphere (``v_z <= 0``) come back as NaN rather than
        as the mirror image the naive division would produce.
        """
        vectors = np.asarray(vectors, dtype=np.float64)
        vz = vectors[..., 2]
        with np.errstate(invalid="ignore", divide="ignore"):
            alpha = vz / self.fl
            cam_x, cam_y = self.distort(vectors[..., 0] / alpha, vectors[..., 1] / alpha)
        forward = vz > 0.0
        x = np.where(forward, cam_x + self.cx - 0.5, np.nan)
        y = np.where(forward, cam_y + self.cy - 0.5, np.nan)
        return x, y

    def on_strip(self, x, y) -> np.ndarray:
        """True where ``(x, y)`` lands on this band's 128 x 1648 strip."""
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        with np.errstate(invalid="ignore"):
            return (x >= 0.0) & (x < self.samples) & (y >= 0.0) & (y < self.lines)

    def pixel_directions(self) -> np.ndarray:
        """Unit direction of every pixel centre, shaped ``(lines, samples, 3)``."""
        x, y = np.meshgrid(
            np.arange(self.samples, dtype=np.float64),
            np.arange(self.lines, dtype=np.float64),
            indexing="xy",
        )
        return self.pixel_to_vector(x, y)

    def boresight(self) -> np.ndarray:
        """``INS-6150N_BORESIGHT`` from :func:`spiceypy.getfov`, unit length."""
        _, _, bsight, _, _ = spiceypy.getfov(self.ins_id, 32)
        bsight = np.asarray(bsight, dtype=np.float64)
        return bsight / np.linalg.norm(bsight)


@lru_cache(maxsize=None)
def _band_camera_cached(band: str, signature: tuple[float, ...]) -> BandCamera:
    return BandCamera.from_kernel(band)


def band_cameras(bands) -> tuple[BandCamera, ...]:
    """One :class:`BandCamera` per band name, in the order given.

    The cache key carries the kernel-pool values themselves, so a different IK
    (or an unloaded pool) never returns a stale model.
    """
    cameras = []
    for band in bands:
        ins = band_id(band)
        signature = (
            _gdpool(f"INS{ins}_DISTORTION_X"),
            _gdpool(f"INS{ins}_DISTORTION_Y"),
            _gdpool(f"INS{ins}_DISTORTION_K1"),
            _gdpool(f"INS{ins}_DISTORTION_K2"),
            _gdpool(f"INS{ins}_FOCAL_LENGTH"),
            _gdpool(f"INS{ins}_PIXEL_SIZE"),
        )
        cameras.append(_band_camera_cached(str(band).strip().upper(), signature))
    return tuple(cameras)


def photoactive_mask(samples: int = FRAMELET_SAMPLES) -> np.ndarray:
    """Columns that carry light: 23 dark columns, then 1608 photoactive ones.

    The rest of the 1648-sample line is dark, isolation and overscan.
    """
    mask = np.zeros(int(samples), dtype=bool)
    stop = min(int(samples), DARK_COLUMNS + PHOTOACTIVE_COLUMNS)
    mask[DARK_COLUMNS:stop] = True
    return mask


# --------------------------------------------------------------------------
# timing
# --------------------------------------------------------------------------
def timing_parameters(band: str = "RED") -> tuple[float, float]:
    """``(START_TIME_BIAS, INTERFRAME_DELTA)`` in seconds, from the IK.

    The kernel repeats both values identically for all five ids; the band
    argument exists so the read is still a read of a real keyword rather than
    of an assumed constant.
    """
    ins = band_id(band)
    return _gdpool(f"INS{ins}_START_TIME_BIAS"), _gdpool(f"INS{ins}_INTERFRAME_DELTA")


def frame_epochs(
    start_et: float,
    n_frames: int,
    interframe_delay_s: float,
    *,
    band: str = "RED",
    dt: float = 0.0,
) -> np.ndarray:
    """Ephemeris time of every frame of an image.

    The IK's rule, verbatim::

        t_i = START_TIME + START_TIME_BIAS + i * (INTERFRAME_DELAY + INTERFRAME_DELTA)

    plus the per-image refinement ``dt``.  All bands of a frame share the
    epoch: the strips are read out together, so what separates them on the sky
    is their place on the focal plane, not their time.
    """
    bias, delta = timing_parameters(band)
    index = np.arange(int(n_frames), dtype=np.float64)
    return float(start_et) + bias + index * (float(interframe_delay_s) + delta) + float(dt)
