"""Array to PNG: illumination normalisation, striding, stretch, transparency.

A region stack is 3000 x 3200 per time step and the browser draws it in a
few hundred CSS pixels, so the server strides the array down before it
encodes: an integer stride is a pure subsample, which costs one slice and
keeps every served pixel a real measurement rather than an average of
several.  Invalid pixels leave as alpha 0 instead of as a colour, so the
front end can put a map under the image and see through the gaps.

Between the array and the stretch sits one more step for a reflected-light
instrument.  A JunoCam swath is lit from one side, and what dominates it is
therefore the cosine of the solar incidence rather than anything about
Jupiter.  Measured on the first orbit-4 strip of 2026-09-07: the RED band's
median runs from 2,480 DN where the incidence is 55 degrees to 286 DN where
it is 86, and dividing by ``cos(i)`` takes the same numbers to 4,600 and
4,700 -- eight-to-one becomes flat.  In variance, the correction takes the
band from a coefficient of variation of 0.60 to 0.27, so four fifths of what
the picture had to show was the Sun's angle.  The same division has to happen
before a spectrum is taken, or the slope belongs partly to the illumination.
The models are the two classical ones plus an empirical flattener:

``lambert``
    ``I / max(cos i, 0.05)``.  One parameter-free division; right for a
    diffuse surface, and close enough for cloud tops away from the limb.
``minnaert:k``
    ``I / (max(cos i, 0.05)^k * max(cos e, 0.05)^(k-1))``.  Minnaert's law
    with ``k = 1`` *is* Lambert; ``k`` near 0.7 is what fits Jupiter's
    visible cloud deck, and it also corrects the emission-angle limb
    brightening that Lambert leaves behind.
``flat:sigma``
    ``I`` over its own Gaussian low-pass, normalised to unit mean: no
    physics at all, but it removes any smooth gradient -- illumination,
    vignetting, a mosaic seam -- and is the honest choice when the
    geometry is what is being questioned.
"""

from __future__ import annotations

import io
import logging
import math

import numpy as np

LOGGER = logging.getLogger(__name__)

#: Default cap on the longer side of a served image, in pixels.
DEFAULT_MAX_PX = 1600

#: Solar incidence past which a reflected-light pixel is not a measurement;
#: the same angle :mod:`jiram_catalog.junocam.stacks` masks the products at,
#: repeated here because a product written before the mask must still display
#: like one written after it.
NIGHT_INCIDENCE_DEG = 88.0

#: Floor under every cosine, so a division near the terminator or the limb
#: multiplies by at most twenty rather than by infinity.
MIN_COS = 0.05

#: Minnaert's exponent: the range the front end offers and its default.
MINNAERT_K_RANGE = (0.3, 1.2)
DEFAULT_MINNAERT_K = 0.7

#: Gaussian flattener: the width range the front end offers, in pixels of the
#: *served* image, and its default.
FLAT_SIGMA_RANGE = (1.0, 512.0)
DEFAULT_FLAT_SIGMA = 32.0

#: Kernel half-width, in units of sigma.  Three sigma holds 99.7 per cent of
#: the weight; further out the tail is below the noise of anything this is
#: used to divide by.
FLAT_TRUNCATE = 3.0

#: Every normalisation name the API answers to, in the order the selector
#: shows them.
NORM_NAMES: tuple[str, ...] = ("none", "lambert", "minnaert", "flat")

#: PNG mappings: linear, or the ``asinh`` stretch that keeps a bright core
#: from eating the whole top of the range.
STRETCH_MODES: tuple[str, ...] = ("linear", "asinh")

#: Softening constant of the ``asinh`` mapping.  Ten puts the knee about a
#: tenth of the way up the range, which is where a cloud field's histogram
#: sits once the illumination has been divided out.
ASINH_SOFTENING = 10.0


# ---------------------------------------------------------------------------
# illumination normalisation
# ---------------------------------------------------------------------------
def parse_norm(text: str | None) -> tuple[str, float]:
    """``"minnaert:0.8"`` -> ``("minnaert", 0.8)``; a name alone takes its default.

    ``None`` and the empty string mean "no normalisation", so a caller can
    pass a query parameter straight through.  An unknown name or an
    out-of-range parameter raises :class:`ValueError`, which the routes turn
    into a 400: silently falling back to ``none`` would answer a question the
    client did not ask.
    """
    raw = "" if text is None else str(text).strip()
    if not raw:
        return "none", float("nan")
    name, _, argument = raw.partition(":")
    name = name.strip().lower()
    if name not in NORM_NAMES:
        raise ValueError(f"unknown norm {raw!r}; known: {', '.join(NORM_NAMES)}")
    if name in ("none", "lambert"):
        if argument.strip():
            raise ValueError(f"{name} takes no parameter (got {raw!r})")
        return name, float("nan")
    default, low, high = (
        (DEFAULT_MINNAERT_K, *MINNAERT_K_RANGE)
        if name == "minnaert"
        else (DEFAULT_FLAT_SIGMA, *FLAT_SIGMA_RANGE)
    )
    if not argument.strip():
        return name, float(default)
    try:
        value = float(argument)
    except ValueError as exc:
        raise ValueError(f"{name} wants a number, not {argument!r}") from exc
    if not np.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name} parameter must be in [{low:g}, {high:g}], not {value:g}")
    return name, value


