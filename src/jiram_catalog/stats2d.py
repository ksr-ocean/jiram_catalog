"""Masked two-dimensional statistics for JIRAM strips.

A strip is one pass' swath resampled onto a regular square grid: a field with
holes.  Every diagnostic in this module -- the two-dimensional periodogram and
its shell spectrum, the one-dimensional spectra along rows and columns, the
structure functions, the bicoherence -- has to survive those holes, and the
whole design follows from that one fact.

The recipe is the same everywhere.  The mean is taken over the valid pixels
only; the invalid pixels are then set to ``fill`` (zero by default), which is
the mean, so filling adds no signal and no discontinuity beyond the edge of the
hole itself; a separable taper is applied to the full canvas so that the finite
strip does not ring; and the resulting periodogram is divided by
``valid_frac * window_power`` so that the total, at first order, is the variance
of the underlying field rather than the variance of the punctured one.

That correction is exact only in an idealised sense and the caller should know
where it fails.  For a zero-mean stationary field ``f``, a mask ``m`` and a
window ``w`` the windowed, filled field has mean square
``<f^2 m w^2> ~= var(f) * <m w^2>``, whereas the divisor used here is
``<m> * <w^2>``.  The two agree only when the mask is uncorrelated with the
taper, so a strip whose holes sit preferentially in the tapered border is biased
in total variance at the level of that correlation; the exactly unbiased divisor
would be ``mean(m * w**2)``, which the spec deliberately does not use.  The
second and larger caveat is spectral: masking convolves the true spectrum with
the mask's own power spectrum, which moves variance across ``k`` even when the
total is right.  Smooth, few, large holes have a red mask spectrum and distort
the slope of a power law only mildly; speckle masks do not, and no scalar
correction can repair them.  ``correction="none"`` returns the raw periodogram
for callers who want to do their own accounting.

Conventions
-----------
This module follows the canonical numerical conventions of
``~/scratch/goflow_rnd/docs/conventions.md``, version tag ``conventions-v1``
(2026-09-01), and the following items are adopted verbatim:

* arrays are C-ordered with spatial axes last, ``(ny, nx)``, the row index
  increasing with ``y``; all arithmetic here is ``float64``/``complex128``
  (conventions section 1);
* SI units, metres, square pixels ``dx = dy``, ``Lx = nx*dx`` (section 2);
* the forward transform is ``np.fft.fft2(f, norm="forward")``, so that
  coefficients are physical amplitudes and Parseval reads
  ``mean(f^2) = sum_k |f_hat[k]|^2`` (section 3);
* wavenumbers are angular, in radians per metre,
  ``kx = 2*pi*np.fft.fftfreq(nx, dx)``, with fundamental ``dk = 2*pi/L`` and
  Nyquist ``k_nyq = pi/dx`` (section 3);
* shell ``j`` holds the lattice modes with ``round(|k|/dk) == j``, its centre is
  ``k_j = j*dk`` and its population ``N_j`` is counted on the actual lattice,
  never assumed proportional to ``k`` (section 4);
* the realized shell spectrum is ``E_j = (1/dk) * sum_{k in S_j} <power>``, so
  that ``sum_j E_j*dk`` is the variance (section 4).

Two departures from the conventions document are deliberate and are the spec's:

1. section 4 defines ``E`` with the kinetic-energy factor ``0.5*|u_hat|^2``;
   here the field is a radiance, not a velocity, and ``E`` integrates to the
   *variance*, so the factor is absent.
2. section 4 reports shells only inside the inscribed disc ``k_j <= k_nyq`` and
   drops the partial corner shells.  Dropping them would break Parseval, and the
   spec asks for a population counting that "covers every lattice point", so
   every shell out to the corner of the lattice is returned and the inscribed
   disc is flagged instead, as the boolean ``inside_disc``.  Slopes and any
   normalisation should still be taken over ``inside_disc`` only.

Public API
----------
``power_spectrum_2d``, ``isotropic_spectrum``, ``spectrum_1d``,
``structure_function``, ``BicoherenceAccumulator``, ``strip_statistics``,
``population_statistics``, plus ``add_subparser``/``run`` for the
``strip-stats`` subcommand.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np
import xarray as xr

__all__ = [
    "BicoherenceAccumulator",
    "CONVENTIONS_VERSION",
    "add_subparser",
    "isotropic_spectrum",
    "population_statistics",
    "power_spectrum_2d",
    "run",
    "spectrum_1d",
    "strip_statistics",
    "structure_function",
]

CONVENTIONS_VERSION = "conventions-v1"

LOGGER = logging.getLogger(__name__)


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _as_field(field: Any) -> np.ndarray:
    """A two-dimensional ``float64`` view of anything array-like."""
    array = np.asarray(getattr(field, "values", field), dtype=np.float64)
    if array.ndim != 2:
        raise ValueError(f"field must be 2-D (ny, nx); got shape {array.shape}")
    return array


def _prepare(field: Any, valid: Any | None) -> tuple[np.ndarray, np.ndarray]:
    """The field as ``float64`` and the mask of pixels that are usable.

    A pixel is usable when it is finite *and* flagged by ``valid``; NaN is always
    invalid whatever the flag says, which is what the strip layout intends (its
    ``image`` is NaN wherever no frame painted).
    """
    array = _as_field(field)
    mask = np.isfinite(array)
    if valid is not None:
        flag = np.asarray(getattr(valid, "values", valid))
        if flag.shape != array.shape:
            raise ValueError(f"valid {flag.shape} does not match field {array.shape}")
        mask &= flag.astype(bool)
    return array, mask


def _window_1d(window: str | None, n: int, alpha: float) -> np.ndarray:
    """A symmetric one-dimensional taper of length ``n``.

    ``None``, ``"none"``, ``"boxcar"`` and ``"rect"`` all mean no taper.
    ``"tukey"`` is the cosine-tapered rectangle whose taper occupies a fraction
    ``alpha`` of the length (``alpha = 0`` is a boxcar, ``alpha = 1`` is Hann);
    ``"hann"`` is Tukey with ``alpha = 1``.
    """
    if n < 1:
        raise ValueError("window length must be positive")
    name = "none" if window is None else str(window).lower()
    if name in {"none", "boxcar", "rect", "rectangular"}:
        return np.ones(n, dtype=np.float64)
    if name == "hann":
        alpha = 1.0
        name = "tukey"
    if name != "tukey":
        raise ValueError(f"unknown window {window!r}; use tukey, hann or None")
    if n == 1:
        return np.ones(1, dtype=np.float64)
    a = float(np.clip(alpha, 0.0, 1.0))
    if a <= 0.0:
        return np.ones(n, dtype=np.float64)
    x = np.arange(n, dtype=np.float64) / (n - 1)
    w = np.ones(n, dtype=np.float64)
    low = x < a / 2.0
    high = x > 1.0 - a / 2.0
    w[low] = 0.5 * (1.0 + np.cos(2.0 * np.pi / a * (x[low] - a / 2.0)))
    w[high] = 0.5 * (1.0 + np.cos(2.0 * np.pi / a * (x[high] - 1.0 + a / 2.0)))
    return w


def _detrended(
    array: np.ndarray, mask: np.ndarray, detrend: str | None, fill: float
) -> np.ndarray:
    """Remove the valid-pixel mean (or nothing) and set the invalid pixels."""
    name = "none" if detrend is None else str(detrend).lower()
    if name not in {"none", "mean"}:
        raise ValueError(f"unknown detrend {detrend!r}; use 'mean' or 'none'")
    offset = 0.0
    if name == "mean" and mask.any():
        offset = float(array[mask].mean())
    return np.where(mask, array - offset, float(fill))


def _rfft_fold(n: int) -> np.ndarray:
    """Weights that fold the negative frequencies onto the ``rfft`` half-line."""
    fold = np.full(n // 2 + 1, 2.0)
    fold[0] = 1.0
    if n % 2 == 0:
        fold[-1] = 1.0
    return fold


# --------------------------------------------------------------------------
# two-dimensional periodogram
# --------------------------------------------------------------------------
def power_spectrum_2d(
    field: Any,
    valid: Any | None = None,
    *,
    dx_m: float,
    window: str | None = "tukey",
    alpha: float = 0.25,
    detrend: str | None = "mean",
    fill: float = 0.0,
    correction: str = "valid_frac",
) -> dict[str, Any]:
    """The masked two-dimensional periodogram of one field, ``fftshift``ed.

    The field is detrended over its valid pixels, the invalid pixels are set to
    ``fill``, a separable taper is applied to the whole canvas, and the forward
    transform of the conventions document (``norm="forward"``) is squared.  With
    ``correction="valid_frac"`` the result is divided by
    ``valid_frac * window_power`` so that a fully valid, untapered field obeys
    Parseval exactly and a masked one is unbiased in total variance to first
    order; see the module docstring for the residual bias.  ``correction="none"``
    returns the raw periodogram.

    Returns a dict with ``power`` ``(ny, nx)``, the angular wavenumber axes
    ``kx``, ``ky`` in rad/m (both ``fftshift``ed, so that ``power[j, i]`` belongs
    to ``(ky[j], kx[i])``), the mean square ``variance`` of the windowed, filled
    field, ``valid_frac``, ``window_power`` (the mean of ``window**2`` over the
    grid) and a ``meta`` record of the options.
    """
    array, mask = _prepare(field, valid)
    ny, nx = array.shape
    dx = float(dx_m)
    if not np.isfinite(dx) or dx <= 0.0:
        raise ValueError(f"dx_m must be a positive length in metres; got {dx_m!r}")

    valid_frac = float(mask.mean())
    filled = _detrended(array, mask, detrend, fill)

    wy = _window_1d(window, ny, alpha)
    wx = _window_1d(window, nx, alpha)
    taper = wy[:, None] * wx[None, :]
    window_power = float(np.mean(taper * taper))
    windowed = filled * taper
    variance = float(np.mean(windowed * windowed))

    spectrum = np.fft.fft2(windowed, norm="forward")
    power = spectrum.real**2 + spectrum.imag**2

    name = "none" if correction is None else str(correction).lower()
    if name not in {"none", "valid_frac"}:
        raise ValueError(f"unknown correction {correction!r}")
    if name == "valid_frac":
        denominator = valid_frac * window_power
        if denominator <= 0.0:
            power = np.full_like(power, np.nan)
        else:
            power = power / denominator

    return {
        "power": np.fft.fftshift(power),
        "kx": np.fft.fftshift(2.0 * np.pi * np.fft.fftfreq(nx, d=dx)),
        "ky": np.fft.fftshift(2.0 * np.pi * np.fft.fftfreq(ny, d=dx)),
        "variance": variance,
        "valid_frac": valid_frac,
        "window_power": window_power,
        "meta": {
            "shape": (ny, nx),
            "dx_m": dx,
            "window": window,
            "alpha": float(alpha),
            "detrend": detrend,
            "fill": float(fill),
            "correction": name,
            "conventions": CONVENTIONS_VERSION,
        },
    }


def isotropic_spectrum(spec: dict[str, Any], *, nbins: int | None = None) -> dict[str, Any]:
    """Shell-integrate a two-dimensional periodogram onto ``E(k)``.

    Shells follow the conventions document: shell ``j`` holds the lattice modes
    with ``round(|k|/dk) == j``, its centre is ``k_j = j*dk``, its population is
    counted on the lattice, and ``E_j = (1/dk) * sum_{k in S_j} power``.  The
    default bin width is the fundamental of the longer side,
    ``dk = 2*pi/(max(nx, ny)*dx)``; ``nbins`` instead spreads that many shells
    over the whole lattice, out to the corner.

    Every lattice point falls in exactly one returned shell, so
    ``sum(E*dk) == sum(power)`` to rounding.  The corner shells are partial and
    the boolean ``inside_disc`` marks the ones inside ``k <= k_nyq``; take slopes
    and normalisations there.
    """
    power = np.asarray(spec["power"], dtype=np.float64)
    kx = np.asarray(spec["kx"], dtype=np.float64)
    ky = np.asarray(spec["ky"], dtype=np.float64)
    ny, nx = power.shape
    if kx.shape != (nx,) or ky.shape != (ny,):
        raise ValueError("kx/ky do not match the shape of power")

    dkx = float(kx[1] - kx[0]) if nx > 1 else 0.0
    dky = float(ky[1] - ky[0]) if ny > 1 else 0.0
    length_x = 2.0 * np.pi / dkx if dkx else np.inf
    length_y = 2.0 * np.pi / dky if dky else np.inf
    modulus = np.hypot(ky[:, None], kx[None, :])
    k_max = float(modulus.max())
    # the inscribed disc: the smaller of the two Nyquist wavenumbers
    k_nyq = float(min(np.abs(kx).max(), np.abs(ky).max())) if nx > 1 and ny > 1 else k_max

    if nbins is None:
        dk = 2.0 * np.pi / max(length_x, length_y)
        index = np.rint(modulus / dk).astype(np.int64)
        n_shells = int(index.max()) + 1
    else:
        n_shells = int(nbins)
        if n_shells < 2:
            raise ValueError("nbins must be at least 2")
        dk = k_max / (n_shells - 0.5)
        index = np.rint(modulus / dk).astype(np.int64)
    np.clip(index, 0, n_shells - 1, out=index)

    flat = index.ravel()
    count = np.bincount(flat, minlength=n_shells).astype(np.int64)
    shell_sum = np.bincount(flat, weights=power.ravel(), minlength=n_shells)
    k = np.arange(n_shells, dtype=np.float64) * dk
    energy = shell_sum / dk
    energy[count == 0] = np.nan
    return {
        "k": k,
        "E": energy,
        "count": count,
        "dk": float(dk),
        "k_nyq": k_nyq,
        "inside_disc": k <= k_nyq,
    }


# --------------------------------------------------------------------------
# one-dimensional spectra
# --------------------------------------------------------------------------
def spectrum_1d(
    field: Any,
    valid: Any | None = None,
    *,
    dx_m: float,
    axis: int,
    window: str | None = "tukey",
    alpha: float = 0.25,
    min_valid_frac: float = 0.9,
) -> dict[str, Any]:
    """One-dimensional spectra along ``axis``, averaged over the usable lines.

    ``axis=1`` takes the rows (lines along ``x``), ``axis=0`` the columns.  A
    line enters the average only if at least ``min_valid_frac`` of it is valid;
    each accepted line is detrended by its own valid-pixel mean, its holes set to
    zero, tapered, transformed, and divided by ``line_valid_frac *
    window_power``.

    ``k`` is the non-negative half-line ``2*pi*rfftfreq(n, dx)`` and ``P`` folds
    the negative frequencies onto it, so that ``sum(P)`` is the mean square of a
    fully valid tapered line.  ``n_lines`` is how many lines survived.
    """
    array, mask = _prepare(field, valid)
    if axis not in (0, 1):
        raise ValueError("axis must be 0 (columns) or 1 (rows)")
    if axis == 0:
        array = array.T
        mask = mask.T
    n_lines_total, n = array.shape
    dx = float(dx_m)
    if not np.isfinite(dx) or dx <= 0.0:
        raise ValueError(f"dx_m must be a positive length in metres; got {dx_m!r}")

    taper = _window_1d(window, n, alpha)
    window_power = float(np.mean(taper * taper))
    fraction = mask.mean(axis=1)
    keep = fraction >= float(min_valid_frac)
    n_kept = int(keep.sum())
    k = 2.0 * np.pi * np.fft.rfftfreq(n, d=dx)

    if n_kept == 0:
        return {
            "k": k,
            "P": np.full(k.size, np.nan),
            "n_lines": 0,
            "n_lines_total": int(n_lines_total),
            "window_power": window_power,
            "meta": {"axis": int(axis), "dx_m": dx, "window": window, "alpha": float(alpha),
                     "min_valid_frac": float(min_valid_frac),
                     "conventions": CONVENTIONS_VERSION},
        }

    lines = array[keep]
    flags = mask[keep]
    counts = flags.sum(axis=1)
    means = np.where(flags, lines, 0.0).sum(axis=1) / np.maximum(counts, 1)
    prepared = np.where(flags, lines - means[:, None], 0.0) * taper
    coefficients = np.fft.rfft(prepared, axis=1, norm="forward")
    power = (coefficients.real**2 + coefficients.imag**2) * _rfft_fold(n)
    power /= (fraction[keep][:, None] * window_power)
    return {
        "k": k,
        "P": power.mean(axis=0),
        "n_lines": n_kept,
        "n_lines_total": int(n_lines_total),
        "window_power": window_power,
        "meta": {"axis": int(axis), "dx_m": dx, "window": window, "alpha": float(alpha),
                 "min_valid_frac": float(min_valid_frac),
                 "conventions": CONVENTIONS_VERSION},
    }


# --------------------------------------------------------------------------
# structure functions
# --------------------------------------------------------------------------
def _shift_pair(
    work: np.ndarray, mask: np.ndarray, lag: int, along_x: bool
) -> tuple[np.ndarray, np.ndarray]:
    """The increment and the joint validity at one lag, as whole-array slices."""
    if along_x:
        if lag >= work.shape[1]:
            return np.empty((0,)), np.zeros((0,), dtype=bool)
        return work[:, lag:] - work[:, :-lag], mask[:, lag:] & mask[:, :-lag]
    if lag >= work.shape[0]:
        return np.empty((0,)), np.zeros((0,), dtype=bool)
    return work[lag:, :] - work[:-lag, :], mask[lag:, :] & mask[:-lag, :]


def structure_function(
    field: Any,
    valid: Any | None = None,
    *,
    dx_m: float,
    orders: Sequence[float] = (2, 3),
    max_lag_px: int = 64,
    direction: str = "isotropic",
) -> dict[str, Any]:
    """Masked structure functions ``S_p(r) = < (f(x+r) - f(x))^p >``.

    Only pairs whose two members are both valid contribute, which is the whole
    difficulty: the mask depends on the lag, so the estimate at each lag is an
    average over a different set of pairs.  The increments are formed as shifted
    whole-array slices, one pass per lag, never pixel by pixel.

    Odd integer orders keep their sign -- ``S_3`` is the signed third-order
    structure function, not ``<|df|^3>`` -- while a non-integer order uses
    ``|df|^p``.  ``direction`` is ``"x"``, ``"y"`` or ``"isotropic"``; the
    isotropic estimate pools the ``x`` and ``y`` pairs at equal lag (a
    count-weighted average of the two, which is the same as the plain average
    when the canvas is square and is the honest one when it is not).

    Returns ``r_m`` (lags 1..``max_lag_px``, in metres), ``S`` keyed by order,
    and ``count``, the number of contributing pairs.  Lags with no pair at all
    give NaN.
    """
    array, mask = _prepare(field, valid)
    dx = float(dx_m)
    if not np.isfinite(dx) or dx <= 0.0:
        raise ValueError(f"dx_m must be a positive length in metres; got {dx_m!r}")
    kind = str(direction).lower()
    if kind not in {"x", "y", "isotropic"}:
        raise ValueError("direction must be 'x', 'y' or 'isotropic'")
    n_lag = int(max_lag_px)
    if n_lag < 1:
        raise ValueError("max_lag_px must be at least 1")
    powers = [int(p) if float(p).is_integer() else float(p) for p in orders]

    work = np.where(mask, array, 0.0)
    axes = {"x": (True,), "y": (False,), "isotropic": (True, False)}[kind]

    lags = np.arange(1, n_lag + 1, dtype=np.int64)
    count = np.zeros(n_lag, dtype=np.int64)
    totals = {p: np.zeros(n_lag, dtype=np.float64) for p in powers}
    for position, lag in enumerate(lags):
        for along_x in axes:
            increment, joint = _shift_pair(work, mask, int(lag), along_x)
            if joint.size == 0 or not joint.any():
                continue
            selected = increment[joint]
            count[position] += selected.size
            for p in powers:
                moment = selected**p if isinstance(p, int) else np.abs(selected) ** p
                totals[p][position] += float(moment.sum())

    empty = count == 0
    structure: dict[Any, np.ndarray] = {}
    for p in powers:
        value = np.divide(
            totals[p], count, out=np.full(n_lag, np.nan), where=~empty
        )
        structure[p] = value
    return {
        "r_m": lags.astype(np.float64) * dx,
        "S": structure,
        "count": count,
        "meta": {"direction": kind, "dx_m": dx, "orders": tuple(powers),
                 "max_lag_px": n_lag, "conventions": CONVENTIONS_VERSION},
    }


# --------------------------------------------------------------------------
# bicoherence
# --------------------------------------------------------------------------
class BicoherenceAccumulator:
    """Segment-averaged squared bicoherence of one-dimensional lines.

    The estimator is the standard normalised one,

    ``b2(k1, k2) = |sum_s X_s(k1) X_s(k2) X_s*(k1+k2)|^2
                   / (sum_s |X_s(k1) X_s(k2)|^2 * sum_s |X_s(k1+k2)|^2)``,

    which Cauchy-Schwarz confines to ``[0, 1]``: it is one when the phase of the
    sum mode is locked to the sum of the phases of the two parent modes in every
    segment, and it falls off as ``1/n_segments`` when that phase is random.  It
    is therefore a measure of phase coupling and not of amplitude, and it needs
    many segments before a small value means anything.

    Segments are accumulated one batch at a time; nothing but the three
    accumulators is kept.  Only ``k1, k2 >= 0`` with ``k1 + k2`` below the
    Nyquist wavenumber are computed, and the rest of the ``(k1, k2)`` plane is
    NaN.  Each segment is detrended over its valid pixels, its holes set to zero,
    and tapered (Hann by default).  A segment is used only if at least
    ``min_valid_frac`` of it is valid: unlike the spectra, the bicoherence has no
    scalar mask correction, because the mask breaks the phase relations it is
    trying to measure, so the defence is to reject punctured segments outright.
    """

    def __init__(self, n_fft: int, dx_m: float, *, window: str | None = "hann") -> None:
        n = int(n_fft)
        if n < 8 or n % 2:
            raise ValueError("n_fft must be an even integer of at least 8")
        dx = float(dx_m)
        if not np.isfinite(dx) or dx <= 0.0:
            raise ValueError(f"dx_m must be a positive length in metres; got {dx_m!r}")
        self.n_fft = n
        self.dx_m = dx
        self.window = window
        self._taper = _window_1d(window, n, 1.0)
        self._window_power = float(np.mean(self._taper * self._taper))
        self._half = n // 2
        half = self._half
        self._bispectrum = np.zeros((half, half), dtype=np.complex128)
        self._parents = np.zeros((half, half), dtype=np.float64)
        self._sum_mode = np.zeros(half + 1, dtype=np.float64)
        self._n_segments = 0
        j = np.arange(half)
        self._total = j[:, None] + j[None, :]
        self._triangle = self._total < half

    # -- accumulation ------------------------------------------------------
    @property
    def n_segments(self) -> int:
        """How many segments have been accepted so far."""
        return self._n_segments

    def add_lines(
        self,
        lines: np.ndarray,
        valid: np.ndarray | None = None,
        *,
        min_valid_frac: float = 0.95,
    ) -> int:
        """Accumulate ``(n, n_fft)`` segments; return how many were accepted."""
        block = np.atleast_2d(np.asarray(getattr(lines, "values", lines), dtype=np.float64))
        if block.ndim != 2 or block.shape[1] != self.n_fft:
            raise ValueError(f"lines must be (n, {self.n_fft}); got {block.shape}")
        mask = np.isfinite(block)
        if valid is not None:
            flag = np.atleast_2d(np.asarray(getattr(valid, "values", valid)))
            if flag.shape != block.shape:
                raise ValueError("valid does not match lines")
            mask &= flag.astype(bool)
        keep = mask.mean(axis=1) >= float(min_valid_frac)
        accepted = int(keep.sum())
        if accepted == 0:
            return 0

        segments = block[keep]
        flags = mask[keep]
        counts = flags.sum(axis=1)
        means = np.where(flags, segments, 0.0).sum(axis=1) / np.maximum(counts, 1)
        prepared = np.where(flags, segments - means[:, None], 0.0) * self._taper
        coefficients = np.fft.rfft(prepared, axis=1, norm="forward")

        half = self._half
        parents = coefficients[:, :half]
        magnitude = parents.real**2 + parents.imag**2
        # sum_s |X(k1)|^2 |X(k2)|^2 is an outer product summed over segments
        self._parents += magnitude.T @ magnitude
        # sum_s |X(k1+k2)|^2 depends only on the sum index, so one vector serves
        full = coefficients.real**2 + coefficients.imag**2
        self._sum_mode += full.sum(axis=0)
        # the bispectrum does not factor; walk the anti-diagonals k1 + k2 = c
        for c in range(half):
            product = parents[:, : c + 1] * parents[:, c::-1]
            contribution = (np.conj(coefficients[:, c])[:, None] * product).sum(axis=0)
            rows = np.arange(c + 1)
            self._bispectrum[rows, c - rows] += contribution
        self._n_segments += accepted
        return accepted

    def add_field(
        self,
        field: Any,
        valid: Any | None = None,
        *,
        axis: int = 1,
        step: int | None = None,
    ) -> int:
        """Cut every line along ``axis`` into segments and accumulate them.

        ``step`` defaults to ``n_fft``, i.e. non-overlapping segments; a smaller
        step gives overlapping ones (which are not independent, so the
        ``1/n_segments`` noise floor no longer applies).
        """
        array, mask = _prepare(field, valid)
        if axis not in (0, 1):
            raise ValueError("axis must be 0 (columns) or 1 (rows)")
        if axis == 0:
            array = array.T
            mask = mask.T
        n = array.shape[1]
        stride = self.n_fft if step is None else int(step)
        if stride < 1:
            raise ValueError("step must be at least 1")
        starts = range(0, n - self.n_fft + 1, stride)
        pieces = [array[:, s: s + self.n_fft] for s in starts]
        if not pieces:
            return 0
        flags = [mask[:, s: s + self.n_fft] for s in starts]
        return self.add_lines(np.concatenate(pieces, axis=0), np.concatenate(flags, axis=0))

    # -- result ------------------------------------------------------------
    def result(self) -> dict[str, Any]:
        """``k1``, ``k2``, ``b2``, ``n_segments`` and the mean ``bispectrum``."""
        half = self._half
        k = 2.0 * np.pi * np.fft.rfftfreq(self.n_fft, d=self.dx_m)[:half]
        sum_power = np.where(self._triangle, self._sum_mode[np.minimum(self._total, half)], np.nan)
        denominator = self._parents * sum_power
        numerator = np.abs(self._bispectrum) ** 2
        with np.errstate(invalid="ignore", divide="ignore"):
            b2 = np.where(denominator > 0.0, numerator / denominator, np.nan)
        b2 = np.where(self._triangle, b2, np.nan)
        scale = max(self._n_segments, 1)
        return {
            "k1": k,
            "k2": k.copy(),
            "b2": b2,
            "n_segments": self._n_segments,
            "bispectrum": np.where(self._triangle, self._bispectrum / scale, np.nan + 0j),
            "meta": {"n_fft": self.n_fft, "dx_m": self.dx_m, "window": self.window,
                     "conventions": CONVENTIONS_VERSION},
        }


# --------------------------------------------------------------------------
# strip-level and population-level products
# --------------------------------------------------------------------------
STRIP_ATTRS = ("strip_id", "km_per_px", "band", "orbit", "time_mid", "valid_frac")


def _strip_arrays(dataset: xr.Dataset) -> tuple[np.ndarray, np.ndarray, float]:
    """The image, its validity mask and the pixel size in metres."""
    if "image" not in dataset:
        raise KeyError("strip dataset has no 'image' variable")
    image = np.asarray(dataset["image"].values, dtype=np.float64)
    if "valid" in dataset:
        valid = np.asarray(dataset["valid"].values).astype(bool)
    else:
        valid = np.isfinite(image)
    km_per_px = dataset.attrs.get("km_per_px")
    if km_per_px is None:
        raise KeyError("strip dataset has no 'km_per_px' attribute")
    return image, valid, float(km_per_px) * 1000.0


def select_band(ds: xr.Dataset, band: str | None = None) -> tuple[xr.Dataset, str | None]:
    """One band of a multi-band strip, and its name.

    A JIRAM strip has no ``band`` dimension and is returned unchanged; a
    JunoCam strip has one and the statistics are of one band at a time,
    because a spectrum of three co-registered colours stacked together is not
    a spectrum of anything.  ``band=None`` takes the first band, which is the
    order the label's ``FILTER_NAME`` gave.
    """
    if "band" not in ds.dims:
        return ds, ds.attrs.get("band")
    names = [str(value) for value in np.asarray(ds["band"].values)]
    if not names:
        raise KeyError("the strip has an empty band dimension")
    wanted = names[0] if band is None else str(band).upper()
    if wanted not in names:
        raise KeyError(f"unknown band {wanted!r}; the strip carries {names}")
    return ds.isel(band=names.index(wanted)), wanted


def strip_statistics(
    ds: xr.Dataset,
    band: str | None = None,
    *,
    norm: str | None = None,
    max_lag_px: int = 64,
) -> xr.Dataset:
    """The standard statistics of one strip, as a small Dataset.

    Isotropic (shell) spectrum on ``k``; one-dimensional spectra along ``x`` and
    along ``y`` on their own axes ``kx`` and ``ky`` (they differ whenever the
    canvas is not square); second- and third-order structure functions on ``r``,
    pooled over the two axes.  The strip's identifying attributes are copied
    over, along with the conventions tag.

    ``band`` selects one band of a multi-band (JunoCam) strip and is ignored
    by a strip that has only one; the band that was used is recorded in the
    result's ``band`` attribute either way.

    ``norm`` divides an illumination model out of the image before anything is
    transformed, and defaults to the strip's own ``norm_default`` attribute --
    ``lambert`` for JunoCam, ``none`` for JIRAM, which has none of it.  It
    matters more here than on screen: limb darkening is a smooth ramp across
    the whole swath, so it is a large-amplitude, low-wavenumber signal that
    sits under every decade of the spectrum below it and steepens the slope
    that would otherwise be measured.  The model that was used is recorded in
    the result's ``norm`` attribute, and the mask it leaves behind -- the night
    side is not a measurement -- is the mask the transforms see.
    """
    from .api.images import normalise_plane, parse_norm

    requested = norm if norm is not None and str(norm).strip() else ds.attrs.get(
        "norm_default", "none"
    )
    # ``norm_name``, not ``name``: the attribute copy below runs a ``for name
    # in STRIP_ATTRS`` loop, and a norm called ``valid_frac`` is a bug that
    # would show up only in the recorded metadata.
    norm_name, parameter = parse_norm(requested)
    ds, selected = select_band(ds, band)
    image, valid, dx_m = _strip_arrays(ds)
    if norm_name != "none":
        # ``none`` short-circuits rather than round-tripping through an
        # identity: the JIRAM spectra in the library were computed on exactly
        # these arrays, and a normalisation that does nothing must also change
        # nothing about the numbers.
        normalised, valid = normalise_plane(
            image,
            incidence=(
                np.asarray(ds["incidence"].values, dtype=np.float64)
                if "incidence" in ds
                else None
            ),
            emission=(
                np.asarray(ds["emission"].values, dtype=np.float64)
                if "emission" in ds
                else None
            ),
            valid=valid,
            norm=norm_name,
            parameter=parameter,
        )
        image = np.where(valid, normalised, 0.0)
    spec = power_spectrum_2d(image, valid, dx_m=dx_m)
    shells = isotropic_spectrum(spec)
    along_x = spectrum_1d(image, valid, dx_m=dx_m, axis=1)
    along_y = spectrum_1d(image, valid, dx_m=dx_m, axis=0)
    lag_cap = max(1, min(int(max_lag_px), image.shape[0] - 1, image.shape[1] - 1))
    sf = structure_function(
        image, valid, dx_m=dx_m, orders=(2, 3), max_lag_px=lag_cap, direction="isotropic"
    )

    out = xr.Dataset(
        data_vars={
            "E": ("k", shells["E"]),
            "count_k": ("k", shells["count"]),
            "inside_disc": ("k", shells["inside_disc"]),
            "P_x": ("kx", along_x["P"]),
            "P_y": ("ky", along_y["P"]),
            "S2": ("r", sf["S"][2]),
            "S3": ("r", sf["S"][3]),
            "count_r": ("r", sf["count"]),
        },
        coords={"k": shells["k"], "kx": along_x["k"], "ky": along_y["k"], "r": sf["r_m"]},
    )
    out["k"].attrs.update(units="rad m-1", long_name="angular wavenumber (shell centre)")
    out["kx"].attrs.update(units="rad m-1", long_name="angular wavenumber along x")
    out["ky"].attrs.update(units="rad m-1", long_name="angular wavenumber along y")
    out["r"].attrs.update(units="m", long_name="separation")
    out["E"].attrs.update(long_name="shell-integrated variance density", units="variance rad-1 m")
    out["S2"].attrs.update(long_name="second-order structure function")
    out["S3"].attrs.update(long_name="third-order structure function (signed)")

    for name in STRIP_ATTRS:
        if name in ds.attrs:
            out.attrs[name] = ds.attrs[name]
    if selected is not None:
        out.attrs["band"] = str(selected)
    out.attrs.update(
        norm=norm_name if not np.isfinite(parameter) else f"{norm_name}:{parameter:g}",
        conventions=CONVENTIONS_VERSION,
        dx_m=dx_m,
        dk=shells["dk"],
        k_nyq=shells["k_nyq"],
        variance=spec["variance"],
        spectrum_valid_frac=spec["valid_frac"],
        window_power=spec["window_power"],
        n_lines_x=along_x["n_lines"],
        n_lines_y=along_y["n_lines"],
        max_lag_px=lag_cap,
        rows=int(image.shape[0]),
        cols=int(image.shape[1]),
    )
    return out


def _open_strip(item: Any) -> tuple[xr.Dataset, bool]:
    """Accept a Dataset directly or a path to one; say whether to close it."""
    if isinstance(item, xr.Dataset):
        return item, False
    return xr.open_dataset(item, engine="netcdf4"), True


def _mean_and_error(stack: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """NaN-aware mean and standard error of the mean down the leading axis."""
    with np.errstate(invalid="ignore"):
        count = np.sum(np.isfinite(stack), axis=0)
        mean = np.where(count > 0, np.nanmean(stack, axis=0), np.nan)
        deviation = np.where(count > 1, np.nanstd(stack, axis=0, ddof=1), np.nan)
        error = deviation / np.sqrt(np.maximum(count, 1))
    return mean, error


def population_statistics(
    paths: Sequence[str | Path], *, n_fft: int = 256
) -> xr.Dataset:
    """Population statistics over strips of one resolution class.

    Every strip is reduced by :func:`strip_statistics`, the spectra of the later
    strips are interpolated onto the wavenumber grids of the first (the strips
    share a pixel size but not a canvas size, so their grids differ in spacing
    only through the canvas length), and the mean and standard error are taken
    across strips.  A single bicoherence is accumulated over every strip's rows,
    cut into non-overlapping segments of ``n_fft`` pixels along ``x``.

    ``paths`` may hold file paths or open Datasets; the mixture is allowed.  A
    strip whose ``km_per_px`` differs from the first one's is an error: this is a
    within-resolution-class product.
    """
    items = list(paths)
    if not items:
        raise ValueError("population_statistics needs at least one strip")

    reference: dict[str, np.ndarray] | None = None
    collected: dict[str, list[np.ndarray]] = {"E": [], "P_x": [], "P_y": [], "S2": [], "S3": []}
    km_per_px: float | None = None
    accumulator: BicoherenceAccumulator | None = None
    strip_ids: list[str] = []

    for item in items:
        dataset, owned = _open_strip(item)
        try:
            image, valid, dx_m = _strip_arrays(dataset)
            this_km = float(dataset.attrs["km_per_px"])
            if km_per_px is None:
                km_per_px = this_km
            elif not np.isclose(this_km, km_per_px):
                raise ValueError(
                    f"mixed resolution classes: {this_km} vs {km_per_px} km/px"
                )
            stats = strip_statistics(dataset)
            if reference is None:
                reference = {
                    "k": np.asarray(stats["k"].values, dtype=np.float64),
                    "kx": np.asarray(stats["kx"].values, dtype=np.float64),
                    "ky": np.asarray(stats["ky"].values, dtype=np.float64),
                    "r": np.asarray(stats["r"].values, dtype=np.float64),
                }
            for name, axis in (("E", "k"), ("P_x", "kx"), ("P_y", "ky"),
                               ("S2", "r"), ("S3", "r")):
                source = np.asarray(stats[axis].values, dtype=np.float64)
                values = np.asarray(stats[name].values, dtype=np.float64)
                collected[name].append(_interpolate(reference[axis], source, values))
            if accumulator is None:
                accumulator = BicoherenceAccumulator(n_fft, dx_m)
            accumulator.add_field(image, valid, axis=1)
            strip_ids.append(str(dataset.attrs.get("strip_id", f"strip{len(strip_ids):03d}")))
        finally:
            if owned:
                dataset.close()

    assert reference is not None and accumulator is not None
    bicoherence = accumulator.result()
    data_vars: dict[str, Any] = {}
    for name, axis in (("E", "k"), ("P_x", "kx"), ("P_y", "ky"), ("S2", "r"), ("S3", "r")):
        mean, error = _mean_and_error(np.vstack(collected[name]))
        data_vars[f"{name}_mean"] = (axis, mean)
        data_vars[f"{name}_se"] = (axis, error)
    data_vars["b2"] = (("k1", "k2"), bicoherence["b2"])
    out = xr.Dataset(
        data_vars=data_vars,
        coords={
            "k": reference["k"],
            "kx": reference["kx"],
            "ky": reference["ky"],
            "r": reference["r"],
            "k1": bicoherence["k1"],
            "k2": bicoherence["k2"],
        },
    )
    out.attrs.update(
        n_strips=len(items),
        km_per_px=float(km_per_px) if km_per_px is not None else float("nan"),
        n_fft=int(n_fft),
        n_segments=int(bicoherence["n_segments"]),
        conventions=CONVENTIONS_VERSION,
        strip_ids=",".join(strip_ids),
    )
    return out


def _interpolate(target: np.ndarray, source: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Linear interpolation onto ``target``, NaN outside the source range."""
    if source.shape == target.shape and np.allclose(source, target):
        return np.asarray(values, dtype=np.float64)
    finite = np.isfinite(values)
    if finite.sum() < 2:
        return np.full(target.shape, np.nan)
    out = np.interp(target, source[finite], values[finite], left=np.nan, right=np.nan)
    inside = (target >= source[finite][0]) & (target <= source[finite][-1])
    return np.where(inside, out, np.nan)


