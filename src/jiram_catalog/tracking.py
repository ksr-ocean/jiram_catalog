"""Classical template-matching cloud tracker for polar map pairs.

The tracker is the textbook one -- a square template cut from the first image,
slid over a square search neighbourhood of the second, scored by the normalised
cross-correlation, the integer peak refined by a parabola -- with two details
that the JIRAM polar maps force on it.

The first is validity.  A published map is one JIRAM frame resampled onto a
3000 x 3200 grid, so more than 90% of the array is exactly zero and every
template near the footprint edge is part background.  Zeros (and NaNs) are
therefore not data: a template is used only if enough of it is real, and the
correlation is evaluated on the pixels that are real in *both* patches, which
makes the mask depend on the trial displacement.

The second is arithmetic.  Doing this honestly needs six masked window sums per
trial displacement -- ``n``, ``Sa``, ``Saa``, ``Sb``, ``Sbb``, ``Sab`` -- and a
35 x 35 search means 1225 trials per template.  Computing them one template at a
time is hopeless; computing them as *cost volumes* is not.  For a fixed
displacement every one of the six sums is a box filter of a pointwise product,
so a whole tile of templates is served by one multiply and one summed-area pass,
and the per-template cost collapses by more than an order of magnitude.  The
images are also centred on their local means first, which is exact (the NCC is
invariant under adding a constant to either patch) and removes the catastrophic
cancellation that ``n*Saa - Sa^2`` would otherwise suffer in float32.

Coordinates.  Arrays are indexed ``(row, col)`` 0-based throughout, and
displacements are ``(drow, dcol)`` in pixels.  The TRACKER4 tables use VICAR
``(sample, line)`` instead, and -- see :func:`match_vectors` -- their line axis
runs opposite to the stored record order of the published maps.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .vicar import Label, read_vicar

__all__ = [
    "grid_positions",
    "match_vectors",
    "metres_per_second_per_pixel",
    "parabolic_peak",
    "read_tp4",
    "track_pair",
]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _offsets(search: int) -> np.ndarray:
    """The ``search`` signed offsets of a search axis, centred on zero."""
    return np.arange(search, dtype=np.int64) - (search // 2)


def grid_positions(
    shape: tuple[int, int], template: int = 15, search: int = 35, step: int = 1
) -> np.ndarray:
    """Regular ``(row, col)`` grid over the region where template and search fit."""
    offs = _offsets(search)
    half = template // 2
    lo = half + max(0, int(-offs[0]))
    hi_r = shape[0] - 1 - half - max(0, int(offs[-1]))
    hi_c = shape[1] - 1 - half - max(0, int(offs[-1]))
    rows = np.arange(lo, hi_r + 1, step)
    cols = np.arange(lo, hi_c + 1, step)
    grid = np.stack(np.meshgrid(rows, cols, indexing="ij"), axis=-1)
    return grid.reshape(-1, 2)


def _box_sums(product: np.ndarray, size: int, work: dict) -> np.ndarray:
    """Sliding ``size`` x ``size`` sums of ``product`` over its last two axes.

    Two summed-area passes (cumulative sum, then a shifted difference) give the
    window sums in four sweeps of the array instead of ``size**2``; the
    intermediate buffers are reused across the whole search.
    """
    row_cs = work["row_cs"]
    row_cs[..., 0] = 0.0
    np.cumsum(product, axis=-1, out=row_cs[..., 1:])
    horiz = work["horiz"]
    np.subtract(row_cs[..., size:], row_cs[..., :-size], out=horiz)

    col_cs = work["col_cs"]
    col_cs[..., 0, :] = 0.0
    np.cumsum(horiz, axis=-2, out=col_cs[..., 1:, :])
    out = work["out"]
    np.subtract(col_cs[..., size:, :], col_cs[..., :-size, :], out=out)
    return out


def parabolic_peak(patch: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Sub-pixel offset of the maximum of 3 x 3 neighbourhoods.

    ``patch`` has shape ``(3, 3, K)``; axis 0 is the row (``y``) direction and
    axis 1 the column (``x``) direction, both sampled at -1, 0, +1.  A full
    six-term quadratic ``c0 + c1 x + c2 y + c3 x^2 + c4 y^2 + c5 x y`` is fitted
    to the nine samples by least squares -- on a 3 x 3 stencil the normal
    equations close in three sums -- and the stationary point is returned.
    Where that point is not an interior maximum the estimate falls back to the
    separable one-dimensional parabola through the centre, and where even that
    is not a maximum the offset is zero.  Returned as ``(dy, dx)`` in pixels.
    """
    f = np.asarray(patch, dtype=np.float64)
    if f.shape[:2] != (3, 3):
        raise ValueError("parabolic_peak expects a (3, 3, K) stencil")
    x = np.array([-1.0, 0.0, 1.0])[None, :, None]
    y = np.array([-1.0, 0.0, 1.0])[:, None, None]

    total = f.sum(axis=(0, 1))
    sx = (x * f).sum(axis=(0, 1)) / 6.0            # c1
    sy = (y * f).sum(axis=(0, 1)) / 6.0            # c2
    sxy = (x * y * f).sum(axis=(0, 1)) / 4.0       # c5
    sxx = (x * x * f).sum(axis=(0, 1))
    syy = (y * y * f).sum(axis=(0, 1))
    plus = (sxx + syy - (4.0 / 3.0) * total) / 2.0   # c3 + c4
    minus = (sxx - syy) / 2.0                        # c3 - c4
    c3 = 0.5 * (plus + minus)
    c4 = 0.5 * (plus - minus)

    det = 4.0 * c3 * c4 - sxy * sxy
    with np.errstate(invalid="ignore", divide="ignore"):
        dx = (-2.0 * c4 * sx + sxy * sy) / det
        dy = (sxy * sx - 2.0 * c3 * sy) / det
    good = (det > 0.0) & (c3 < 0.0) & (c4 < 0.0)
    good &= np.isfinite(dx) & np.isfinite(dy) & (np.abs(dx) <= 1.0) & (np.abs(dy) <= 1.0)

    # Separable fallback along each axis through the integer peak.
    def _axis(minus_one, centre, plus_one):
        curve = minus_one - 2.0 * centre + plus_one
        with np.errstate(invalid="ignore", divide="ignore"):
            shift = 0.5 * (minus_one - plus_one) / curve
        return np.where((curve < 0.0) & np.isfinite(shift) & (np.abs(shift) <= 1.0), shift, 0.0)

    fx = _axis(f[1, 0], f[1, 1], f[1, 2])
    fy = _axis(f[0, 1], f[1, 1], f[2, 1])
    return np.where(good, dy, fy), np.where(good, dx, fx)


