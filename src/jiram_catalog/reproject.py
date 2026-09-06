"""Polar stereographic map grid and exact inverse-camera reprojection.

Two pieces meet here.  :class:`PolarStereo` is the map: a plane tangent at the
pole in which a map pixel is a fixed number of kilometres.  :func:`project_to_pixels`
is the camera: it runs the geometry engine's forward chain backwards, taking a
planetocentric (lat, lon) to the fractional detector pixel that saw it.  Putting
one inside the other gives :func:`reproject_frame`, which fills a map by asking,
for every output pixel, which input pixel it came from -- exact inverse mapping,
so no output pixel is left unpainted and no input pixel is scattered twice.

The camera inverse mirrors :func:`jiram_catalog.geometry.frame_geometry` step for
step, including the stellar aberration bookkeeping: the engine converts each
*apparent* pixel ray into its geometric counterpart before intersecting the
ellipsoid, so the inverse must re-apply the *forward* correction to the geometric
observer-to-surface vector before it is expressed in the band frame.  Skipping
that leaves a systematic error of order ``|v|/c`` (about 7 arcsec, a third of a
JIRAM pixel).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import spiceypy

from .geometry import BANDS, TARGET, TARGET_FRAME, aberrate

__all__ = ["PolarStereo", "PAPER_GRID", "project_to_pixels", "reproject_frame"]

OBSERVER = "JUNO"


#: Radial laws ``rho(zeta)`` a polar azimuthal grid can use.  ``"stereographic"``
#: is the spec's default; the others exist because the fit had to decide between
#: them empirically (see docs/reports/paper_projection_fit.md).
PROJECTIONS = ("stereographic", "orthographic", "equidistant", "lambert")


@dataclass(frozen=True)
class PolarStereo:
    """Polar azimuthal map grid, true to scale at the pole.

    Sign convention (0-based pixel coordinates, ``line`` increasing downward as
    an image is displayed, ``sample`` increasing to the right)::

        zeta  = 90 - lat            (hemisphere "N";  90 + lat for "S")
        rho   = 2 * radius_km * tan(zeta / 2)                      [km]
        az    = (+1 if not clockwise else -1) * (lon_east - lon0_deg)
        x     =  rho * cos(az)      [km, along +sample]
        y     =  rho * sin(az)      [km, along -line, i.e. "up" on screen]
        sample = pole_sample + x / km_per_px
        line   = pole_line   - y / km_per_px

    So the meridian ``lon0_deg`` runs from the pole along the +sample axis, and
    ``clockwise=True`` means east longitude increases clockwise on screen.
    Flipping ``clockwise`` mirrors the map about the ``lon0_deg`` meridian, i.e.
    it flips the sign of the cross-track coordinate ``y``.

    ``projection`` selects the radial law ``rho(zeta)``; every law is true to
    scale at the pole, so ``radius_km`` and ``km_per_px`` enter only through
    their ratio and all four laws agree to first order in ``zeta``::

        stereographic  rho = 2 R tan(zeta/2)          (conformal; the default)
        equidistant    rho = R zeta
        lambert        rho = 2 R sin(zeta/2)          (equal area)
        orthographic   rho = |projection of the surface point on the
                              equatorial plane| = sin(zeta) / sqrt(sin^2(zeta)/A^2
                              + cos^2(zeta)/R^2)

    where ``R = radius_km`` is the polar (or sphere) radius and
    ``A = equatorial_radius_km`` (defaulting to ``R``, i.e. a sphere).  Only the
    orthographic law uses ``A``; it is the projection the published maps turned
    out to use, with ``R``/``A`` Jupiter's polar/equatorial radii.
    """

    pole_line: float
    pole_sample: float
    km_per_px: float
    lon0_deg: float
    clockwise: bool
    hemisphere: str
    radius_km: float
    projection: str = "stereographic"
    equatorial_radius_km: float | None = None

    def __post_init__(self) -> None:
        if self.hemisphere not in ("N", "S"):
            raise ValueError(f"hemisphere must be 'N' or 'S', not {self.hemisphere!r}")
        if not self.km_per_px > 0:
            raise ValueError("km_per_px must be positive")
        if not self.radius_km > 0:
            raise ValueError("radius_km must be positive")
        if self.projection not in PROJECTIONS:
            raise ValueError(f"projection must be one of {PROJECTIONS}, not {self.projection!r}")
        if self.equatorial_radius_km is not None and not self.equatorial_radius_km > 0:
            raise ValueError("equatorial_radius_km must be positive")

    # -- helpers -----------------------------------------------------------
    @property
    def _sense(self) -> float:
        """+1 when east longitude increases counter-clockwise on screen."""
        return -1.0 if self.clockwise else 1.0

    @property
    def _equatorial(self) -> float:
        return self.radius_km if self.equatorial_radius_km is None else self.equatorial_radius_km

    def colatitude_deg(self, lat: np.ndarray) -> np.ndarray:
        """Angular distance from the projection pole, in degrees."""
        lat = np.asarray(lat, dtype=np.float64)
        return 90.0 - lat if self.hemisphere == "N" else 90.0 + lat

    def rho_km(self, colatitude_deg) -> np.ndarray:
        """Radial map distance from the pole, in km, for a colatitude in degrees."""
        zeta = np.radians(np.clip(np.asarray(colatitude_deg, dtype=np.float64), 0.0, 180.0))
        radius = self.radius_km
        if self.projection == "stereographic":
            return 2.0 * radius * np.tan(np.minimum(zeta, np.pi - 1e-9) / 2.0)
        if self.projection == "equidistant":
            return radius * zeta
        if self.projection == "lambert":
            return 2.0 * radius * np.sin(zeta / 2.0)
        equatorial = self._equatorial
        sine, cosine = np.sin(zeta), np.cos(zeta)
        return sine / np.sqrt((sine / equatorial) ** 2 + (cosine / radius) ** 2)

    def colatitude_of_rho(self, rho) -> np.ndarray:
        """Inverse of :meth:`rho_km`: colatitude in degrees for a radius in km."""
        rho = np.asarray(rho, dtype=np.float64)
        radius = self.radius_km
        if self.projection == "stereographic":
            return np.degrees(2.0 * np.arctan2(rho, 2.0 * radius))
        if self.projection == "equidistant":
            return np.degrees(rho / radius)
        if self.projection == "lambert":
            return np.degrees(2.0 * np.arcsin(np.clip(rho / (2.0 * radius), -1.0, 1.0)))
        equatorial = self._equatorial
        # rho^2 (sin^2/A^2 + cos^2/R^2) = sin^2 solved for sin^2(zeta).
        squared = (rho / radius) ** 2 / (1.0 + rho**2 * (1.0 / radius**2 - 1.0 / equatorial**2))
        return np.degrees(np.arcsin(np.sqrt(np.clip(squared, 0.0, 1.0))))

    def latlon_to_km(self, lat, lon_east) -> tuple[np.ndarray, np.ndarray]:
        """Projection-plane coordinates in km (``x`` toward +sample, ``y`` toward -line)."""
        rho = self.rho_km(self.colatitude_deg(lat))
        az = np.radians(self._sense * (np.asarray(lon_east, dtype=np.float64) - self.lon0_deg))
        return rho * np.cos(az), rho * np.sin(az)

    # -- the two mappings --------------------------------------------------
    def latlon_to_pixel(self, lat, lon_east) -> tuple[np.ndarray, np.ndarray]:
        """Map planetocentric (lat, lon_east) in degrees to 0-based (line, sample)."""
        x, y = self.latlon_to_km(lat, lon_east)
        return self.pole_line - y / self.km_per_px, self.pole_sample + x / self.km_per_px

    def pixel_to_latlon(self, line, sample) -> tuple[np.ndarray, np.ndarray]:
        """Map 0-based (line, sample) to planetocentric (lat, lon_east) in degrees."""
        x = (np.asarray(sample, dtype=np.float64) - self.pole_sample) * self.km_per_px
        y = (self.pole_line - np.asarray(line, dtype=np.float64)) * self.km_per_px
        zeta = self.colatitude_of_rho(np.hypot(x, y))
        lat = 90.0 - zeta if self.hemisphere == "N" else zeta - 90.0
        az = np.degrees(np.arctan2(y, x))
        lon = (self.lon0_deg + self._sense * az) % 360.0
        return lat, lon


# --------------------------------------------------------------------------
# inverse camera model
# --------------------------------------------------------------------------
def _camera_transforms(geo) -> tuple[np.ndarray, np.ndarray, np.ndarray | None, np.ndarray]:
    """``(M_body_to_j2000_T, M_j2000_to_frame_T, velocity, radii)`` for one frame.

    The two matrices are returned already transposed for row-vector use, in the
    order the inverse chain applies them.
    """
    if geo.band not in BANDS:
        raise ValueError(f"band must be 'L' or 'M', not {geo.band!r}")
    m_frame_to_j2000 = np.asarray(spiceypy.pxform(geo.frame, "J2000", geo.et))
    m_j2000_to_body = np.asarray(spiceypy.pxform("J2000", TARGET_FRAME, geo.trgepc))
    state, _ = spiceypy.spkezr(OBSERVER, geo.et, "J2000", "NONE", "SOLAR SYSTEM BARYCENTER")
    velocity = np.asarray(state, dtype=np.float64)[3:6]
    radii = np.asarray(spiceypy.bodvrd(TARGET, "RADII", 3)[1], dtype=np.float64)
    return m_j2000_to_body, m_frame_to_j2000, velocity, radii


def surface_point(lat, lon_east, radii) -> np.ndarray:
    """Planetocentric (lat, lon) in degrees to the ellipsoid surface point, km."""
    lat = np.radians(np.asarray(lat, dtype=np.float64))
    lon = np.radians(np.asarray(lon_east, dtype=np.float64))
    radii = np.asarray(radii, dtype=np.float64).reshape(3)
    direction = np.stack(
        [np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)], axis=-1
    )
    scale = 1.0 / np.sqrt(np.sum((direction / radii) ** 2, axis=-1))
    return direction * scale[..., None]


def project_to_pixels(geo, lat, lon_east) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Inverse camera model: (lat, lon_east) -> fractional 1-based (line, sample).

    ``geo`` is a :class:`jiram_catalog.geometry.FrameGeometry`; kernels must
    still be loaded.  The returned ``visible`` mask is True where the surface
    faces the observer (emission < 90 deg) and the ray points down the boresight
    hemisphere (``dz > 0``); it says nothing about the detector bounds, which
    :func:`reproject_frame` applies separately.  Assumes the frame was computed
    with an ``LT+S`` correction, as :func:`frame_geometry` does by default.
    """
    m_j2000_to_body, m_frame_to_j2000, velocity, radii = _camera_transforms(geo)

    point = surface_point(lat, lon_east, radii)
    to_point = point - np.asarray(geo.obspos_km, dtype=np.float64)

    # Undo the body rotation, re-apply the forward stellar aberration (the engine
    # removed it from the rays), then step into the band frame.
    in_j2000 = to_point @ m_j2000_to_body
    apparent = aberrate(in_j2000, velocity, inverse=False)
    in_frame = apparent @ m_frame_to_j2000

    dx, dy, dz = in_frame[..., 0], in_frame[..., 1], in_frame[..., 2]
    with np.errstate(invalid="ignore", divide="ignore"):
        x = dx / dz
        y = dy / dz
        line = geo.lines / 2.0 + 0.5 - x / geo.ifov_rad
        sample = geo.samples / 2.0 + 0.5 - y / geo.ifov_rad

    normal = point / radii**2
    normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
    to_observer = -to_point
    facing = np.sum(normal * to_observer, axis=-1) > 0.0
    with np.errstate(invalid="ignore"):
        visible = facing & (dz > 0.0) & np.isfinite(line) & np.isfinite(sample)
    return line, sample, visible


