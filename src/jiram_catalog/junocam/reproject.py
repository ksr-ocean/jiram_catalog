"""Reprojecting a JunoCam image's bands onto a region grid.

The mapping is inverse, exactly as in :mod:`jiram_catalog.reproject`: every
output cell is taken back to a surface point, the surface point is taken back
through the camera, and the image is sampled where it lands.  Nothing is
scattered forward, so no output cell is left unpainted between two input
pixels and no input pixel is deposited twice.

What is new here is that the camera is a *time series*.  One ground point can
be seen by several frames and, because the four filter strips sit at different
places on the focal plane, by different frames in different bands: the RED and
GREEN strips are 155 pixel widths apart, and the spin carries the scene about
116 rows per interframe delay, so a point that RED sees in frame ``i`` GREEN
sees roughly one and a third frames later.  Every frame is therefore projected
independently and the overlapping ones are averaged per band.

That structure is what makes band-to-band registration of the *reprojected*
bands a real test of the geometry.  A common timing error moves all bands
alike and cancels out of a band difference; what does not cancel is the strip
offsets ``cy``, the interframe delay, and the attitude between the frames that
saw the same ground in different colours.  Red-green and red-blue shifts of
under a pixel on a 10 km/px grid are a statement about those, not about the
absolute epoch.
"""

from __future__ import annotations

import numpy as np

from ..reproject import _bilinear, surface_point
from .camera import photoactive_mask

__all__ = ["footprint_window", "phase_shift", "reproject_image"]

#: Stride over (rows, samples) when a framelet's footprint is measured; the
#: bounding box only needs the extremes, not every pixel.
FOOTPRINT_STRIDE = (4, 16)
#: Cells of slack around a measured footprint.
FOOTPRINT_PAD = 2


def footprint_window(grid, lat, lon_east, *, pad: int = FOOTPRINT_PAD):
    """Bounding box ``(row0, row1, col0, col1)`` of a set of surface points."""
    lat = np.asarray(lat, dtype=np.float64).ravel()
    lon_east = np.asarray(lon_east, dtype=np.float64).ravel()
    good = np.isfinite(lat) & np.isfinite(lon_east)
    if not good.any():
        return None
    row, col, visible = grid.latlon_to_pixel(lat[good], lon_east[good])
    keep = visible & np.isfinite(row) & np.isfinite(col)
    if not keep.any():
        return None
    row0 = max(int(np.floor(row[keep].min())) - pad, 0)
    row1 = min(int(np.ceil(row[keep].max())) + pad + 1, grid.rows)
    col0 = max(int(np.floor(col[keep].min())) - pad, 0)
    col1 = min(int(np.ceil(col[keep].max())) + pad + 1, grid.cols)
    if row1 <= row0 or col1 <= col0:
        return None
    return row0, row1, col0, col1


def _to_camera(points: np.ndarray, context) -> np.ndarray:
    """Surface points in ``IAU_JUPITER`` -> apparent directions in ``JUNO_JUNOCAM``.

    The inverse of :func:`jiram_catalog.junocam.geometry.body_rays`: the
    engine removed stellar aberration from the pixel rays, so it has to be put
    back on the observer-to-surface vector before the vector is expressed in
    the camera frame.
    """
    from ..geometry import aberrate

    to_point = points - context.obspos_km
    in_j2000 = to_point @ context.rot_j2000_to_body
    if context.velocity_j2000 is not None and np.any(context.velocity_j2000):
        in_j2000 = aberrate(in_j2000, context.velocity_j2000, inverse=False)
    return in_j2000 @ context.rot_cam_to_j2000