# --------------------------------------------------------------------------
# the tracker
# --------------------------------------------------------------------------
def track_pair(
    img0: np.ndarray,
    img1: np.ndarray,
    *,
    template: int = 15,
    search: int = 35,
    step: int = 1,
    positions: np.ndarray | None = None,
    min_valid_frac: float = 0.9,
    min_ncc: float = 0.5,
    tile: int = 128,
) -> np.ndarray:
    """Track ``img0`` into ``img1`` by masked normalised cross-correlation.

    For every template centre -- either the integer ``positions`` array of shape
    ``(N, 2)`` as ``(row, col)``, or a regular grid with ``step`` -- the
    ``template`` x ``template`` patch of ``img0`` is correlated with every patch
    of ``img1`` whose centre lies within the ``search`` x ``search``
    neighbourhood of the same point.  Pixels equal to zero or not finite are
    invalid; a template needs ``min_valid_frac`` of its pixels valid, the
    correlation is evaluated only where both patches are valid and needs the
    same fraction there, and the peak is kept only if its correlation reaches
    ``min_ncc``.  The integer peak is refined by :func:`parabolic_peak` unless it
    sits on the border of the search area.

    Returns an ``(M, 6)`` array of the accepted vectors: ``row0, col0, drow,
    dcol, ncc, n_valid``, where ``n_valid`` counts the pixels that were valid in
    both patches at the winning displacement.  ``tile`` sets the side of the
    blocks the templates are batched into; it trades memory for speed and does
    not change the result.
    """
    img0 = np.asarray(img0)
    img1 = np.asarray(img1)
    if img0.ndim != 2 or img0.shape != img1.shape:
        raise ValueError("track_pair needs two images of the same 2-D shape")
    if template % 2 == 0:
        raise ValueError("template must be odd so that it has a centre pixel")
    if search < 1:
        raise ValueError("search must be at least 1")

    lines, samples = img0.shape
    half = template // 2
    offs = _offsets(search)
    off_lo, off_hi = int(offs[0]), int(offs[-1])
    min_valid = min_valid_frac * template * template

    if positions is None:
        positions = grid_positions((lines, samples), template, search, step)
    positions = np.asarray(positions)
    if positions.ndim != 2 or positions.shape[1] != 2:
        raise ValueError("positions must have shape (N, 2) as (row, col)")
    rows = np.rint(positions[:, 0]).astype(np.int64)
    cols = np.rint(positions[:, 1]).astype(np.int64)

    lo = half + max(0, -off_lo)
    hi_r = lines - 1 - half - max(0, off_hi)
    hi_c = samples - 1 - half - max(0, off_hi)
    inside = (rows >= lo) & (rows <= hi_r) & (cols >= lo) & (cols <= hi_c)
    rows, cols = rows[inside], cols[inside]
    if rows.size == 0:
        return np.zeros((0, 6), dtype=np.float64)

    results: list[np.ndarray] = []
    key = (rows // tile) * (samples // tile + 2) + (cols // tile)
    order = np.argsort(key, kind="stable")
    bounds = np.flatnonzero(np.r_[True, np.diff(key[order]) != 0, True])

    for start, stop in zip(bounds[:-1], bounds[1:]):
        block = order[start:stop]
        results.append(
            _track_block(
                img0, img1, rows[block], cols[block],
                template=template, offs=offs, min_valid=min_valid, min_ncc=min_ncc,
            )
        )
    found = [block for block in results if block.size]
    if not found:
        return np.zeros((0, 6), dtype=np.float64)
    out = np.concatenate(found, axis=0)
    return out[np.lexsort((out[:, 1], out[:, 0]))]


def _centred(image: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """``(values - mean of the valid pixels) * valid``, and the validity mask."""
    values = np.asarray(image, dtype=np.float64)
    valid = np.isfinite(values) & (values != 0.0)
    if valid.any():
        values = values - values[valid].mean()
    centred = np.where(valid, values, 0.0).astype(np.float32)
    return centred, valid.astype(np.float32)


def _track_block(
    img0: np.ndarray,
    img1: np.ndarray,
    rows: np.ndarray,
    cols: np.ndarray,
    *,
    template: int,
    offs: np.ndarray,
    min_valid: float,
    min_ncc: float,
) -> np.ndarray:
    """Cost-volume tracker for one spatial block of template centres."""
    half = template // 2
    off_lo, off_hi = int(offs[0]), int(offs[-1])
    search = offs.size
    n_pos = rows.size

    r_lo, r_hi = int(rows.min()), int(rows.max())
    c_lo, c_hi = int(cols.min()), int(cols.max())
    # Region of img0 whose products are box-summed; its window sums land on the
    # template centres r_lo..r_hi, c_lo..c_hi.
    a0, a1 = r_lo - half, r_hi + half
    b0, b1 = c_lo - half, c_hi + half
    n_rows, n_cols = a1 - a0 + 1, b1 - b0 + 1

    first, valid0 = _centred(img0[a0 : a1 + 1, b0 : b1 + 1])
    second, valid1 = _centred(
        img1[a0 + off_lo : a1 + off_hi + 1, b0 + off_lo : b1 + off_hi + 1]
    )
    # Six pairs of factors: (v0, w), (a, w), (a^2, w), (v0, b), (v0, b^2), (a, b).
    left = np.stack([valid0, first, first * first, valid0, valid0, first])
    right = np.stack(
        [valid1, valid1, valid1, second, second * second, second]
    )

    out_rows, out_cols = n_rows - template + 1, n_cols - template + 1
    work = {
        "row_cs": np.empty((6, n_rows, n_cols + 1), dtype=np.float32),
        "horiz": np.empty((6, n_rows, out_cols), dtype=np.float32),
        "col_cs": np.empty((6, n_rows + 1, out_cols), dtype=np.float32),
        "out": np.empty((6, out_rows, out_cols), dtype=np.float32),
    }
    product = np.empty((6, n_rows, n_cols), dtype=np.float32)

    take_r = rows - r_lo
    take_c = cols - c_lo
    volume = np.empty((search * search, n_pos), dtype=np.float32)
    counts = np.empty((search * search, n_pos), dtype=np.float32)

    for i, drow in enumerate(offs):
        row_slice = slice(int(drow) - off_lo, int(drow) - off_lo + n_rows)
        for j, dcol in enumerate(offs):
            col_slice = slice(int(dcol) - off_lo, int(dcol) - off_lo + n_cols)
            np.multiply(left, right[:, row_slice, col_slice], out=product)
            sums = _box_sums(product, template, work)
            n, s_a, s_aa, s_b, s_bb, s_ab = sums[:, take_r, take_c]
            var_a = n * s_aa - s_a * s_a
            var_b = n * s_bb - s_b * s_b
            denom = var_a * var_b
            with np.errstate(invalid="ignore", divide="ignore"):
                ncc = (n * s_ab - s_a * s_b) / np.sqrt(denom)
            ncc[~((denom > 0.0) & (n >= min_valid) & np.isfinite(ncc))] = -1.0
            volume[i * search + j] = ncc
            counts[i * search + j] = n

    flat = volume.argmax(axis=0)
    which = np.arange(n_pos)
    peak = volume[flat, which]
    n_at_peak = counts[flat, which]
    i_row, i_col = np.divmod(flat, search)

    cube = volume.reshape(search, search, n_pos)
    stencil = np.empty((3, 3, n_pos), dtype=np.float32)
    for a in (-1, 0, 1):
        ra = np.clip(i_row + a, 0, search - 1)
        for b in (-1, 0, 1):
            cb = np.clip(i_col + b, 0, search - 1)
            stencil[a + 1, b + 1] = cube[ra, cb, which]
    d_row, d_col = parabolic_peak(stencil)
    interior = (i_row > 0) & (i_row < search - 1) & (i_col > 0) & (i_col < search - 1)
    d_row = np.where(interior, d_row, 0.0)
    d_col = np.where(interior, d_col, 0.0)

    keep = (peak >= min_ncc) & (n_at_peak >= min_valid)
    if not keep.any():
        return np.zeros((0, 6), dtype=np.float64)
    return np.column_stack(
        [
            rows[keep].astype(np.float64),
            cols[keep].astype(np.float64),
            offs[i_row[keep]] + d_row[keep],
            offs[i_col[keep]] + d_col[keep],
            peak[keep].astype(np.float64),
            n_at_peak[keep].astype(np.float64),
        ]
    )


# --------------------------------------------------------------------------
# the paper's vectors
# --------------------------------------------------------------------------
def read_tp4(path: str | Path) -> tuple[np.ndarray, Label]:
    """Read one TRACKER4 ``.tp4`` table.

    Returns the ``(N, 8)`` float array -- ``sample0, line0, sample1, line1,
    dsample, dline, vx, vy`` -- and the VICAR label, which carries ``TTIME0``,
    ``TTIME1``, ``DT``, the template and search sizes and the valid window.
    """
    table, label = read_vicar(path)
    table = np.asarray(table, dtype=np.float64)
    if table.ndim != 2 or table.shape[1] != 8:
        raise ValueError(f"{Path(path).name}: expected an (N, 8) table, got {table.shape}")
    return table, label


def metres_per_second_per_pixel(theirs: np.ndarray) -> float:
    """The ``MPS*1000/DT`` factor implied by a ``.tp4`` table's own columns."""
    theirs = np.asarray(theirs, dtype=np.float64)
    displacement = np.hypot(theirs[:, 4], theirs[:, 5])
    speed = np.hypot(theirs[:, 6], theirs[:, 7])
    use = displacement > 1.0
    if not use.any():
        return float("nan")
    return float(np.median(speed[use] / displacement[use]))


def match_vectors(
    ours: np.ndarray,
    theirs: np.ndarray,
    index_base: int,
    *,
    n_lines: int | None = None,
    ms_per_px: float | None = None,
) -> pd.DataFrame:
    """Join our vectors to a TRACKER4 table on the integer template position.

    ``ours`` is what :func:`track_pair` returns, indexed ``(row, col)`` 0-based;
    ``theirs`` is a ``(N, 8)`` table whose first two columns are the integer
    ``(sample, line)`` of the template centre in ``index_base`` numbering.  With
    ``n_lines`` given, the line axis is taken to run opposite to the stored
    record order of the map, so ``row = n_lines - 1 - (line - index_base)`` and
    the row displacement is ``-dline``; this is the convention the published
    perijove-4 maps and their ``.tp4`` tables actually use.  Without it the two
    axes are taken to agree.

    The returned frame holds one row per matched template with our displacement,
    theirs, the per-vector difference in pixels (``d_px`` is the length of the
    difference vector) and the same difference in m/s, using ``ms_per_px`` or,
    by default, the factor implied by the table's own velocity columns.
    """
    ours = np.asarray(ours, dtype=np.float64).reshape(-1, 6)
    theirs = np.asarray(theirs, dtype=np.float64).reshape(-1, 8)
    if index_base not in (0, 1):
        raise ValueError("index_base must be 0 or 1")

    sample = np.rint(theirs[:, 0]).astype(np.int64) - index_base
    line = np.rint(theirs[:, 1]).astype(np.int64) - index_base
    if n_lines is None:
        their_row, sign = line, 1.0
    else:
        their_row, sign = n_lines - 1 - line, -1.0

    scale = metres_per_second_per_pixel(theirs) if ms_per_px is None else float(ms_per_px)
    mine = pd.DataFrame(
        {
            "row": np.rint(ours[:, 0]).astype(np.int64),
            "col": np.rint(ours[:, 1]).astype(np.int64),
            "drow": ours[:, 2],
            "dcol": ours[:, 3],
            "ncc": ours[:, 4],
            "n_valid": ours[:, 5],
        }
    )
    other = pd.DataFrame(
        {
            "row": their_row,
            "col": sample,
            "drow_ref": sign * theirs[:, 5],
            "dcol_ref": theirs[:, 4],
        }
    )
    joined = mine.merge(other, on=["row", "col"], how="inner")
    joined["d_drow"] = joined["drow"] - joined["drow_ref"]
    joined["d_dcol"] = joined["dcol"] - joined["dcol_ref"]
    joined["d_px"] = np.hypot(joined["d_drow"], joined["d_dcol"])
    joined["d_speed_ms"] = joined["d_px"] * scale
    joined["speed_ms"] = np.hypot(joined["drow"], joined["dcol"]) * scale
    joined["speed_ref_ms"] = np.hypot(joined["drow_ref"], joined["dcol_ref"]) * scale
    joined["d_speed_scalar_ms"] = (joined["speed_ms"] - joined["speed_ref_ms"]).abs()
    joined.attrs["ms_per_px"] = scale
    return joined