# --------------------------------------------------------------------------
# reprojection
# --------------------------------------------------------------------------
def _bilinear(image: np.ndarray, row: np.ndarray, col: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Bilinear sample of ``image`` at 0-based fractional (row, col).

    Returns ``(values, ok)``; ``ok`` is False outside the array and wherever any
    of the four neighbours is not finite.
    """
    lines, samples = image.shape
    inside = (
        np.isfinite(row) & np.isfinite(col)
        & (row >= 0.0) & (row <= lines - 1.0)
        & (col >= 0.0) & (col <= samples - 1.0)
    )
    row_c = np.clip(np.nan_to_num(row), 0.0, lines - 1.0)
    col_c = np.clip(np.nan_to_num(col), 0.0, samples - 1.0)
    r0 = np.clip(np.floor(row_c).astype(np.intp), 0, lines - 2 if lines > 1 else 0)
    c0 = np.clip(np.floor(col_c).astype(np.intp), 0, samples - 2 if samples > 1 else 0)
    r1 = np.minimum(r0 + 1, lines - 1)
    c1 = np.minimum(c0 + 1, samples - 1)
    fr = row_c - r0
    fc = col_c - c0

    v00, v01 = image[r0, c0], image[r0, c1]
    v10, v11 = image[r1, c0], image[r1, c1]
    ok = inside & np.isfinite(v00) & np.isfinite(v01) & np.isfinite(v10) & np.isfinite(v11)
    values = (
        (1.0 - fr) * ((1.0 - fc) * np.nan_to_num(v00) + fc * np.nan_to_num(v01))
        + fr * ((1.0 - fc) * np.nan_to_num(v10) + fc * np.nan_to_num(v11))
    )
    return np.where(ok, values, 0.0), ok


def footprint_bbox(
    geo, grid: PolarStereo, shape: tuple[int, int], pad: int = 3
) -> tuple[int, int, int, int] | None:
    """Bounding box ``(line0, line1, sample0, sample1)`` of a frame in the map."""
    line, sample = grid.latlon_to_pixel(geo.lat, geo.lon_east)
    good = geo.on_planet & np.isfinite(line) & np.isfinite(sample)
    if not good.any():
        return None
    line0 = int(np.floor(line[good].min())) - pad
    line1 = int(np.ceil(line[good].max())) + pad + 1
    sample0 = int(np.floor(sample[good].min())) - pad
    sample1 = int(np.ceil(sample[good].max())) + pad + 1
    line0, sample0 = max(line0, 0), max(sample0, 0)
    line1, sample1 = min(line1, shape[0]), min(sample1, shape[1])
    if line1 <= line0 or sample1 <= sample0:
        return None
    return line0, line1, sample0, sample1


def reproject_frame(
    image: np.ndarray,
    geo,
    grid: PolarStereo,
    shape: tuple[int, int],
    *,
    method: str = "bilinear",
) -> tuple[np.ndarray, np.ndarray]:
    """Reproject one raw frame onto ``grid`` by exact inverse mapping.

    Every output pixel is taken back through the map projection and then through
    the inverse camera model; the input is sampled there.  Output pixels that
    fall outside the detector, on the far side of the limb, or on a NaN input
    neighbourhood get ``out = 0`` and ``weight = 0``.  Only the footprint's
    bounding box (padded by 3 px) is computed.
    """
    if method not in ("bilinear", "nearest"):
        raise ValueError(f"unknown method: {method!r}")
    image = np.asarray(image, dtype=np.float64)
    out = np.zeros(shape, dtype=np.float64)
    weight = np.zeros(shape, dtype=np.float64)

    box = footprint_bbox(geo, grid, shape)
    if box is None:
        return out, weight
    line0, line1, sample0, sample1 = box

    lines = np.arange(line0, line1, dtype=np.float64)
    samples = np.arange(sample0, sample1, dtype=np.float64)
    grid_line, grid_sample = np.meshgrid(lines, samples, indexing="ij")

    lat, lon = grid.pixel_to_latlon(grid_line, grid_sample)
    src_line, src_sample, visible = project_to_pixels(geo, lat, lon)

    row = src_line - 1.0
    col = src_sample - 1.0
    if method == "nearest":
        row = np.round(row)
        col = np.round(col)
    values, ok = _bilinear(image, row, col)
    ok &= visible

    out[line0:line1, sample0:sample1] = np.where(ok, values, 0.0)
    weight[line0:line1, sample0:sample1] = ok.astype(np.float64)
    return out, weight


# --- BEGIN FITTED PAPER GRID (rewritten by scripts/fit_paper_projection.py) ---
#: The map projection of the 48 published perijove-4 maps of Ingersoll et al.
#: (2022), recovered empirically; see docs/reports/paper_projection_fit.md.
PAPER_GRID = PolarStereo(
    pole_line=1800.0003,
    pole_sample=1800.0131,
    km_per_px=15.0046,
    lon0_deg=90.0009,
    clockwise=False,
    hemisphere='N',
    radius_km=66854.0,
    projection='orthographic',
    equatorial_radius_km=71492.0,
)
# --- END FITTED PAPER GRID ---