def reproject_image(
    image: np.ndarray,
    geo,
    grid,
    bands=None,
    *,
    stride: tuple[int, int] = FOOTPRINT_STRIDE,
    pad: int = FOOTPRINT_PAD,
) -> tuple[np.ndarray, np.ndarray]:
    """Average every frame of an image onto ``grid``, band by band.

    ``image`` is ``(n_frames, n_bands, 128, 1648)`` as
    :func:`jiram_catalog.junocam.images.read_image` returns it and ``geo`` the
    matching :class:`~jiram_catalog.junocam.geometry.ImageGeometry`; SPICE
    kernels need not still be loaded, because every transform the inverse chain
    needs was stored per frame.  Returns ``(data, count)``: the per-band mean
    of the frames that saw each cell, NaN where none did, and how many frames
    that was.
    """
    image = np.asarray(image)
    if image.shape[:2] != geo.lat.shape[:2]:
        raise ValueError(
            f"image {image.shape[:2]} does not match geometry {geo.lat.shape[:2]}"
        )
    names = tuple(geo.bands) if bands is None else tuple(str(b).upper() for b in bands)
    missing = [name for name in names if name not in geo.bands]
    if missing:
        raise ValueError(f"bands {missing} are not in the image ({list(geo.bands)})")
    indices = [geo.bands.index(name) for name in names]
    cameras = geo.cameras()
    photoactive = photoactive_mask(cameras[0].samples)

    total = np.zeros((len(names), grid.rows, grid.cols), dtype=np.float64)
    hits = np.zeros((len(names), grid.rows, grid.cols), dtype=np.int32)

    row_step, col_step = stride
    # The ellipsoid of the *camera* geometry, not the grid's: the inverse chain
    # has to invert the forward one, and the grid's radii only fix its own
    # pixel <-> (lat, lon) rule.  They agree for a grid built on pck00010.
    radii = np.asarray(geo.radii_km, dtype=np.float64)
    for frame in range(geo.n_frames):
        sub_lat = geo.lat[frame, :, ::row_step, ::col_step]
        sub_lon = geo.lon_east[frame, :, ::row_step, ::col_step]
        window = footprint_window(grid, sub_lat, sub_lon, pad=pad)
        if window is None:
            continue
        row0, row1, col0, col1 = window
        cell_lat, cell_lon = grid.lat_lon_grids(window)
        points = surface_point(cell_lat, cell_lon, radii)

        context = geo.frame_context(frame)
        in_camera = _to_camera(points, context)
        normal = points / radii**2
        normal /= np.linalg.norm(normal, axis=-1, keepdims=True)
        facing = np.sum(normal * (context.obspos_km - points), axis=-1) > 0.0

        for slot, band in enumerate(indices):
            camera = cameras[band]
            x, y = camera.project(in_camera)
            usable = facing & camera.on_strip(x, y)
            with np.errstate(invalid="ignore"):
                column = np.clip(np.nan_to_num(x), 0.0, camera.samples - 1.0)
            usable &= photoactive[np.floor(column).astype(np.intp)]
            usable &= photoactive[np.ceil(column).astype(np.intp)]
            if not usable.any():
                continue
            values, ok = _bilinear(image[frame, band].astype(np.float64), y, x)
            ok &= usable
            total[slot, row0:row1, col0:col1] += np.where(ok, values, 0.0)
            hits[slot, row0:row1, col0:col1] += ok.astype(np.int32)

    with np.errstate(invalid="ignore", divide="ignore"):
        data = np.where(hits > 0, total / np.maximum(hits, 1), np.nan).astype(np.float32)
    return data, np.minimum(hits, 255).astype(np.uint8)


# --------------------------------------------------------------------------
# band registration
# --------------------------------------------------------------------------
def _hann(shape: tuple[int, int]) -> np.ndarray:
    return np.outer(np.hanning(shape[0]), np.hanning(shape[1]))


def _parabolic(low: float, mid: float, high: float) -> float:
    """Sub-sample offset of a parabola's vertex through three samples."""
    denominator = low - 2.0 * mid + high
    if denominator == 0.0:
        return 0.0
    return float(np.clip(0.5 * (low - high) / denominator, -1.0, 1.0))


def phase_shift(reference: np.ndarray, moving: np.ndarray) -> tuple[float, float, int]:
    """Phase-correlation shift ``(d_row, d_col, n_overlap)`` of ``moving`` vs ``reference``.

    Only cells finite in both images take part; the rest are set to the common
    mean, which makes the overlap a window rather than a hole with an edge.
    The cross-power spectrum is ``F(reference) * conj(F(moving))``, so its peak
    sits at the displacement of ``moving`` from ``reference``: a feature that
    ``reference`` puts at row ``r`` is at row ``r + d_row`` in ``moving``.  The
    sub-pixel part comes from a parabolic fit through the peak and its two
    neighbours on each axis.
    """
    reference = np.asarray(reference, dtype=np.float64)
    moving = np.asarray(moving, dtype=np.float64)
    if reference.shape != moving.shape:
        raise ValueError("phase correlation needs two images of the same shape")
    overlap = np.isfinite(reference) & np.isfinite(moving)
    count = int(overlap.sum())
    if count < 64:
        return float("nan"), float("nan"), count

    def _prepare(values: np.ndarray) -> np.ndarray:
        filled = np.zeros_like(values)
        level = float(values[overlap].mean())
        filled[overlap] = values[overlap] - level
        return filled * _hann(values.shape)

    first = np.fft.rfft2(_prepare(reference))
    second = np.fft.rfft2(_prepare(moving))
    cross = first * np.conj(second)
    magnitude = np.abs(cross)
    cross = np.divide(cross, magnitude, out=np.zeros_like(cross), where=magnitude > 0)
    correlation = np.fft.irfft2(cross, s=reference.shape)

    peak = np.unravel_index(int(np.argmax(correlation)), correlation.shape)
    rows, cols = correlation.shape
    d_row = peak[0] + _parabolic(
        correlation[(peak[0] - 1) % rows, peak[1]],
        correlation[peak],
        correlation[(peak[0] + 1) % rows, peak[1]],
    )
    d_col = peak[1] + _parabolic(
        correlation[peak[0], (peak[1] - 1) % cols],
        correlation[peak],
        correlation[peak[0], (peak[1] + 1) % cols],
    )
    if d_row > rows / 2:
        d_row -= rows
    if d_col > cols / 2:
        d_col -= cols
    return float(d_row), float(d_col), count
