"""Limb detection and the per-image start-time refinement.

JunoCam's timing is the weakest link in its geometry, and the limb is the one
feature in the image whose position the geometry predicts exactly.  Juno spins
at about 2 RPM, or 12 deg/s, and one pixel of the focal plane subtends
``1/1480.6`` rad, or 0.0387 deg, so the scene crosses the strips at about 306
pixel rows per second (measured, not assumed): a one millisecond timing error
is a third of a pixel, and the 61.88 ms bias the IK publishes is nineteen
pixels.  Fitting the predicted limb to the observed one is therefore a
*timing* measurement, and a sharp one -- which is exactly how the published
JunoCam pipelines (Eichstaedt's, as described by Orton et al. 2017 and
Tabataba-Vakili et al. 2020) refine their pointing, by hand.

Three pieces live here, all free of SPICE: the sub-pixel *predicted* limb read
off the ellipsoid-intercept discriminant, the *observed* limb read off the
image, and a golden-section search on the median absolute residual.  The
caller supplies a ``predict(dt)`` callable that redoes the geometry of the
limb framelets at a shifted epoch; nothing in this module knows how.

Two details are worth stating because they are choices, not consequences.

*The predicted limb is sub-pixel.*  The specification defines it as "the row
where the ray first hits the planet", which read off a boolean mask would
quantise to whole rows and turn the objective into a staircase that a
golden-section search cannot descend.  The intercept test is
``discriminant >= 0`` for the ray-ellipsoid quadratic, and the discriminant is
a smooth function of the row, so its zero crossing -- linearly interpolated
between the last row that misses and the first that hits -- is the same
boundary evaluated exactly.

*The observed limb is detected twice.*  The intensity levels that define the
edge come from the prediction (space = the framelet's predicted off-planet
median, planet = the median of the first twelve predicted on-planet rows), so
a prediction that starts tens of pixels off can pick its "planet" level out of
empty sky.  The fit therefore runs in two passes: detect against the nominal
timing, fit, re-detect against the fitted timing, fit again.  Residuals before
and after are reported on the *final* point set so that they compare the same
limb points at two epochs rather than two different sets of points.

What limits the residual, measured on the 28 refined images of perijove 4
day 033 (see ``docs/reports/junocam_pj4_geometry.md``):

* Each image's limb splits into two populations that a scanning camera cannot
  mix -- the edge the planet *enters* the strip by, seen in the first frames,
  and the edge it *leaves* by, seen in the last.  Over the 28 images the
  trailing population sits on the 1-bar ellipsoid to a median of 0.16 px;
  the leading one is a median of 2.1 px and up to 8.3 px outside it, further
  out in blue than in red and further out where the limb crosses high
  latitudes.  That is an apparent-limb-height effect -- scattering haze above
  the 1-bar surface detected as planet -- not a pointing error, which would
  move both edges the same way.  It is also why the median
  absolute residual is a slightly awkward objective: with the two populations
  balanced it settles between them, which is what makes one image of the set
  (``JNCR_2017033_04C00105``) sit at 1.5 px while the rest are near 0.5.
* Within one population the scatter is about 0.34 px MAD, and the residual
  drifts about -0.32 px per frame.  Two independent observables put the same
  number on that drift: the band registration of the reprojected image crosses
  zero at an ``INTERFRAME_DELTA`` near 2 ms rather than the kernel's 1 ms, and
  the red-blue shift moves about twice as fast with that parameter as the
  red-green shift does (2.0 and 3.0 on the two images swept) -- the ratio of
  their focal-plane separations, which is what a per-frame *rate* predicts and
  a wrong distortion centre does not.  The kernel's published value is what
  this module uses; the discrepancy is recorded, not corrected.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = [
    "COLUMN_MARGIN",
    "DAYSIDE_MAX_INCIDENCE_DEG",
    "DT_BOUNDS",
    "DT_TOL_S",
    "LIMB_COLUMN_STEP",
    "MIN_LIMB_POINTS",
    "PLANET_ROWS",
    "SMOOTH_ROWS",
    "LimbFit",
    "LimbPoints",
    "detect_observed_rows",
    "edge_rows",
    "golden_section",
    "limb_columns",
    "refine_dt",
    "smooth_columns",
]

#: Every 8th column of the strip carries a limb point (the specification's
#: subsample); the search cost is linear in this.
LIMB_COLUMN_STEP = 8
#: Columns nearer than this to either end of the photoactive area are dropped:
#: the distortion is largest there and the strip ends are where a framelet's
#: edge effects live.
COLUMN_MARGIN = 32
#: Rows of the column profile averaged before the edge is located.
SMOOTH_ROWS = 5
#: Rows just inside the limb whose median defines the local planet level.
PLANET_ROWS = 12
#: A limb point must be sunlit: incidence at the first on-planet pixel.
DAYSIDE_MAX_INCIDENCE_DEG = 85.0
#: Rows of space that must exist outside a usable edge.
SPACE_ROWS = 4
#: How far from the predicted row the observed crossing may be, in rows.  The
#: nominal timing can be a whole exposure half-width out (102 ms, 32 px, for a
#: 64-stage methane frame), so the window has to be generous.
SEARCH_ROWS = 48.0
#: Minimum planet-minus-space contrast, in units of the space level's own
#: robust scatter, for an edge to be trusted.
MIN_CONTRAST_SIGMA = 5.0
#: ... and an absolute floor in DN, for a framelet whose sky is noiseless.
MIN_CONTRAST_DN = 1.0
#: Search interval for the start-time offset, in seconds, and its tolerance.
DT_BOUNDS: tuple[float, float] = (-0.25, 0.25)
DT_TOL_S = 5e-4
#: Fewer points than this and the image is left unrefined.
MIN_LIMB_POINTS = 200
#: At most this many framelets enter the fit, spread evenly over the image, so
#: that a 126-framelet product costs the same as a 24-framelet one.
MAX_LIMB_FRAMELETS = 16
#: A trial epoch that keeps less than this fraction of the points is penalised
#: rather than rewarded for having thrown the hard points away.
MIN_VALID_FRACTION = 0.5

_GOLDEN = 0.5 * (3.0 - np.sqrt(5.0))  # 1 - 1/phi


# --------------------------------------------------------------------------
# columns and the predicted edge
# --------------------------------------------------------------------------
def limb_columns(
    photoactive: np.ndarray, step: int = LIMB_COLUMN_STEP, margin: int = COLUMN_MARGIN
) -> np.ndarray:
    """Subsampled photoactive columns, clear of both ends of the strip."""
    columns = np.flatnonzero(np.asarray(photoactive, dtype=bool))
    if columns.size <= 2 * margin:
        return np.empty(0, dtype=np.intp)
    columns = columns[margin : columns.size - margin]
    return columns[:: int(step)].astype(np.intp)


def edge_rows(discriminant: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sub-pixel rows where a column enters and leaves the planet.

    ``discriminant`` is the ray-ellipsoid quadratic's discriminant on a
    ``(..., rows, columns)`` grid: non-negative where the ray hits.  Returns
    ``(top, bottom)``, each ``(..., columns)``, the interpolated zero crossings
    of the first entry from above and the last exit downwards, NaN where the
    column has no such crossing (all sky, all planet, or a crossing pinned to
    the first or last row, where there is no space on the outside).
    """
    disc = np.asarray(discriminant, dtype=np.float64)
    inside = disc >= 0.0
    rows = disc.shape[-2]

    enter = inside[..., 1:, :] & ~inside[..., :-1, :]
    leave = inside[..., :-1, :] & ~inside[..., 1:, :]

    def _interpolate(flags: np.ndarray, index: np.ndarray) -> np.ndarray:
        low = np.take_along_axis(disc[..., :-1, :], index[..., None, :], axis=-2)[..., 0, :]
        high = np.take_along_axis(disc[..., 1:, :], index[..., None, :], axis=-2)[..., 0, :]
        span = high - low
        fraction = np.where(span != 0.0, -low / np.where(span != 0.0, span, 1.0), 0.5)
        row = index.astype(np.float64) + np.clip(fraction, 0.0, 1.0)
        return np.where(flags.any(axis=-2), row, np.nan)

    top = _interpolate(enter, np.argmax(enter, axis=-2))
    reversed_leave = leave[..., ::-1, :]
    last = (rows - 2) - np.argmax(reversed_leave, axis=-2)
    bottom = _interpolate(leave, last)
    return top, bottom