# --------------------------------------------------------------------------
# command line
# --------------------------------------------------------------------------
def _configure(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add the ``strip-stats`` options to ``parser`` (shared by both entries)."""
    parser.add_argument("--mirror", help="local mirror root")
    parser.add_argument("--orbits", default="all", metavar="SPEC",
                        help="all or e.g. 4,5,10-20")
    parser.add_argument(
        "--band",
        choices=["L", "M", "RED", "GREEN", "BLUE", "METHANE"],
        help="restrict to strips carrying this band, and reduce that band of a "
        "multi-band strip (default: the strip's first band)",
    )
    parser.add_argument("--resolution-class", type=float, metavar="KM",
                        help="only strips of this km_per_px class (required for --population)")
    parser.add_argument("--max-lag-px", type=int, default=64,
                        help="largest structure-function lag, in pixels")
    parser.add_argument("--n-fft", type=int, default=256,
                        help="segment length of the population bicoherence")
    parser.add_argument("--min-valid-frac", type=float, default=0.0,
                        help="skip strips below this valid fraction")
    parser.add_argument("--population", action="store_true",
                        help="also write the population product for the selected class")
    parser.add_argument("--out", help="output directory (default <mirror>/strips/stats)")
    parser.add_argument("--limit", type=int, help="process only the first K strips")
    parser.add_argument("-v", action="store_true", help="enable debug logging")
    parser.set_defaults(func=run)
    return parser


def add_subparser(subparsers: Any) -> None:
    """Register the ``strip-stats`` subcommand on an ``add_subparsers`` object."""
    parser = subparsers.add_parser(
        "strip-stats", help="spectra, structure functions and bicoherence of strips"
    )
    _configure(parser)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m jiram_catalog.stats2d",
        description="spectra, structure functions and bicoherence of strips",
    )
    return _configure(parser)


def run(args: argparse.Namespace) -> int:
    """Reduce every selected strip and, optionally, the population product."""
    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "v", False) else logging.INFO,
        format="%(message)s",
    )
    from .pds import mirror_root

    try:
        from . import strips as strips_module
    except ImportError as error:  # pragma: no cover - strips.py lands separately
        print(f"error: the strip library is not available yet ({error})")
        return 2

    root = mirror_root(getattr(args, "mirror", None))
    orbits: list[int] | None = None
    specification = getattr(args, "orbits", "all")
    if specification and str(specification).lower() != "all":
        from .cli import parse_orbits

        orbits = parse_orbits(str(specification))

    table = strips_module.load_strips(
        root,
        orbits=orbits,
        band=getattr(args, "band", None),
        resolution_max_km=getattr(args, "resolution_class", None),
        min_valid_frac=(args.min_valid_frac or None),
    )
    if getattr(args, "resolution_class", None) is not None:
        table = table[np.isclose(table["km_per_px"].to_numpy(float), args.resolution_class)]
    if getattr(args, "limit", None):
        table = table.head(int(args.limit))
    if len(table) == 0:
        print("no strip matches the selection")
        return 1

    out_dir = Path(args.out) if getattr(args, "out", None) else root / "strips" / "stats"
    out_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    paths: list[Path] = []
    for _, row in table.iterrows():
        source = root / str(row["path"])
        paths.append(source)
        dataset = strips_module.read_strip(root, str(row["strip_id"]))
        try:
            # A JunoCam strip has one spectrum per band, so the band goes in
            # the file name too; a JIRAM strip keeps the name it always had.
            banded = "band" in dataset.dims
            stats = strip_statistics(
                dataset, band=getattr(args, "band", None), max_lag_px=int(args.max_lag_px)
            )
        finally:
            dataset.close()
        suffix = f"__{stats.attrs['band']}" if banded else ""
        target = out_dir / f"{row['strip_id']}{suffix}_stats.nc"
        stats.to_netcdf(target, format="NETCDF4", engine="netcdf4")
        written.append(target)
        LOGGER.info("wrote %s", target)

    print(f"strips reduced: {len(written)} -> {out_dir}")
    if getattr(args, "population", False):
        population = population_statistics(paths, n_fft=int(args.n_fft))
        label = f"{args.band or 'all'}_{population.attrs['km_per_px']:g}km"
        target = out_dir / f"population_{label}.nc"
        population.to_netcdf(target, format="NETCDF4", engine="netcdf4")
        print(f"population product: {target} "
              f"(n_strips={population.attrs['n_strips']}, "
              f"n_segments={population.attrs['n_segments']})")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return run(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
