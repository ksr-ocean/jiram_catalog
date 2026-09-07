"""Per-image JunoCam quality: documented epoch flags plus measured metrics.

Two independent things are joined here.  The first is what the archive says
about the instrument: ``configs/junocam_quality.yaml`` transcribes the
volumes' ERRATA into orbit intervals (``nominal``, ``regulator_damage``,
``ccd_damage``, ``post_anneal``) and the per-band throughput loss documented
through orbit 60 but explicitly *not* applied to the RDR products.  The
second is what the pixels say, measured on the mirrored RDR image itself.

The metrics are computed per 128-line framelet and aggregated as medians --
over that band's framelets for the per-band columns, over all framelets for
the image-level columns.  The median, not the mean, because a single bloomed
or dropped framelet should not move an image's score.

* ``streak_index`` -- variance of the framelet's line means divided by the
  framelet's total variance.  This is the JIRAM L-band striping diagnostic:
  it is near 1 when the framelet is a set of constant, differing lines (pure
  horizontal striping, the PJ56 radiation signature) and near ``1/n_samples``
  when the noise is uncorrelated along a line.  A uniform framelet has no
  variance to divide by and scores 0, i.e. unstreaked.
* ``noise_mad`` -- median absolute deviation of the high-pass residual, the
  image minus its own 5x5 median filter, over the pixels below the 20th
  percentile of brightness.  Restricting to the darkest fifth measures read
  and dark noise rather than scene structure.  The dark pixels are chosen
  first and the median filter is evaluated only on their windows, which is
  what makes an exact 5x5 median affordable over a 15360-line strip.
  A "dark reference" from the line's own first and last 16 samples was
  rejected: those columns are not reliably off the planet.  A perijove frame
  is often more than a fifth off-planet, in which case the darkest fifth is
  all exact zeros and ``noise_mad`` is a truthful 0; the companion column
  ``noise_mad_nonzero`` repeats the measurement over the darkest fifth of the
  illuminated pixels and is the one to read for read noise.
* ``saturation_frac`` -- fraction of pixels at the maximum code the label
  declares, ``2**SAMPLE_BITS - 1``.  On a 16-bit RDR this is a conservative
  flag (the decompanding of an 8-bit EDR rarely reaches 65535), so
  ``max_dn``, the observed maximum, is reported alongside it.
* ``zero_frac`` -- fraction of pixels at 0.
* ``bloom_flag`` -- true when a methane framelet's line means exceed three
  times their own median on more than 5% of its lines, the signature the
  ERRATA describes of charge blooming out of the overexposed visible strips
  into the adjacent methane strip.

The tier rule, in full:

    A  the epoch is nominal or post_anneal, and streak_index < 0.3, and
       saturation_frac < 0.02
    B  the epoch is nominal or post_anneal but one of those metrics fails or
       could not be measured
    C  the epoch is a damage epoch (regulator_damage, ccd_damage), or is not
       covered by the epoch table at all

so tier A is a claim about both the instrument's documented health and the
image's own pixels, and C is a claim about the instrument alone.
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from multiprocessing import Pool
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import yaml
from numpy.lib.stride_tricks import sliding_window_view

from .index import load_images
from .labels import FRAMELET_LINES
from .mirror import junocam_root

LOGGER = logging.getLogger(__name__)

#: Environment override for the YAML transcribed from the volumes' ERRATA.
CONFIG_ENV = "JIRAM_JUNOCAM_QUALITY"

#: Tier-A thresholds (see the module docstring for the full rule).
STREAK_INDEX_MAX_A = 0.3
SATURATION_FRAC_MAX_A = 0.02
#: Epochs that are not themselves a disqualification.
HEALTHY_EPOCHS = ("nominal", "post_anneal")
#: Epochs in which the ERRATA documents radiation damage to the camera.
DAMAGE_EPOCHS = ("regulator_damage", "ccd_damage")
#: Returned for an orbit no interval of the YAML covers.
UNKNOWN_EPOCH = "unknown"

#: High-pass filter: an exact square median filter of this side length.
MEDIAN_FILTER_SIZE = 5
#: Percentile of brightness below which pixels count as "dark" for noise_mad.
DARK_PERCENTILE = 20.0
#: Blooming: a line mean this many times its framelet's median line mean ...
BLOOM_LINE_RATIO = 3.0
#: ... on more than this fraction of the framelet's lines raises bloom_flag.
BLOOM_LINE_FRACTION = 0.05
#: Bands the throughput table and the per-band metric columns are keyed by.
BANDS = ("red", "green", "blue", "methane")

METRIC_NAMES = ("streak_index", "noise_mad", "saturation_frac", "zero_frac")

QUALITY_COLUMNS = (
    [
        "product_id",
        "level",
        "volume",
        "orbit",
        "doy_dir",
        "path",
        "start_time",
        "filters",
        "n_bands",
        "n_framelets",
        "n_framelet_rows",
        "sample_bits",
        "quality_epoch",
    ]
    + [f"throughput_factor_{band}" for band in ("red", "green", "blue")]
    + list(METRIC_NAMES)
    + ["noise_mad_nonzero", "max_dn", "bloom_flag"]
    + [f"{metric}_{band}" for band in BANDS for metric in METRIC_NAMES]
    + ["quality_tier", "metrics_ok", "metrics_error"]
)
_FLOAT_COLUMNS = (
    [f"throughput_factor_{band}" for band in ("red", "green", "blue")]
    + list(METRIC_NAMES)
    + ["noise_mad_nonzero", "max_dn"]
    + [f"{metric}_{band}" for band in BANDS for metric in METRIC_NAMES]
)
_INT_COLUMNS = {
    "orbit": pa.int16(),
    "n_bands": pa.int16(),
    "n_framelets": pa.int32(),
    "n_framelet_rows": pa.int32(),
    "sample_bits": pa.int16(),
}
QUALITY_SCHEMA = pa.schema(
    [
        (
            column,
            pa.timestamp("ns")
            if column == "start_time"
            else _INT_COLUMNS.get(column)
            or (
                pa.float64()
                if column in _FLOAT_COLUMNS
                else pa.bool_()
                if column in {"bloom_flag", "metrics_ok"}
                else pa.string()
            ),
        )
        for column in QUALITY_COLUMNS
    ]
)


# --------------------------------------------------------------------------
# documented epochs and throughput, from the YAML
# --------------------------------------------------------------------------


def config_path(path: str | Path | None = None) -> Path:
    """Where ``configs/junocam_quality.yaml`` lives, honouring the env var."""
    if path is not None:
        return Path(path).expanduser()
    from_env = os.environ.get(CONFIG_ENV)
    if from_env:
        return Path(from_env).expanduser()
    packaged = Path(__file__).resolve().parents[3] / "configs" / "junocam_quality.yaml"
    if packaged.is_file():
        return packaged
    return Path("configs/junocam_quality.yaml")


@lru_cache(maxsize=8)
def _load_config_cached(location: str) -> dict[str, Any]:
    path = Path(location)
    if not path.is_file():
        raise FileNotFoundError(f"JunoCam quality configuration not found: {path}")
    document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not document.get("epochs"):
        raise ValueError(f"{path}: no epochs defined")
    return document


def load_quality_config(path: str | Path | None = None) -> dict[str, Any]:
    """Read (and cache) the ERRATA transcription."""
    return _load_config_cached(str(config_path(path).resolve()))


def epoch_for_orbit(orbit: int | None, path: str | Path | None = None) -> str:
    """Name the documented instrument-health epoch of an orbit.

    The intervals are closed and read in file order, so the first one that
    contains the orbit wins; an orbit outside all of them is ``unknown``
    rather than silently ``nominal``.
    """
    if orbit is None or (isinstance(orbit, float) and math.isnan(orbit)):
        return UNKNOWN_EPOCH
    value = int(orbit)
    for entry in load_quality_config(path)["epochs"]:
        if int(entry["orbit_min"]) <= value <= int(entry["orbit_max"]):
            return str(entry["name"])
    return UNKNOWN_EPOCH


def throughput_factors(
    orbit: int | None, path: str | Path | None = None
) -> dict[str, float]:
    """Per-band response factor for an orbit; 1.0 outside documented ranges.

    Inside a band's documented range the ERRATA's own linear fit is used,
    ``intercept + slope * orbit``, clipped to ``(0, 1]``.  Outside it the
    factor is exactly 1.0: the ERRATA quotes no loss there and its fit is not
    extrapolated past the orbits it was measured on.
    """
    factors = {band: 1.0 for band in ("red", "green", "blue")}
    if orbit is None or (isinstance(orbit, float) and math.isnan(orbit)):
        return factors
    value = int(orbit)
    for entry in load_quality_config(path).get("throughput", []):
        band = str(entry["band"]).lower()
        if band not in factors:
            continue
        if int(entry["orbit_min"]) <= value <= int(entry["orbit_max"]):
            factor = float(entry["intercept"]) + float(entry["slope"]) * value
            factors[band] = float(min(1.0, max(0.0, factor)))
    return factors


def quality_tier(
    epoch: str, streak_index: float | None, saturation_frac: float | None
) -> str:
    """Apply the A/B/C rule documented in the module docstring."""
    if epoch in DAMAGE_EPOCHS or epoch not in HEALTHY_EPOCHS:
        return "C"
    values = (streak_index, saturation_frac)
    if any(value is None or not math.isfinite(float(value)) for value in values):
        return "B"
    if float(streak_index) < STREAK_INDEX_MAX_A and float(saturation_frac) < SATURATION_FRAC_MAX_A:
        return "A"
    return "B"


# --------------------------------------------------------------------------
# measured metrics
# --------------------------------------------------------------------------


def image_dtype(sample_bits: int, sample_type: str = "UNSIGNED_INTEGER") -> np.dtype:
    """NumPy dtype for a JunoCam sample.

    PDS3 spells byte order in ``SAMPLE_TYPE``: a bare ``UNSIGNED_INTEGER`` is
    most-significant-byte first, and only an ``LSB_``/``PC_`` prefix makes it
    little-endian.  JunoCam RDRs are the bare (big-endian) 16-bit form.
    """
    text = str(sample_type or "").upper()
    if int(sample_bits) <= 8:
        return np.dtype(np.uint8)
    little = text.startswith("LSB") or text.startswith("PC_") or "LSB_" in text
    return np.dtype("<u2") if little else np.dtype(">u2")


def read_image(
    path: str | Path, lines: int, samples: int, sample_bits: int, sample_type: str
) -> np.ndarray:
    """Read a JunoCam ``.IMG`` strip as a ``(lines, samples)`` array."""
    dtype = image_dtype(sample_bits, sample_type)
    data = np.fromfile(str(path), dtype=dtype)
    expected = int(lines) * int(samples)
    if data.size < expected:
        raise ValueError(f"image is short: {data.size} of {expected} samples")
    return data[:expected].reshape(int(lines), int(samples))


def framelets(image: np.ndarray, framelet_lines: int = FRAMELET_LINES) -> np.ndarray:
    """View the strip as ``(n_framelet_rows, framelet_lines, samples)``."""
    rows = image.shape[0] // framelet_lines
    if rows < 1:
        raise ValueError(f"image has fewer than {framelet_lines} lines")
    return image[: rows * framelet_lines].reshape(rows, framelet_lines, image.shape[1])


def streak_index(framelet: np.ndarray) -> float:
    """Variance of the line means over the total variance of the framelet."""
    values = np.asarray(framelet, dtype=np.float64)
    total = float(values.var())
    if not math.isfinite(total) or total <= 0.0:
        return 0.0
    return float(values.mean(axis=1).var() / total)


def noise_mad(
    framelet: np.ndarray,
    size: int = MEDIAN_FILTER_SIZE,
    dark_percentile: float = DARK_PERCENTILE,
    exclude_zero: bool = False,
) -> float:
    """MAD of the high-pass residual over the darkest ``dark_percentile``.

    The 5x5 median is exact and computed with strides only (no SciPy), and
    only on the windows centred on the dark pixels -- a fifth of the image --
    which is what keeps it affordable on a 15360-line strip.

    ``exclude_zero`` is what makes the companion column ``noise_mad_nonzero``
    differ from ``noise_mad``.  A perijove JunoCam frame is typically more
    than a fifth off-planet, so the darkest fifth of *all* pixels is a set of
    exact zeros whose high-pass residual is identically zero: ``noise_mad``
    then reports 0 truthfully but uninformatively.  Measuring the darkest
    fifth of the illuminated pixels instead recovers the read-noise estimate
    the metric is for.  Both are reported; neither replaces the other.
    """
    values = np.asarray(framelet, dtype=np.float32)
    radius = int(size) // 2
    population = values[values > 0] if exclude_zero else values
    if population.size == 0:
        return math.nan
    threshold = float(np.percentile(population, dark_percentile))
    mask = (values <= threshold) & (values > 0) if exclude_zero else values <= threshold
    if not mask.any():
        return math.nan
    padded = np.pad(values, radius, mode="edge")
    windows = sliding_window_view(padded, (int(size), int(size)))
    selected = windows[mask].reshape(-1, int(size) * int(size))
    residual = values[mask] - np.median(selected, axis=1)
    centre = float(np.median(residual))
    return float(np.median(np.abs(residual - centre)))


def saturation_fraction(framelet: np.ndarray, max_code: int) -> float:
    """Fraction of samples at the maximum code the label declares."""
    values = np.asarray(framelet)
    return float(np.count_nonzero(values >= max_code) / values.size)


def zero_fraction(framelet: np.ndarray) -> float:
    values = np.asarray(framelet)
    return float(np.count_nonzero(values == 0) / values.size)


def bloom_flag(
    framelet: np.ndarray,
    ratio: float = BLOOM_LINE_RATIO,
    fraction: float = BLOOM_LINE_FRACTION,
) -> bool:
    """True when a methane framelet's line means run away from their median."""
    line_means = np.asarray(framelet, dtype=np.float64).mean(axis=1)
    median = float(np.median(line_means))
    if not math.isfinite(median) or median <= 0.0:
        return False
    exceeding = float(np.count_nonzero(line_means > ratio * median) / line_means.size)
    return bool(exceeding > fraction)