# --------------------------------------------------------------------------
# the observed edge
# --------------------------------------------------------------------------
def smooth_columns(strip: np.ndarray, window: int = SMOOTH_ROWS) -> np.ndarray:
    """Running mean of ``window`` rows down each column, edges truncated."""
    data = np.asarray(strip, dtype=np.float64)
    rows = data.shape[-2]
    half = int(window) // 2
    padded = np.concatenate(
        [
            np.repeat(data[..., :1, :], half, axis=-2),
            data,
            np.repeat(data[..., -1:, :], half, axis=-2),
        ],
        axis=-2,
    )
    cumulative = np.cumsum(padded, axis=-2)
    zero_shape = list(cumulative.shape)
    zero_shape[-2] = 1
    cumulative = np.concatenate([np.zeros(zero_shape), cumulative], axis=-2)
    total = cumulative[..., int(window) :, :] - cumulative[..., : -int(window), :]
    return total[..., :rows, :] / float(window)


@dataclass(frozen=True)
class LimbPoints:
    """One limb sample per (framelet, column, side)."""

    framelet: np.ndarray
    column: np.ndarray
    side: np.ndarray  # 0 = planet below the edge, 1 = planet above it
    observed_row: np.ndarray

    def __len__(self) -> int:  # pragma: no cover - trivial
        return int(self.framelet.size)


