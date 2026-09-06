"""Offline tests for the masked 2-D statistics module (no data, no kernels).

Every field here is synthetic and every expected answer is either exact by
construction (Parseval, the wavenumber of a pure sinusoid, a brute-force pair
count) or a property of the generator (the slope of a prescribed power law, the
phase coupling of a quadratically coupled triad), so the tests check the
estimator and never a fixture.

The random fields are built the way the conventions document prescribes:
a real white-noise field is transformed and multiplied by a real even filter, so
Hermitian symmetry holds by construction and is never imposed afterwards.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr

from jiram_catalog.stats2d import (
    BicoherenceAccumulator,
    isotropic_spectrum,
    population_statistics,
    power_spectrum_2d,
    spectrum_1d,
    strip_statistics,
    structure_function,
)

DX = 2000.0  # metres per pixel, the conventions document's nominal case


# --------------------------------------------------------------------------
# synthetic fields
# --------------------------------------------------------------------------
def _lattice(n: int, dx: float = DX) -> tuple[np.ndarray, float]:
    """``|k|`` on the unshifted ``(n, n)`` lattice, and the fundamental ``dk``."""
    k = 2.0 * np.pi * np.fft.fftfreq(n, d=dx)
    return np.hypot(k[:, None], k[None, :]), 2.0 * np.pi / (n * dx)


def power_law_field(
    n: int = 512, exponent: float = -3.0, seed: int = 3, j_min: int = 6
) -> np.ndarray:
    """A Gaussian random field whose shell spectrum is ``E(k) ~ k**exponent``.

    Shell ``j`` gets exactly the conventions document's share: every one of its
    ``N_j`` modes carries ``E(k_j)*dk/N_j``, with ``k_j = j*dk`` and ``N_j``
    counted on the lattice.  The phases come from a real white-noise field, so
    the result is real to rounding.

    The energized band starts at ``j_min`` (wavelength ``n/j_min`` px, 85 px by
    default), which puts the energy-containing scale just above the fitted
    window of 64 to 8 px, as in a real strip.  That is not cosmetic.  The masked
    periodogram's leakage is the true spectrum convolved with the mask's own,
    and the mask's hard blob edges give it a ``k^-3`` tail, so the contamination
    at wavenumber ``k`` scales with the *total variance* of the field: energizing
    ``k^-3`` all the way down to the fundamental puts 85% of the variance in the
    single largest shell and buries the 8 px signal under leakage no scalar
    correction can remove.  A field whose variance sits at 85 px keeps the
    residual mask bias near 0.09 in slope, half the tolerance below, so this test
    still fails if the masking is mishandled.
    """
    rng = np.random.default_rng(seed)
    modulus, dk = _lattice(n)
    index = np.rint(modulus / dk).astype(np.int64)
    population = np.bincount(index.ravel())
    target = np.zeros_like(modulus)
    inner = index >= j_min
    target[inner] = (index[inner] * dk) ** exponent * dk / population[index[inner]]
    noise = np.fft.fft2(rng.standard_normal((n, n)), norm="forward")
    field = np.fft.ifft2(noise * np.sqrt(target * n * n), norm="forward")
    assert np.abs(field.imag).max() < 1e-12 * np.sqrt(np.mean(field.real**2))
    real = field.real
    return real / real.std()


def blob_mask(n: int = 512, fraction: float = 0.15, seed: int = 11) -> np.ndarray:
    """A mask hiding ``fraction`` of the canvas as a few smooth blobs.

    The noise is low-passed at a 250 px Gaussian scale before thresholding, which
    leaves two blobs of about 20,000 px each -- "a few", with the least perimeter
    (and so the least spectral leakage) that 15% of the canvas allows.
    """
    rng = np.random.default_rng(seed)
    modulus, _ = _lattice(n)
    cut = 2.0 * np.pi / (250.0 * DX)
    smooth = np.fft.ifft2(
        np.fft.fft2(rng.standard_normal((n, n)), norm="forward") * np.exp(-((modulus / cut) ** 2)),
        norm="forward",
    ).real
    return smooth > np.percentile(smooth, 100.0 * fraction)


def sinusoid_field(n: int = 100, wavelength_px: int = 10) -> np.ndarray:
    """A pure sinusoid along ``x``, constant along ``y``, periodic on the grid."""
    assert n % wavelength_px == 0
    x = np.arange(n)
    return np.broadcast_to(np.cos(2.0 * np.pi * x / wavelength_px), (n, n)).copy()


def fitted_slope(k: np.ndarray, energy: np.ndarray, k_lo: float, k_hi: float) -> float:
    """Least-squares slope of ``log E`` against ``log k`` on ``[k_lo, k_hi]``."""
    use = np.isfinite(energy) & (energy > 0.0) & (k >= k_lo) & (k <= k_hi)
    assert use.sum() > 10
    return float(np.polyfit(np.log(k[use]), np.log(energy[use]), 1)[0])


def synthetic_strip(seed: int, km_per_px: float = 15.0, shape=(320, 384)) -> xr.Dataset:
    """A strip-like Dataset: dims (y, x), NaN-holed image, mask, coords, attrs."""
    rng = np.random.default_rng(seed)
    ny, nx = shape
    modulus, _ = _lattice(max(ny, nx), km_per_px * 1000.0)
    smooth = np.fft.ifft2(
        np.fft.fft2(rng.standard_normal(modulus.shape), norm="forward")
        * np.exp(-((modulus / (2.0 * np.pi / (12.0 * km_per_px * 1000.0))) ** 2)),
        norm="forward",
    ).real[:ny, :nx]
    image = 10.0 + smooth / smooth.std()
    valid = np.ones(shape, dtype=bool)
    valid[: ny // 12, :] = False
    valid[:, : nx // 15] = False
    image = np.where(valid, image, np.nan).astype(np.float32)
    return xr.Dataset(
        {"image": (("y", "x"), image), "valid": (("y", "x"), valid)},
        coords={
            "x_km": ("x", np.arange(nx) * km_per_px),
            "y_km": ("y", np.arange(ny) * km_per_px),
        },
        attrs={
            "strip_id": f"04_M_2017033T114006_{seed:02d}",
            "km_per_px": km_per_px,
            "band": "M",
            "orbit": 4,
            "time_mid": "2017-02-02T11:40:06",
            "valid_frac": float(valid.mean()),
        },
    )


# --------------------------------------------------------------------------
# 1. Parseval
# --------------------------------------------------------------------------
def test_parseval_and_shell_sum_are_exact():
    rng = np.random.default_rng(1)
    field = rng.standard_normal((256, 256))
    spec = power_spectrum_2d(field, None, dx_m=DX, window=None, correction="none")

    total = float(spec["power"].sum())
    assert abs(total - spec["variance"]) <= 1e-9 * abs(spec["variance"])

    shells = isotropic_spectrum(spec)
    shell_total = float(np.nansum(shells["E"] * shells["dk"]))
    assert abs(shell_total - total) <= 1e-9 * total
    assert int(shells["count"].sum()) == field.size


# --------------------------------------------------------------------------
# 2. wavenumber axis
# --------------------------------------------------------------------------
def test_sinusoid_sits_on_the_expected_wavenumber():
    field = sinusoid_field(100, 10)
    spec = power_spectrum_2d(field, None, dx_m=DX, window=None, correction="none")
    power, kx, ky = spec["power"], spec["kx"], spec["ky"]

    row, column = np.unravel_index(int(np.argmax(power)), power.shape)
    expected = 2.0 * np.pi / (10.0 * DX)
    half_bin = 0.5 * float(kx[1] - kx[0])
    assert abs(abs(float(kx[column])) - expected) <= half_bin

    zero_row = int(np.argmin(np.abs(ky)))
    assert ky[zero_row] == 0.0
    assert row == zero_row
    off_dc = np.delete(power, zero_row, axis=0)
    assert off_dc.max() <= 1e-20 * power.max()


# --------------------------------------------------------------------------
# 3. power-law recovery
# --------------------------------------------------------------------------
def test_power_law_slope_full_and_masked():
    field = power_law_field(512, -3.0, seed=3)
    k_lo = 2.0 * np.pi / (64.0 * DX)
    k_hi = 2.0 * np.pi / (8.0 * DX)

    full = isotropic_spectrum(power_spectrum_2d(field, None, dx_m=DX))
    slope_full = fitted_slope(full["k"], full["E"], k_lo, k_hi)
    assert abs(slope_full + 3.0) <= 0.1, slope_full

    mask = blob_mask(512, 0.15, seed=11)
    assert 0.10 <= 1.0 - mask.mean() <= 0.20
    holed = np.where(mask, field, np.nan)
    masked = isotropic_spectrum(power_spectrum_2d(holed, mask, dx_m=DX))
    slope_masked = fitted_slope(masked["k"], masked["E"], k_lo, k_hi)
    assert abs(slope_masked + 3.0) <= 0.2, slope_masked


# --------------------------------------------------------------------------
# 4. structure function against brute force
# --------------------------------------------------------------------------
def test_structure_function_matches_brute_force():
    rng = np.random.default_rng(4)
    field = rng.standard_normal((64, 64))
    valid = rng.random((64, 64)) >= 0.20
    holed = np.where(valid, field, np.nan)

    result = structure_function(
        holed, valid, dx_m=DX, orders=(2,), max_lag_px=5, direction="x"
    )

    ny, nx = field.shape
    for position, lag in enumerate(range(1, 6)):
        total, count = 0.0, 0
        for j in range(ny):
            for i in range(nx - lag):
                if valid[j, i] and valid[j, i + lag]:
                    total += (field[j, i + lag] - field[j, i]) ** 2
                    count += 1
        assert count == int(result["count"][position])
        assert result["r_m"][position] == pytest.approx(lag * DX)
        assert result["S"][2][position] == pytest.approx(total / count, rel=1e-12, abs=1e-12)


# --------------------------------------------------------------------------
# 5. bicoherence
# --------------------------------------------------------------------------
def _triad_segments(coupled: bool, n: int = 256, n_seg: int = 400, seed: int = 5):
    rng = np.random.default_rng(seed)
    x = np.arange(n) * DX
    j1, j2 = 10, 17
    k1 = 2.0 * np.pi * j1 / (n * DX)
    k2 = 2.0 * np.pi * j2 / (n * DX)
    lines = np.empty((n_seg, n))
    for s in range(n_seg):
        p1, p2 = rng.uniform(0.0, 2.0 * np.pi, 2)
        p3 = p1 + p2 if coupled else rng.uniform(0.0, 2.0 * np.pi)
        lines[s] = (
            np.cos(k1 * x + p1)
            + np.cos(k2 * x + p2)
            + 0.5 * np.cos((k1 + k2) * x + p3)
            + 0.3 * rng.standard_normal(n)
        )
    return lines, j1, j2


def test_bicoherence_separates_coupled_from_independent_phases():
    coupled_lines, j1, j2 = _triad_segments(True)
    accumulator = BicoherenceAccumulator(256, DX)
    assert accumulator.add_lines(coupled_lines) == 400
    coupled = accumulator.result()
    assert coupled["n_segments"] == 400
    assert coupled["b2"][j1, j2] > 0.5, coupled["b2"][j1, j2]

    free_lines, _, _ = _triad_segments(False)
    other = BicoherenceAccumulator(256, DX)
    other.add_lines(free_lines)
    free = other.result()
    assert free["b2"][j1, j2] < 0.1, free["b2"][j1, j2]

    assert np.nanmax(coupled["b2"]) <= 1.0 + 1e-12
    assert np.isnan(coupled["b2"][100, 100])  # k1 + k2 beyond Nyquist


# --------------------------------------------------------------------------
# 6. one-dimensional spectra and line rejection
# --------------------------------------------------------------------------
def test_spectrum_1d_peak_and_line_rejection():
    field = sinusoid_field(100, 10)
    spec = spectrum_1d(field, None, dx_m=DX, axis=1)
    expected = 2.0 * np.pi / (10.0 * DX)
    peak = spec["k"][int(np.argmax(spec["P"]))]
    assert abs(peak - expected) <= 0.5 * float(spec["k"][1] - spec["k"][0])
    assert spec["n_lines"] == 100

    valid = np.ones_like(field, dtype=bool)
    valid[:7, :80] = False  # 7 rows only 20% valid, well under the default 0.9
    holed = np.where(valid, field, np.nan)
    thinned = spectrum_1d(holed, valid, dx_m=DX, axis=1)
    assert thinned["n_lines"] == 93
    assert abs(thinned["k"][int(np.argmax(thinned["P"]))] - expected) <= 0.5 * float(
        spec["k"][1] - spec["k"][0]
    )


# --------------------------------------------------------------------------
# 7. strip and population products
# --------------------------------------------------------------------------
def test_strip_and_population_products():
    strips = [synthetic_strip(seed) for seed in (21, 22, 23)]

    stats = strip_statistics(strips[0], max_lag_px=16)
    assert set(stats.data_vars) >= {"E", "P_x", "P_y", "S2", "S3", "count_k", "count_r"}
    assert set(stats.coords) >= {"k", "kx", "ky", "r"}
    assert stats.sizes["r"] == 16
    assert stats.attrs["strip_id"] == strips[0].attrs["strip_id"]
    assert stats.attrs["km_per_px"] == 15.0
    assert stats.attrs["band"] == "M"
    assert np.isfinite(stats["S2"].values).all()

    population = population_statistics(strips, n_fft=64)
    assert population.attrs["n_strips"] == 3
    assert population.attrs["km_per_px"] == 15.0
    assert set(population.data_vars) >= {
        "E_mean", "E_se", "P_x_mean", "P_x_se", "P_y_mean", "P_y_se",
        "S2_mean", "S2_se", "S3_mean", "S3_se", "b2",
    }
    assert set(population.coords) >= {"k", "kx", "ky", "r", "k1", "k2"}
    assert population.attrs["n_segments"] > 0
    assert np.isfinite(population["E_mean"].values[1:]).any()
    assert np.isfinite(population["E_se"].values[1:]).any()