def norm_label(name: str, parameter: float) -> str:
    """The canonical spelling of a parsed norm, which is also its cache key."""
    if name in ("none", "lambert") or not np.isfinite(parameter):
        return name
    return f"{name}:{parameter:g}"


def norm_options(has_incidence: bool, has_emission: bool = True) -> list[str]:
    """Which normalisations a product can actually answer for.

    ``lambert`` and ``minnaert`` need the per-pixel solar incidence, which
    only a product built with it carries; ``flat`` needs nothing but the
    image, and ``none`` is always on offer.
    """
    names = ["none"]
    if has_incidence:
        names.append("lambert")
        if has_emission:
            names.append("minnaert")
    names.append("flat")
    return names


def _gaussian_kernel(sigma: float, limit: int) -> np.ndarray:
    """A unit-sum Gaussian, truncated at three sigma and at ``limit`` samples.

    ``np.convolve(..., mode="same")`` returns as many samples as the *longer*
    of its two arguments, so a kernel wider than the axis it runs along would
    hand back a plane of the wrong shape; the half-width is capped at half the
    axis, which is as wide as a low-pass of that axis can mean anything.
    """
    radius = int(min(math.ceil(FLAT_TRUNCATE * float(sigma)), max((limit - 1) // 2, 0)))
    offsets = np.arange(-radius, radius + 1, dtype=np.float64)
    kernel = np.exp(-0.5 * (offsets / float(sigma)) ** 2)
    return kernel / kernel.sum()


def gaussian_lowpass(values: np.ndarray, weights: np.ndarray, sigma: float) -> np.ndarray:
    """Weighted Gaussian low-pass of a masked field, by separable convolution.

    The field is convolved with the weights zeroed out where the mask is
    false, and the result divided by the identically blurred mask: that is a
    local weighted mean, so a hole in the middle of the swath is filled by
    what surrounds it rather than pulled towards zero, and the edge of the
    swath does not fade.  Separable and one-dimensional, so the cost is two
    passes of ``np.convolve`` rather than a two-dimensional kernel -- and it
    runs on the *served* array, after striding, which is what keeps a 256
    pixel sigma from costing a second.
    """
    field = np.asarray(values, dtype=np.float64)
    mask = np.asarray(weights, dtype=np.float64)
    filled = np.where(mask > 0.0, np.nan_to_num(field, nan=0.0), 0.0)

    if field.ndim != 2:
        raise ValueError(f"the flattener wants one map plane, not {field.shape}")

    def blur(plane: np.ndarray) -> np.ndarray:
        rows, cols = plane.shape
        along_x = _gaussian_kernel(sigma, cols)
        along_y = _gaussian_kernel(sigma, rows)
        out = np.apply_along_axis(lambda line: np.convolve(line, along_x, mode="same"), 1, plane)
        return np.apply_along_axis(lambda line: np.convolve(line, along_y, mode="same"), 0, out)

    smooth = blur(filled)
    cover = blur(mask)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(cover > 1e-6, smooth / np.maximum(cover, 1e-6), np.nan)


def normalise_plane(
    image: np.ndarray,
    *,
    incidence: np.ndarray | None = None,
    emission: np.ndarray | None = None,
    valid: np.ndarray | None = None,
    norm: str = "none",
    parameter: float = float("nan"),
) -> tuple[np.ndarray, np.ndarray]:
    """One plane divided by an illumination model, and the mask that survives.

    Returns ``(values, mask)``: the normalised field with NaN wherever the
    mask is false, and the mask itself -- the caller's ``valid``, narrowed to
    the finite pixels and, for every model but ``none``, to the pixels lit to
    within :data:`NIGHT_INCIDENCE_DEG` of the terminator.  The night cut is
    made only when a model is asked for, so a JIRAM product -- which observes
    the night side on purpose and whose default is ``none`` -- is never masked
    by an angle that means nothing to a thermal camera.

    A model that needs an angle the product does not carry is a no-op with a
    warning rather than an error: the amendment's viewers ask for the
    instrument's default before they know what the file holds.
    """
    values = np.asarray(image, dtype=np.float64)
    mask = np.isfinite(values)
    if valid is not None:
        mask = mask & np.asarray(valid, dtype=bool)
    name = str(norm or "none").lower()
    if name == "none":
        return np.where(mask, values, np.nan), mask

    sun = None if incidence is None else np.asarray(incidence, dtype=np.float64)
    if sun is not None:
        with np.errstate(invalid="ignore"):
            mask = mask & np.isfinite(sun) & (sun < NIGHT_INCIDENCE_DEG)

    if name in ("lambert", "minnaert") and sun is None:
        LOGGER.warning("norm=%s asked of a product without incidence; serving it raw", name)
        return np.where(mask, values, np.nan), mask

    if name == "lambert":
        with np.errstate(invalid="ignore"):
            factor = np.maximum(np.cos(np.radians(sun)), MIN_COS)
    elif name == "minnaert":
        k = DEFAULT_MINNAERT_K if not np.isfinite(parameter) else float(parameter)
        view = None if emission is None else np.asarray(emission, dtype=np.float64)
        with np.errstate(invalid="ignore"):
            factor = np.maximum(np.cos(np.radians(sun)), MIN_COS) ** k
            if view is not None:
                factor = factor * np.maximum(np.cos(np.radians(view)), MIN_COS) ** (k - 1.0)
    else:  # flat
        sigma = DEFAULT_FLAT_SIGMA if not np.isfinite(parameter) else float(parameter)
        background = gaussian_lowpass(values, mask.astype(np.float64), max(sigma, 1.0))
        level = background[mask]
        level = level[np.isfinite(level) & (level != 0.0)]
        scale = float(np.mean(level)) if level.size else 1.0
        with np.errstate(invalid="ignore", divide="ignore"):
            factor = background / (scale if scale != 0.0 else 1.0)
        factor = np.where(np.isfinite(factor) & (np.abs(factor) > 1e-6), factor, np.nan)

    with np.errstate(invalid="ignore", divide="ignore"):
        out = np.where(mask, values / factor, np.nan)
    mask = mask & np.isfinite(out)
    return np.where(mask, out, np.nan), mask


def percentiles_of(values: np.ndarray, mask: np.ndarray, low: float, high: float) -> tuple[float, float]:
    """``(p_low, p_high)`` over the masked, finite values; ``(0, 1)`` if none."""
    pool = np.asarray(values, dtype=np.float64)[np.asarray(mask, dtype=bool)]
    pool = pool[np.isfinite(pool)]
    if pool.size == 0:
        return 0.0, 1.0
    first, second = (float(value) for value in np.percentile(pool, (low, high)))
    return first, (second if second > first else first + 1.0)


def stride_for(shape: tuple[int, int], max_px: int) -> int:
    """Integer stride that brings the longer side of ``shape`` to ``max_px``."""
    longest = max(int(shape[0]), int(shape[1]))
    limit = max(1, int(max_px))
    return max(1, int(np.ceil(longest / limit)))


def stretch_to_uint8(
    values: np.ndarray, vmin: float, vmax: float, mode: str = "linear"
) -> tuple[np.ndarray, np.ndarray]:
    """Stretch to 0-255 plus the mask of the pixels that had a value.

    A degenerate range (``vmax <= vmin``) would divide by zero; it is
    widened by one unit instead, which renders a constant field as a flat
    grey rather than as an error.

    ``mode="asinh"`` maps the same ``[vmin, vmax]`` window through
    ``asinh(a t) / asinh(a)``, which is linear near the bottom and
    logarithmic near the top: a normalised JunoCam band has a long bright
    tail -- a plume, a specular patch of a cloud top -- and a linear map
    spends most of the eight bits on it.
    """
    array = np.asarray(values, dtype=np.float64)
    low = float(vmin)
    high = float(vmax)
    if not np.isfinite(low) or not np.isfinite(high) or high <= low:
        high = low + 1.0
    finite = np.isfinite(array)
    scaled = np.zeros(array.shape, dtype=np.float64)
    np.divide(array - low, high - low, out=scaled, where=finite)
    scaled = np.clip(scaled, 0.0, 1.0)
    if str(mode).lower() == "asinh":
        scaled = np.arcsinh(ASINH_SOFTENING * scaled) / np.arcsinh(ASINH_SOFTENING)
    grey = scaled * 255.0
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
    mode: str = "linear",
    incidence: np.ndarray | None = None,
    emission: np.ndarray | None = None,
    norm: str = "none",
    parameter: float = float("nan"),
) -> tuple[bytes, int, tuple[int, int]]:
    """PNG of a 2-D plane, its stride and its served ``(rows, cols)``.

    Row 0 of the served image is the smallest ``y``, which is the stack's
    own convention; the front end is told so by ``X-Bounds`` and never has
    to flip anything.

    The normalisation runs *after* the striding, on the array the browser
    will actually see.  For ``lambert`` and ``minnaert`` that is exactly
    equivalent -- the correction is per pixel -- and for ``flat`` it means
    the sigma the user asked for is a distance on the served image, which is
    also the picture they are looking at while they drag the slider.
    """
    array = np.asarray(values)
    stride = stride_for(array.shape, max_px)
    plane = array[::stride, ::stride]
    mask = None if valid is None else np.asarray(valid, dtype=bool)[::stride, ::stride]
    plane, mask = normalise_plane(
        plane,
        incidence=None if incidence is None else np.asarray(incidence)[::stride, ::stride],
        emission=None if emission is None else np.asarray(emission)[::stride, ::stride],
        valid=mask,
        norm=norm,
        parameter=parameter,
    )
    grey, finite = stretch_to_uint8(plane, vmin, vmax, mode)
    return rgba_png(grey, finite & mask), stride, (int(plane.shape[0]), int(plane.shape[1]))


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
