"""Array to PNG, with the striding and the transparency the viewer needs.

A region stack is 3000 x 3200 per time step and the browser draws it in a
few hundred CSS pixels, so the server strides the array down before it
encodes: an integer stride is a pure subsample, which costs one slice and
keeps every served pixel a real measurement rather than an average of
several.  Invalid pixels leave as alpha 0 instead of as a colour, so the
front end can put a map under the image and see through the gaps.
"""

from __future__ import annotations

import io
import logging

import numpy as np

LOGGER = logging.getLogger(__name__)

#: Default cap on the longer side of a served image, in pixels.
DEFAULT_MAX_PX = 1600


def stride_for(shape: tuple[int, int], max_px: int) -> int:
    """Integer stride that brings the longer side of ``shape`` to ``max_px``."""
    longest = max(int(shape[0]), int(shape[1]))
    limit = max(1, int(max_px))
    return max(1, int(np.ceil(longest / limit)))


def stretch_to_uint8(
    values: np.ndarray, vmin: float, vmax: float
) -> tuple[np.ndarray, np.ndarray]:
    """Linear stretch to 0-255 plus the mask of the pixels that had a value.

    A degenerate range (``vmax <= vmin``) would divide by zero; it is
    widened by one unit instead, which renders a constant field as a flat
    grey rather than as an error.
    """
    array = np.asarray(values, dtype=np.float64)
    low = float(vmin)
    high = float(vmax)
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        high = low + 1.0
    finite = np.isfinite(array)
    scaled = np.zeros(array.shape, dtype=np.float64)
    np.divide(array - low, high - low, out=scaled, where=finite)
    grey = np.clip(scaled, 0.0, 1.0) * 255.0
    return np.where(finite, grey, 0.0).astype(np.uint8), finite


def rgba_png(grey: np.ndarray, mask: np.ndarray) -> bytes:
    """Encode a grey plane and its validity mask as an 8-bit RGBA PNG.

    ``imageio`` is already a dependency (it writes the movies), so no new
    imaging library enters the project for this.
    """
    import imageio.v3 as iio

    rows, cols = grey.shape
    rgba = np.zeros((rows, cols, 4), dtype=np.uint8)
    rgba[..., 0] = grey
    rgba[..., 1] = grey
    rgba[..., 2] = grey
    rgba[..., 3] = np.where(np.asarray(mask, dtype=bool), 255, 0).astype(np.uint8)
    return bytes(iio.imwrite("<bytes>", rgba, extension=".png"))


def plane_png(
    values: np.ndarray,
    valid: np.ndarray | None,
    *,
    vmin: float,
    vmax: float,
    max_px: int = DEFAULT_MAX_PX,
) -> tuple[bytes, int, tuple[int, int]]:
    """PNG of a 2-D plane, its stride and its served ``(rows, cols)``.

    Row 0 of the served image is the smallest ``y``, which is the stack's
    own convention; the front end is told so by ``X-Bounds`` and never has
    to flip anything.
    """
    array = np.asarray(values)
    stride = stride_for(array.shape, max_px)
    plane = array[::stride, ::stride]
    grey, finite = stretch_to_uint8(plane, vmin, vmax)
    mask = finite
    if valid is not None:
        mask = finite & np.asarray(valid, dtype=bool)[::stride, ::stride]
    return rgba_png(grey, mask), stride, (int(plane.shape[0]), int(plane.shape[1]))


def emission_png(
    values: np.ndarray,
    valid: np.ndarray | None,
    *,
    max_px: int = DEFAULT_MAX_PX,
) -> tuple[bytes, int, tuple[int, int]]:
    """The same, with the fixed 0-90 degree scale an emission angle wants."""
    return plane_png(values, valid, vmin=0.0, vmax=90.0, max_px=max_px)


def bounds_header(
    x_km: np.ndarray, y_km: np.ndarray, stride: int, shape: tuple[int, int]
) -> str:
    """``xmin,xmax,ymin,ymax`` of the *served* image, in km.

    The served corners are those of the sampled pixels, not of the full
    array: striding drops the last few rows and columns whenever the size
    is not a multiple of the stride, and a front end that assumed
    otherwise would draw the image a pixel or two too wide.
    """
    xs = np.asarray(x_km, dtype=np.float64)[::stride][: shape[1]]
    ys = np.asarray(y_km, dtype=np.float64)[::stride][: shape[0]]
    if xs.size == 0 or ys.size == 0:
        return "0,0,0,0"
    return f"{xs[0]:.6g},{xs[-1]:.6g},{ys[0]:.6g},{ys[-1]:.6g}"


def image_headers(
    shape: tuple[int, int], stride: int, bounds: str, *, max_age: int = 3600
) -> dict[str, str]:
    """The headers the contract puts on every served image."""
    return {
        "X-Rows": str(int(shape[0])),
        "X-Cols": str(int(shape[1])),
        "X-Stride": str(int(stride)),
        "X-Bounds": bounds,
        "Cache-Control": f"max-age={int(max_age)}",
    }