def framelet_metrics(framelet: np.ndarray, max_code: int) -> dict[str, float]:
    return {
        "streak_index": streak_index(framelet),
        "noise_mad": noise_mad(framelet),
        "saturation_frac": saturation_fraction(framelet, max_code),
        "zero_frac": zero_fraction(framelet),
        "noise_mad_nonzero": noise_mad(framelet, exclude_zero=True),
    }


def _band_of(index: int, filters: Sequence[str]) -> str:
    if not filters:
        return "unknown"
    return str(filters[index % len(filters)]).strip().lower()


def measure_image(
    path: str | Path,
    lines: int,
    samples: int,
    sample_bits: int,
    sample_type: str,
    filters: Sequence[str],
) -> dict[str, Any]:
    """Measure every framelet of one image and aggregate by band and overall."""
    image = read_image(path, lines, samples, sample_bits, sample_type)
    stack = framelets(image)
    max_code = int(2 ** int(sample_bits) - 1)
    per_framelet: list[dict[str, float]] = []
    bands: list[str] = []
    bloom = False
    for position in range(stack.shape[0]):
        framelet = stack[position]
        band = _band_of(position, filters)
        per_framelet.append(framelet_metrics(framelet, max_code))
        bands.append(band)
        if band == "methane" and bloom_flag(framelet):
            bloom = True

    result: dict[str, Any] = {"max_dn": float(image.max()), "bloom_flag": bool(bloom)}
    nonzero = [
        row["noise_mad_nonzero"]
        for row in per_framelet
        if math.isfinite(row["noise_mad_nonzero"])
    ]
    result["noise_mad_nonzero"] = float(np.median(nonzero)) if nonzero else math.nan
    for metric in METRIC_NAMES:
        values = [row[metric] for row in per_framelet]
        finite = [value for value in values if math.isfinite(value)]
        result[metric] = float(np.median(finite)) if finite else math.nan
        for band in BANDS:
            band_values = [
                value
                for value, name in zip(values, bands)
                if name == band and math.isfinite(value)
            ]
            result[f"{metric}_{band}"] = (
                float(np.median(band_values)) if band_values else math.nan
            )
    return result


