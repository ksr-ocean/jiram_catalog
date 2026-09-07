"""Reading a JunoCam EDR/RDR image into framelets.

A JunoCam product is one tall single-column strip of 128-line framelets.  The
SIS orders them frame-major with the bands cycling inside each frame in
``FILTER_NAME`` order::

    frame 0 band 0 | frame 0 band 1 | ... | frame 1 band 0 | ...

so ``LINES = n_frames * n_bands * 128`` and the natural in-memory shape is
``(n_frames, n_bands, 128, 1648)`` -- which is exactly the shape the geometry
arrays take, one geometry element per image sample.

RDR samples are 16-bit unsigned big-endian (PDS3 ``UNSIGNED_INTEGER`` is
MSB-first) and already linear: ground processing inverted the onboard 12-to-8
bit companding and rescaled so that a white Lambertian surface at the
spacecraft's solar distance reads 10000 DN.  EDR samples are the raw 8-bit
companded counts, and :func:`decompand` puts them back on the 12-bit linear
scale with the SIS Appendix C ``SQROOT`` table.  The table is a lookup, not a
formula: it is the identity to DN 23, then steps of 2, 4, 8 ... as the
square-root law coarsens, ending at 8-bit 255 -> 12-bit 2879.

Nothing here divides an image into anything smaller than a frame, so the
caller who must not hold two images at once can free one array and read the
next.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .camera import FRAMELET_LINES, FRAMELET_SAMPLES

__all__ = [
    "SQROOT_TABLE",
    "band_names",
    "decompand",
    "image_path",
    "read_image",
]

#: SIS Appendix C, 8-bit ``SQROOT`` value -> 12-bit linear value.  Strictly
#: increasing, so the inverse is well defined up to the table's own step size.
SQROOT_TABLE: np.ndarray = np.array([
    0, 1, 2, 3, 4, 5, 6, 7,
    8, 9, 10, 11, 12, 13, 14, 15,
    16, 17, 18, 19, 20, 21, 22, 23,
    25, 27, 29, 31, 33, 35, 37, 39,
    41, 43, 45, 47, 49, 51, 53, 55,
    57, 59, 61, 63, 67, 71, 75, 79,
    83, 87, 91, 95, 99, 103, 107, 111,
    115, 119, 123, 127, 131, 135, 139, 143,
    147, 151, 155, 159, 163, 167, 171, 175,
    179, 183, 187, 191, 195, 199, 203, 207,
    211, 215, 219, 223, 227, 231, 235, 239,
    243, 247, 255, 263, 271, 279, 287, 295,
    303, 311, 319, 327, 335, 343, 351, 359,
    367, 375, 383, 391, 399, 407, 415, 423,
    431, 439, 447, 455, 463, 471, 479, 487,
    495, 503, 511, 519, 527, 535, 543, 551,
    559, 567, 575, 583, 591, 599, 607, 615,
    623, 631, 639, 647, 655, 663, 671, 679,
    687, 695, 703, 711, 719, 727, 735, 743,
    751, 759, 767, 775, 783, 791, 799, 807,
    815, 823, 831, 839, 847, 855, 863, 871,
    879, 887, 895, 903, 911, 919, 927, 935,
    943, 951, 959, 967, 975, 983, 991, 999,
    1007, 1023, 1039, 1055, 1071, 1087, 1103, 1119,
    1135, 1151, 1167, 1183, 1199, 1215, 1231, 1247,
    1263, 1279, 1295, 1311, 1327, 1343, 1359, 1375,
    1391, 1407, 1439, 1471, 1503, 1535, 1567, 1599,
    1631, 1663, 1695, 1727, 1759, 1791, 1823, 1855,
    1887, 1919, 1951, 1983, 2015, 2047, 2079, 2111,
    2143, 2175, 2207, 2239, 2271, 2303, 2335, 2367,
    2399, 2431, 2463, 2495, 2527, 2559, 2591, 2623,
    2655, 2687, 2719, 2751, 2783, 2815, 2847, 2879,
], dtype=np.int32)


def band_names(row) -> tuple[str, ...]:
    """Band names of an index row, in the label's ``FILTER_NAME`` order."""
    filters = str(row["filters"] if hasattr(row, "__getitem__") else row)
    names = tuple(part.strip().upper() for part in filters.split(";") if part.strip())
    if not names:
        raise ValueError("index row carries no FILTER_NAME entries")
    return names


def image_path(row, mirror: str | Path | None = None) -> Path:
    """Absolute path of the ``.IMG`` a row points at.

    ``path`` in the index is relative to ``<mirror>/junocam`` (the JunoCam
    mirror root), which is what keeps the index portable; an absolute value is
    taken as given.
    """
    if mirror is None:
        from ..config import mirror_root

        mirror = mirror_root()
    raw = Path(str(row["path"]))
    return raw if raw.is_absolute() else Path(mirror) / "junocam" / raw


def decompand(counts) -> np.ndarray:
    """8-bit companded EDR counts -> 12-bit linear DN (SIS ``SQROOT`` table)."""
    counts = np.asarray(counts)
    if counts.min() < 0 or counts.max() > 255:
        raise ValueError("companded counts must be in [0, 255]")
    return SQROOT_TABLE[counts.astype(np.intp)]


def read_image(
    row,
    mirror: str | Path | None = None,
    *,
    dtype: np.dtype | str = np.float32,
    decompand_edr: bool = True,
) -> np.ndarray:
    """Read one product into ``(n_frames, n_bands, 128, 1648)``.

    ``LINES`` is trusted over ``n_framelets`` only to the extent that the two
    must agree: a product whose line count is not ``n_frames * n_bands * 128``
    is a malformed row and raises rather than being silently truncated.  An
    8-bit (EDR) product is decompanded by default so that its values are on the
    same linear scale as an RDR's.
    """
    path = image_path(row, mirror)
    bands = band_names(row)
    n_bands = len(bands)
    n_frames = int(row["n_framelets"])
    lines = int(row["lines"])
    samples = int(row["samples"])
    bits = int(row["sample_bits"])
    if samples != FRAMELET_SAMPLES:
        raise ValueError(
            f"{path.name}: expected {FRAMELET_SAMPLES} samples per line, got {samples}"
            f" (SAMPLING_FACTOR={row['sampling_factor'] if 'sampling_factor' in row else '?'});"
            " on-board summed products are not on the IK's framelet grid"
        )
    if lines != n_frames * n_bands * FRAMELET_LINES:
        raise ValueError(
            f"{path.name}: LINES={lines} is not n_frames*n_bands*128 "
            f"({n_frames}*{n_bands}*{FRAMELET_LINES})"
        )
    if bits == 16:
        raw = np.fromfile(path, dtype=">u2")
    elif bits == 8:
        raw = np.fromfile(path, dtype=np.uint8)
    else:
        raise ValueError(f"{path.name}: unsupported SAMPLE_BITS={bits}")
    expected = lines * samples
    if raw.size != expected:
        raise ValueError(f"{path.name}: {raw.size} samples on disk, expected {expected}")
    raw = raw.reshape(n_frames, n_bands, FRAMELET_LINES, samples)
    if bits == 8 and decompand_edr:
        raw = decompand(raw)
    return raw.astype(dtype)