def _profile_crossings(profile: np.ndarray, threshold: float, side: int) -> np.ndarray:
    """Sub-pixel rows where a smoothed column profile crosses ``threshold``."""
    delta = profile - threshold
    above = delta >= 0.0
    if side == 0:
        found = np.flatnonzero(above[1:] & ~above[:-1])
    else:
        found = np.flatnonzero(above[:-1] & ~above[1:])
    if found.size == 0:
        return np.empty(0, dtype=np.float64)
    low = delta[found]
    high = delta[found + 1]
    span = high - low
    fraction = np.where(span != 0.0, -low / np.where(span != 0.0, span, 1.0), 0.5)
    return found + np.clip(fraction, 0.0, 1.0)


def detect_observed_rows(
    strips: np.ndarray,
    inside: np.ndarray,
    framelet: np.ndarray,
    column: np.ndarray,
    side: np.ndarray,
    predicted: np.ndarray,
    *,
    smooth: int = SMOOTH_ROWS,
    planet_rows: int = PLANET_ROWS,
    search_rows: float = SEARCH_ROWS,
) -> np.ndarray:
    """Observed limb row of every candidate point, NaN where none is credible.

    ``strips`` is ``(n_framelets, rows, n_columns)`` of image values on the
    subsampled columns and ``inside`` the predicted on-planet mask of the same
    shape.  ``framelet``/``column``/``side`` index into it; ``column`` indexes
    the subsample, not the detector.
    """
    strips = np.asarray(strips, dtype=np.float64)
    inside = np.asarray(inside, dtype=bool)
    smoothed = smooth_columns(strips, smooth)
    rows = strips.shape[-2]

    space_level = np.full(strips.shape[0], np.nan)
    space_scale = np.full(strips.shape[0], np.nan)
    for index in range(strips.shape[0]):
        sky = strips[index][~inside[index]]
        if sky.size >= 16:
            level = float(np.median(sky))
            space_level[index] = level
            space_scale[index] = 1.4826 * float(np.median(np.abs(sky - level)))

    observed = np.full(framelet.size, np.nan)
    for point in range(framelet.size):
        frame = int(framelet[point])
        col = int(column[point])
        edge = float(predicted[point])
        if not np.isfinite(edge) or not np.isfinite(space_level[frame]):
            continue
        profile = smoothed[frame, :, col]
        if side[point] == 0:
            first = int(np.ceil(edge))
            window = profile[first : first + planet_rows]
        else:
            last = int(np.floor(edge)) + 1
            window = profile[max(last - planet_rows, 0) : last]
        if window.size < planet_rows:
            continue
        planet_level = float(np.median(window))
        contrast = planet_level - space_level[frame]
        floor = max(MIN_CONTRAST_DN, MIN_CONTRAST_SIGMA * float(np.nan_to_num(space_scale[frame])))
        if not np.isfinite(contrast) or contrast < floor:
            continue
        threshold = 0.5 * (planet_level + space_level[frame])
        crossings = _profile_crossings(profile, threshold, int(side[point]))
        if crossings.size == 0:
            continue
        distance = np.abs(crossings - edge)
        best = int(np.argmin(distance))
        if distance[best] > search_rows:
            continue
        if not 0.0 <= crossings[best] <= rows - 1.0:
            continue
        observed[point] = crossings[best]
    return observed