# --------------------------------------------------------------------------
# the quality table
# --------------------------------------------------------------------------


def quality_path(mirror: str | Path | None = None) -> Path:
    return junocam_root(mirror) / "index" / "junocam_quality.parquet"


def _empty_metrics() -> dict[str, Any]:
    result: dict[str, Any] = {
        "max_dn": math.nan,
        "noise_mad_nonzero": math.nan,
        "bloom_flag": False,
    }
    for metric in METRIC_NAMES:
        result[metric] = math.nan
        for band in BANDS:
            result[f"{metric}_{band}"] = math.nan
    return result


def quality_row(row: Mapping[str, Any], root: Path) -> dict[str, Any]:
    """Build one quality record: documented flags plus measured metrics."""
    orbit = None if pd.isna(row.get("orbit")) else int(row["orbit"])
    epoch = epoch_for_orbit(orbit)
    factors = throughput_factors(orbit)
    record: dict[str, Any] = {
        "product_id": str(row.get("product_id", "")),
        "level": str(row.get("level", "")),
        "volume": str(row.get("volume", "")),
        "orbit": orbit,
        "doy_dir": str(row.get("doy_dir", "")),
        "path": str(row.get("path", "")),
        "start_time": row.get("start_time"),
        "filters": str(row.get("filters", "")),
        "n_bands": None if pd.isna(row.get("n_bands")) else int(row["n_bands"]),
        "n_framelets": None if pd.isna(row.get("n_framelets")) else int(row["n_framelets"]),
        "n_framelet_rows": (
            None if pd.isna(row.get("n_framelet_rows")) else int(row["n_framelet_rows"])
        ),
        "sample_bits": None if pd.isna(row.get("sample_bits")) else int(row["sample_bits"]),
        "quality_epoch": epoch,
        "throughput_factor_red": factors["red"],
        "throughput_factor_green": factors["green"],
        "throughput_factor_blue": factors["blue"],
        "metrics_ok": False,
        "metrics_error": "",
    }
    record.update(_empty_metrics())
    image_path = Path(str(row.get("path", "")))
    if not image_path.is_absolute():
        image_path = root / image_path
    try:
        filters = [value for value in str(row.get("filters", "")).split(";") if value]
        record.update(
            measure_image(
                image_path,
                int(row["lines"]),
                int(row["samples"]),
                int(row["sample_bits"]),
                str(row.get("sample_type", "")),
                filters,
            )
        )
        record["metrics_ok"] = True
    except Exception as exc:
        record["metrics_error"] = " ".join(f"{type(exc).__name__}: {exc}".split())
        LOGGER.debug("metrics failed for %s: %s", image_path, record["metrics_error"])
    record["quality_tier"] = quality_tier(
        epoch, record["streak_index"], record["saturation_frac"]
    )
    return record


