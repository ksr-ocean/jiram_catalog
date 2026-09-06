"""Offline tests for the classical tracker (no SPICE kernels, no network).

The synthetic pair is a band-limited random field and a Fourier-shifted copy of
itself, which gives a displacement that is exact by construction and a
correlation length that can be set deliberately.  The length matters: a template
only 15 px wide has to contain several correlation lengths for the correlation
peak to be sharp enough that a 3 x 3 parabola resolves it to a tenth of a pixel,
and the field here is smoothed on ~2 px, comparable to the published maps.

The two tests that read the paper's ``.tp4`` table skip themselves when the
ground-truth tree is not mounted; everything else is self-contained.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from jiram_catalog.config import paper_data_root
from jiram_catalog.tracking import (
    grid_positions,
    match_vectors,
    metres_per_second_per_pixel,
    parabolic_peak,
    read_tp4,
    track_pair,
)

PAPER = paper_data_root()
TRACKER_DIR = PAPER / "JIRAM velocity_vectors at 45 km"

TRUE_SHIFT = (2.3, -1.7)


# --------------------------------------------------------------------------
# synthetic imagery
# --------------------------------------------------------------------------
def smooth_field(size: int = 220, sigma: float = 2.0, seed: int = 7) -> np.ndarray:
    """A band-limited random field, strictly positive so that zero means invalid."""
    rng = np.random.default_rng(seed)
    spectrum = np.fft.fft2(rng.standard_normal((size, size)))
    freq = np.fft.fftfreq(size)
    power = freq[:, None] ** 2 + freq[None, :] ** 2
    field = np.real(np.fft.ifft2(spectrum * np.exp(-power * (2.0 * np.pi * sigma) ** 2 / 2.0)))
    field = (field - field.min()) / (field.max() - field.min())
    return field + 0.2


def fourier_shift(field: np.ndarray, drow: float, dcol: float) -> np.ndarray:
    """Shift by ``(drow, dcol)`` px with a periodic Fourier interpolation."""
    size = field.shape[0]
    ky = np.fft.fftfreq(size)[:, None]
    kx = np.fft.fftfreq(size)[None, :]
    phase = np.exp(-2j * np.pi * (ky * drow + kx * dcol))
    return np.real(np.fft.ifft2(np.fft.fft2(field) * phase))


# --------------------------------------------------------------------------
# the tracker
# --------------------------------------------------------------------------
def test_recovers_a_known_subpixel_shift():
    first = smooth_field()
    second = fourier_shift(first, *TRUE_SHIFT)
    found = track_pair(first, second, step=3)
    assert len(found) > 500
    error = np.hypot(found[:, 2] - TRUE_SHIFT[0], found[:, 3] - TRUE_SHIFT[1])
    assert np.mean(error < 0.1) >= 0.95, (np.mean(error < 0.1), np.median(error))
    assert abs(np.mean(found[:, 2] - TRUE_SHIFT[0])) < 0.02
    assert abs(np.mean(found[:, 3] - TRUE_SHIFT[1])) < 0.02
    assert found[:, 4].min() >= 0.5


def test_an_integer_shift_lands_on_the_integer():
    """No peak locking: an exact whole-pixel shift comes back unbiased."""
    first = smooth_field(seed=11)
    second = fourier_shift(first, 3.0, -5.0)
    found = track_pair(first, second, step=7)
    assert np.median(found[:, 2]) == pytest.approx(3.0, abs=0.02)
    assert np.median(found[:, 3]) == pytest.approx(-5.0, abs=0.02)
    error = np.hypot(found[:, 2] - 3.0, found[:, 3] + 5.0)
    assert np.median(error) < 0.06 and error.max() < 0.25


def test_invalid_regions_are_skipped():
    first = smooth_field(seed=3)
    second = fourier_shift(first, *TRUE_SHIFT)
    hole = (slice(90, 130), slice(90, 130))
    blanked = first.copy()
    blanked[hole] = 0.0
    found = track_pair(blanked, second, step=1)
    rows, cols = found[:, 0].astype(int), found[:, 1].astype(int)
    # No template centre lies in the hole, and no surviving template takes more
    # than a tenth of its pixels from it -- which is also what n_valid reports.
    assert not np.any((rows >= 90) & (rows < 130) & (cols >= 90) & (cols < 130))
    zeros = np.array([
        int(np.count_nonzero(blanked[r - 7 : r + 8, c - 7 : c + 8] == 0.0))
        for r, c in zip(rows, cols)
    ])
    assert zeros.max() <= 225 - 0.9 * 225
    assert np.all(found[:, 5] == 225 - zeros)
    assert found[:, 5].min() >= 0.9 * 225
    # and the far field is still tracked correctly
    far = (rows < 60) & (cols < 60)
    error = np.hypot(found[far, 2] - TRUE_SHIFT[0], found[far, 3] - TRUE_SHIFT[1])
    assert np.median(error) < 0.1


def test_nan_counts_as_invalid_like_zero():
    first = smooth_field(seed=5)
    second = fourier_shift(first, *TRUE_SHIFT)
    with_nan = first.copy()
    with_nan[100:120, 100:120] = np.nan
    found = track_pair(with_nan, second, step=1)
    assert np.all(np.isfinite(found))
    rows, cols = found[:, 0].astype(int), found[:, 1].astype(int)
    assert not np.any((rows > 100) & (rows < 119) & (cols > 100) & (cols < 119))


def test_noise_is_rejected_by_the_ncc_threshold():
    first = smooth_field(seed=13)
    rng = np.random.default_rng(21)
    noise = rng.random(first.shape) + 0.2
    found = track_pair(first, noise, step=5, min_ncc=0.5)
    assert len(found) == 0
    # with no threshold the peaks exist but are weak
    loose = track_pair(first, noise, step=5, min_ncc=-1.0)
    assert len(loose) > 100
    assert np.median(loose[:, 4]) < 0.5


def test_positions_and_grid_agree():
    first = smooth_field(seed=17)
    second = fourier_shift(first, *TRUE_SHIFT)
    grid = grid_positions(first.shape, step=9)
    a = track_pair(first, second, step=9)
    b = track_pair(first, second, positions=grid)
    assert np.allclose(a, b)


def test_tiling_does_not_change_the_answer():
    """The block decomposition is an optimisation, not a model choice.

    Each tile subtracts its own local mean before the window sums, which the NCC
    is exactly invariant to, so changing the tile size may only move the last
    bits of the float32 accumulation.
    """
    first = smooth_field(seed=23)
    second = fourier_shift(first, *TRUE_SHIFT)
    coarse = track_pair(first, second, step=5, tile=256)
    fine = track_pair(first, second, step=5, tile=48)
    assert coarse.shape == fine.shape
    assert np.array_equal(coarse[:, :2], fine[:, :2])
    assert np.max(np.abs(coarse[:, 2:4] - fine[:, 2:4])) < 1e-3
    assert np.array_equal(coarse[:, 5], fine[:, 5])


def test_positions_outside_the_valid_window_are_dropped():
    first = smooth_field(seed=19)
    second = fourier_shift(first, *TRUE_SHIFT)
    positions = np.array([[0, 0], [5, 5], [110, 110], [219, 219]])
    found = track_pair(first, second, positions=positions)
    assert found.shape[0] == 1
    assert (found[0, 0], found[0, 1]) == (110.0, 110.0)


# --------------------------------------------------------------------------
# sub-pixel refinement
# --------------------------------------------------------------------------
def test_parabolic_peak_on_a_synthetic_surface():
    y, x = np.mgrid[-1:2, -1:2].astype(float)
    truths = [(0.0, 0.0), (0.3, -0.25), (-0.4, 0.45)]
    stencil = np.empty((3, 3, len(truths)))
    for k, (dy, dx) in enumerate(truths):
        stencil[:, :, k] = 1.0 - 0.7 * (x - dx) ** 2 - 0.5 * (y - dy) ** 2
    got_y, got_x = parabolic_peak(stencil)
    assert np.allclose(got_y, [t[0] for t in truths], atol=1e-8)
    assert np.allclose(got_x, [t[1] for t in truths], atol=1e-8)


def test_parabolic_peak_handles_a_cross_term():
    y, x = np.mgrid[-1:2, -1:2].astype(float)
    dy, dx = -0.2, 0.35
    surface = (1.0 - 0.8 * (x - dx) ** 2 - 0.6 * (y - dy) ** 2
               - 0.3 * (x - dx) * (y - dy))
    got_y, got_x = parabolic_peak(surface[:, :, None])
    assert got_y[0] == pytest.approx(dy, abs=1e-8)
    assert got_x[0] == pytest.approx(dx, abs=1e-8)


def test_parabolic_peak_falls_back_on_a_flat_stencil():
    flat = np.zeros((3, 3, 1))
    got_y, got_x = parabolic_peak(flat)
    assert got_y[0] == 0.0 and got_x[0] == 0.0


# --------------------------------------------------------------------------
# match_vectors
# --------------------------------------------------------------------------
def _synthetic_tp4(base: int, n_lines: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """A tiny TRACKER4-like table and the matching tracker output."""
    rows = np.array([100, 101, 102], dtype=float)
    cols = np.array([200, 210, 220], dtype=float)
    drow = np.array([1.5, -2.0, 0.25])
    dcol = np.array([-0.5, 3.0, 1.75])
    ours = np.column_stack([rows, cols, drow + 0.1, dcol - 0.2,
                            np.full(3, 0.9), np.full(3, 225.0)])
    if n_lines is None:
        line, sign = rows + base, 1.0
    else:
        line, sign = n_lines - 1 - rows + base, -1.0
    theirs = np.column_stack([
        cols + base, line, cols + base + dcol, line + sign * drow,
        dcol, sign * drow, dcol * 15.4, sign * drow * 15.4,
    ])
    return ours, theirs


@pytest.mark.parametrize("base", [0, 1])
def test_match_vectors_under_both_index_bases(base):
    ours, theirs = _synthetic_tp4(base)
    joined = match_vectors(ours, theirs, base)
    assert len(joined) == 3
    assert np.allclose(joined["d_drow"], 0.1)
    assert np.allclose(joined["d_dcol"], -0.2)
    assert np.allclose(joined["d_px"], np.hypot(0.1, 0.2))
    assert joined.attrs["ms_per_px"] == pytest.approx(15.4, abs=1e-6)
    assert np.allclose(joined["d_speed_ms"], np.hypot(0.1, 0.2) * 15.4)
    # the wrong base joins nothing
    assert len(match_vectors(ours, theirs, 1 - base)) == 0


@pytest.mark.parametrize("base", [0, 1])
def test_match_vectors_with_a_reversed_line_axis(base):
    ours, theirs = _synthetic_tp4(base, n_lines=3000)
    joined = match_vectors(ours, theirs, base, n_lines=3000)
    assert len(joined) == 3
    assert np.allclose(joined["d_px"], np.hypot(0.1, 0.2))
    assert len(match_vectors(ours, theirs, base)) == 0


# --------------------------------------------------------------------------
# the paper's own table
# --------------------------------------------------------------------------
@pytest.mark.skipif(not TRACKER_DIR.is_dir(), reason="paper data not mounted")
def test_read_tp4_is_self_consistent():
    table, label = read_tp4(TRACKER_DIR / "n0103aa.tp4")
    assert table.ndim == 2 and table.shape[1] == 8
    assert table.shape[0] == int(label["NL"])
    assert np.max(np.abs(table[:, 4] - (table[:, 2] - table[:, 0]))) < 1e-4
    assert np.max(np.abs(table[:, 5] - (table[:, 3] - table[:, 1]))) < 1e-4
    scale = float(label["MPS"]) * 1000.0 / float(label["DT"])
    assert np.max(np.abs(table[:, 6] - table[:, 4] * scale)) < 0.5
    assert np.max(np.abs(table[:, 7] - table[:, 5] * scale)) < 0.5
    assert int(label["NSW"]) == int(label["NLW"]) == 15
    assert int(label["NSAREA"]) == int(label["NLAREA"]) == 35


@pytest.mark.skipif(not TRACKER_DIR.is_dir(), reason="paper data not mounted")
def test_metres_per_second_per_pixel_matches_the_label():
    table, label = read_tp4(TRACKER_DIR / "n0103aa.tp4")
    scale = float(label["MPS"]) * 1000.0 / float(label["DT"])
    assert metres_per_second_per_pixel(table) == pytest.approx(scale, rel=1e-4)