# --------------------------------------------------------------------------
# the fit
# --------------------------------------------------------------------------
def golden_section(func, low: float, high: float, tol: float) -> tuple[float, float]:
    """Golden-section minimum of a unimodal ``func`` on ``[low, high]``.

    Returns ``(x, f(x))`` for the best point evaluated, with the bracket
    narrowed below ``tol``.  The residual-versus-offset curve is a V: every
    limb point moves by the same amount when the epoch shifts, so the median
    absolute residual falls to a single minimum and rises again.
    """
    a, b = float(low), float(high)
    c = b - (b - a) * (1.0 - _GOLDEN)
    d = a + (b - a) * (1.0 - _GOLDEN)
    fc, fd = func(c), func(d)
    best_x, best_f = (c, fc) if fc <= fd else (d, fd)
    while abs(b - a) > tol:
        if fc <= fd:
            b, d, fd = d, c, fc
            c = b - (b - a) * (1.0 - _GOLDEN)
            fc = func(c)
            if fc < best_f:
                best_x, best_f = c, fc
        else:
            a, c, fc = c, d, fd
            d = a + (b - a) * (1.0 - _GOLDEN)
            fd = func(d)
            if fd < best_f:
                best_x, best_f = d, fd
    return best_x, best_f


def residual_statistic(
    observed: np.ndarray, predicted: np.ndarray, n_reference: int
) -> tuple[float, int]:
    """Median absolute residual and its point count, with a drop-out penalty.

    A trial epoch that pushes most of the limb off the strip would otherwise
    score well on the handful of points it kept, so a fit that retains less
    than half of the reference points is charged for what it discarded.
    """
    residual = np.asarray(observed, dtype=np.float64) - np.asarray(predicted, dtype=np.float64)
    valid = np.isfinite(residual)
    count = int(valid.sum())
    if count == 0:
        return float(1e6), 0
    statistic = float(np.median(np.abs(residual[valid])))
    minimum = max(1, int(MIN_VALID_FRACTION * max(n_reference, 1)))
    if count < minimum:
        statistic += 1e3 * (1.0 - count / minimum)
    return statistic, count


@dataclass
class LimbFit:
    """The outcome of one image's start-time refinement."""

    dt_refined_s: float
    limb_residual_px_before: float
    limb_residual_px_after: float
    n_limb_points: int
    refined: bool
    n_limb_framelets: int = 0
    reason: str = ""
    residuals: np.ndarray = field(default_factory=lambda: np.empty(0))

    def summary(self) -> dict:
        return {
            "dt_refined_s": float(self.dt_refined_s),
            "limb_residual_px_before": float(self.limb_residual_px_before),
            "limb_residual_px_after": float(self.limb_residual_px_after),
            "n_limb_points": int(self.n_limb_points),
            "n_limb_framelets": int(self.n_limb_framelets),
            "refined": bool(self.refined),
            "reason": self.reason,
        }


def refine_dt(
    predict,
    detect,
    *,
    bounds: tuple[float, float] = DT_BOUNDS,
    tol: float = DT_TOL_S,
    passes: int = 2,
    min_points: int = MIN_LIMB_POINTS,
) -> LimbFit:
    """Fit the start-time offset that best puts the predicted limb on the observed one.

    ``predict(dt)`` returns the predicted limb row of every candidate point at
    offset ``dt``; ``detect(dt)`` returns the observed rows detected against
    the prediction at that same offset.  Two passes: the first detects against
    the nominal timing, the second against the fitted timing, which is what
    stops a large initial error from reading the "planet" level out of empty
    sky.
    """
    nominal = predict(0.0)
    dt = 0.0
    observed = detect(0.0)
    for _ in range(max(1, int(passes))):
        reference = int(np.isfinite(observed).sum())
        if reference < min_points:
            break

        def objective(trial: float) -> float:
            return residual_statistic(observed, predict(trial), reference)[0]

        dt, _ = golden_section(objective, bounds[0], bounds[1], tol)
        observed = detect(dt)

    residual_after = observed - predict(dt)
    residual_before = observed - nominal
    valid = np.isfinite(residual_after) & np.isfinite(residual_before)
    count = int(valid.sum())
    if count < min_points:
        return LimbFit(
            dt_refined_s=float("nan"),
            limb_residual_px_before=(
                float(np.median(np.abs(residual_before[valid]))) if count else float("nan")
            ),
            limb_residual_px_after=(
                float(np.median(np.abs(residual_after[valid]))) if count else float("nan")
            ),
            n_limb_points=count,
            refined=False,
            reason=f"only {count} limb points (need {min_points})",
            residuals=residual_after[valid],
        )
    return LimbFit(
        dt_refined_s=float(dt),
        limb_residual_px_before=float(np.median(np.abs(residual_before[valid]))),
        limb_residual_px_after=float(np.median(np.abs(residual_after[valid]))),
        n_limb_points=count,
        refined=True,
        residuals=residual_after[valid],
    )