def _quality_task(task: tuple[dict[str, Any], str]) -> dict[str, Any]:
    row, root = task
    return quality_row(row, Path(root))


def coerce_quality(df: pd.DataFrame) -> pd.DataFrame:
    result = df.reindex(columns=QUALITY_COLUMNS).copy()
    for column in _INT_COLUMNS:
        dtype = {"orbit": "Int16", "n_bands": "Int16", "sample_bits": "Int16"}.get(
            column, "Int32"
        )
        result[column] = pd.to_numeric(result[column], errors="coerce").astype(dtype)
    for column in _FLOAT_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce").astype("float64")
    parsed = pd.to_datetime(result["start_time"], errors="coerce")
    if getattr(parsed.dt, "tz", None) is not None:
        parsed = parsed.dt.tz_convert(None)
    result["start_time"] = parsed
    for column in ("bloom_flag", "metrics_ok"):
        result[column] = result[column].fillna(False).astype("bool")
    for column in set(QUALITY_COLUMNS) - set(_FLOAT_COLUMNS) - set(_INT_COLUMNS) - {
        "start_time",
        "bloom_flag",
        "metrics_ok",
    }:
        result[column] = result[column].fillna("").astype("str")
    return result


def _write_quality(df: pd.DataFrame, path: Path) -> None:
    arrays = [
        pa.array(df[column], type=QUALITY_SCHEMA.field(column).type, from_pandas=True)
        for column in QUALITY_COLUMNS
    ]
    temporary = path.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_arrays(arrays, schema=QUALITY_SCHEMA), temporary)
    temporary.replace(path)


def build_quality(
    mirror: str | Path | None = None,
    orbits: Iterable[int] | None = None,
    jobs: int | None = None,
) -> pd.DataFrame:
    """Measure every mirrored RDR image of the selected orbits."""
    root = junocam_root(mirror)
    output = quality_path(mirror)
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = coerce_quality(pd.read_parquet(output)) if output.exists() else coerce_quality(
        pd.DataFrame({column: pd.Series(dtype="object") for column in QUALITY_COLUMNS})
    )

    images = load_images(mirror, orbits)
    selected = (
        sorted(int(value) for value in images["orbit"].dropna().unique())
        if orbits is None
        else sorted(set(int(value) for value in orbits))
    )
    wanted = images.loc[
        (images["level"] == "RDR") & images["img_present"] & images["parse_ok"]
    ]
    LOGGER.info("measuring %d mirrored RDR image(s)", len(wanted))
    tasks = [(row, str(root)) for row in wanted.to_dict(orient="records")]
    worker_count = min(8, os.cpu_count() or 1) if jobs is None else int(jobs)
    if worker_count < 1:
        raise ValueError("--jobs must be at least 1")
    if worker_count == 1 or len(tasks) < 2:
        records = [_quality_task(task) for task in tasks]
    else:
        with Pool(processes=worker_count) as pool:
            records = pool.map(_quality_task, tasks, chunksize=1)

    new = coerce_quality(pd.DataFrame.from_records(records)) if records else existing.iloc[:0]
    kept = existing.loc[~existing["orbit"].isin(selected)]
    combined = pd.concat([kept, new], ignore_index=True)
    combined = combined.drop_duplicates("product_id", keep="last")
    combined = combined.sort_values(
        ["orbit", "start_time", "product_id"],
        kind="stable",
        na_position="last",
        ignore_index=True,
    )
    combined = coerce_quality(combined)
    _write_quality(combined, output)
    LOGGER.info("wrote %d quality rows to %s", len(combined), output)
    return combined


def load_quality(
    mirror: str | Path | None = None, orbits: Iterable[int] | None = None
) -> pd.DataFrame:
    path = quality_path(mirror)
    if not path.exists():
        raise FileNotFoundError(f"JunoCam quality table not found: {path}")
    table = coerce_quality(pd.read_parquet(path))
    if orbits is not None:
        table = table.loc[table["orbit"].isin(sorted(set(int(v) for v in orbits)))]
    return table.reset_index(drop=True)


def quality_summary(df: pd.DataFrame) -> str:
    lines = [f"quality rows: {len(df)}", f"metric failures: {int((~df['metrics_ok']).sum())}"]
    for epoch, count in sorted(df["quality_epoch"].value_counts().items()):
        lines.append(f"  epoch {epoch}: {count}")
    for tier, count in sorted(df["quality_tier"].value_counts().items()):
        lines.append(f"  tier {tier}: {count}")
    lines.append(f"  bloom_flag: {int(df['bloom_flag'].sum())}")
    lines.append("metric            min       p50       max")
    for metric in METRIC_NAMES + ("noise_mad_nonzero", "max_dn"):
        values = pd.to_numeric(df[metric], errors="coerce").dropna()
        if values.empty:
            lines.append(f"{metric:14s}   (no finite values)")
            continue
        lines.append(
            f"{metric:14s} {values.min():9.4g} {values.median():9.4g} {values.max():9.4g}"
        )
    return "\n".join(lines)
