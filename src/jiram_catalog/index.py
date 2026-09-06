"""Frame-table construction, sequence segmentation, loading, and statistics."""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Iterable
from multiprocessing import Pool
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .labels import (
    BOOLEAN_COLUMNS,
    DATETIME_COLUMNS,
    FLOAT_COLUMNS,
    FRAME_COLUMNS,
    INTEGER_DTYPES,
    STRING_COLUMNS,
    parse_label,
)
from .pds import mirror_root

LOGGER = logging.getLogger(__name__)

ARROW_TYPES: dict[str, pa.DataType] = {
    **{column: pa.string() for column in STRING_COLUMNS},
    "orbit": pa.int16(),
    "orbit_dir": pa.int16(),
    "img_size_bytes": pa.int64(),
    "sequence_number": pa.int16(),
    "sequence_samples": pa.int16(),
    "lines": pa.int32(),
    "samples": pa.int32(),
    "sample_bits": pa.int16(),
    "target_pixels": pa.int64(),
    "seq_index": pa.int32(),
    "seq_n": pa.int32(),
    **{column: pa.bool_() for column in BOOLEAN_COLUMNS},
    **{column: pa.timestamp("ns") for column in DATETIME_COLUMNS},
    **{column: pa.float64() for column in FLOAT_COLUMNS},
}
FRAME_SCHEMA = pa.schema([(column, ARROW_TYPES[column]) for column in FRAME_COLUMNS])


def default_index_jobs() -> int:
    return min(8, os.cpu_count() or 1)


def _empty_frames() -> pd.DataFrame:
    frame = pd.DataFrame({column: pd.Series(dtype="object") for column in FRAME_COLUMNS})
    return coerce_frames(frame)


def coerce_frames(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the exact frame schema and column order before Parquet output."""
    result = df.reindex(columns=FRAME_COLUMNS).copy()
    for column, dtype in INTEGER_DTYPES.items():
        result[column] = pd.to_numeric(result[column], errors="coerce").astype(dtype)
    for column in FLOAT_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce").astype("float64")
    for column in DATETIME_COLUMNS:
        result[column] = pd.to_datetime(result[column], errors="coerce")
        if getattr(result[column].dt, "tz", None) is not None:
            result[column] = result[column].dt.tz_convert(None)
    for column in BOOLEAN_COLUMNS:
        result[column] = result[column].astype("boolean")
    for column in STRING_COLUMNS:
        result[column] = result[column].astype("string")
    return result


def assign_sequences(df: pd.DataFrame) -> pd.DataFrame:
    """Recompute sequence columns across the complete combined table."""
    result = coerce_frames(df)
    result["seq_id"] = ""
    result["seq_index"] = pd.Series(-1, index=result.index, dtype="Int32")
    result["seq_n"] = pd.Series(0, index=result.index, dtype="Int32")
    result["seq_gap_s"] = np.nan

    valid = result.loc[result["parse_ok"].fillna(False)].copy()
    valid = valid.loc[valid["orbit_dir"].notna() & valid["band"].notna()]
    valid = valid.sort_values(
        ["orbit_dir", "band", "start_time", "product_id"],
        kind="stable",
        na_position="last",
    )
    for (orbit, band), group in valid.groupby(
        ["orbit_dir", "band"], sort=False, dropna=False
    ):
        sequence_indices: list[Any] = []
        sequence_id = ""
        previous_time: pd.Timestamp | None = None
        previous_number: int | None = None

        def finish_sequence() -> None:
            if sequence_indices:
                result.loc[sequence_indices, "seq_n"] = len(sequence_indices)

        for index_value, row in group.iterrows():
            current_time = row["start_time"]
            current_number_value = row["sequence_number"]
            current_number = (
                None if pd.isna(current_number_value) else int(current_number_value)
            )
            gap = math.nan
            if previous_time is not None and not pd.isna(current_time):
                gap = (pd.Timestamp(current_time) - previous_time).total_seconds()
            begins = not sequence_indices
            if not begins:
                begins = (
                    (not math.isnan(gap) and gap > 45.0)
                    or current_number is None
                    or (
                        previous_number is not None
                        and current_number is not None
                        and current_number <= previous_number
                    )
                )
            if begins:
                finish_sequence()
                sequence_indices = []
                if pd.isna(current_time):
                    sequence_id = ""
                else:
                    timestamp = pd.Timestamp(current_time).strftime("%Y%jT%H%M%S")
                    sequence_id = f"{int(orbit):02d}_{band}_{timestamp}"
                gap = math.nan
            result.at[index_value, "seq_id"] = sequence_id
            result.at[index_value, "seq_index"] = len(sequence_indices)
            result.at[index_value, "seq_gap_s"] = gap
            sequence_indices.append(index_value)
            previous_time = None if pd.isna(current_time) else pd.Timestamp(current_time)
            previous_number = current_number
        finish_sequence()
    return coerce_frames(result)


def _orbit_dirs(root: Path) -> list[int]:
    archive = root / "pds4" / "juno_jiram_bundle" / "data_calibrated"
    result: list[int] = []
    if not archive.exists():
        return result
    for path in archive.glob("orbit[0-9][0-9]"):
        if path.is_dir():
            result.append(int(path.name[-2:]))
    return sorted(set(result))


def _parse_task(task: tuple[str, str, int]) -> dict[str, Any]:
    label, root, orbit = task
    return parse_label(label, root, orbit)


def _write_frames(df: pd.DataFrame, path: Path) -> None:
    arrays = [
        pa.array(df[column], type=FRAME_SCHEMA.field(column).type, from_pandas=True)
        for column in FRAME_COLUMNS
    ]
    table = pa.Table.from_arrays(arrays, schema=FRAME_SCHEMA)
    temporary = path.with_suffix(".parquet.tmp")
    pq.write_table(table, temporary)
    temporary.replace(path)


def build_index(
    mirror: str | Path | None = None,
    orbits: Iterable[int] | None = None,
    jobs: int | None = None,
) -> pd.DataFrame:
    """Parse selected mirrored IMG labels, merge, segment, and write frames."""
    root = mirror_root(mirror)
    archive = root / "pds4" / "juno_jiram_bundle" / "data_calibrated"
    output_dir = root / "index"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / "frames.parquet"
    existing = pd.read_parquet(output_path) if output_path.exists() else _empty_frames()
    existing = coerce_frames(existing)

    if orbits is None:
        selected = sorted(
            set(_orbit_dirs(root))
            | set(
                int(value)
                for value in existing["orbit_dir"].dropna().astype(int).tolist()
            )
        )
    else:
        selected = sorted(set(int(value) for value in orbits))

    labels: list[tuple[str, str, int]] = []
    for orbit in selected:
        orbit_dir = archive / f"orbit{orbit:02d}"
        labels.extend(
            (str(path), str(root), orbit)
            for path in sorted(orbit_dir.glob("JIR_IMG_RDR_*.LBL"))
        )
    LOGGER.info("parsing %d IMG labels from %d orbit(s)", len(labels), len(selected))
    worker_count = default_index_jobs() if jobs is None else jobs
    if worker_count < 1:
        raise ValueError("--jobs must be at least 1")
    if worker_count == 1 or len(labels) < 2:
        records = [_parse_task(task) for task in labels]
    else:
        with Pool(processes=worker_count) as pool:
            records = pool.map(_parse_task, labels)
    new = coerce_frames(pd.DataFrame.from_records(records)) if records else _empty_frames()
    kept = existing.loc[~existing["orbit_dir"].isin(selected)]
    combined = pd.concat([kept, new], ignore_index=True)
    combined = assign_sequences(combined)
    combined = combined.sort_values(
        ["orbit_dir", "start_time", "product_id"],
        kind="stable",
        na_position="last",
        ignore_index=True,
    )
    combined = coerce_frames(combined)
    _write_frames(combined, output_path)
    LOGGER.info("wrote %d frame rows to %s", len(combined), output_path)
    return combined


def load_frames(
    mirror: str | Path | None = None,
    orbits: Iterable[int] | None = None,
) -> pd.DataFrame:
    """Load the frame index, optionally retaining only selected orbit dirs."""
    path = mirror_root(mirror) / "index" / "frames.parquet"
    if not path.exists():
        raise FileNotFoundError(f"frame index not found: {path}")
    frames = coerce_frames(pd.read_parquet(path))
    if orbits is not None:
        selected = sorted(set(int(value) for value in orbits))
        frames = frames.loc[frames["orbit_dir"].isin(selected)]
    return frames.reset_index(drop=True)


def index_summary(df: pd.DataFrame, orbits: Iterable[int]) -> str:
    """Format the index command's compact build summary."""
    selected = sorted(set(int(value) for value in orbits))
    part = df.loc[df["orbit_dir"].isin(selected)]
    bad = int((~part["parse_ok"].fillna(False)).sum())
    lines = [f"rows: {len(part)}", f"parse failures: {bad}", "orbit  band  frames"]
    counts = (
        part.groupby(["orbit_dir", "band"], dropna=False)
        .size()
        .reset_index(name="frames")
    )
    for row in counts.itertuples(index=False):
        band = "null" if pd.isna(row.band) else str(row.band)
        lines.append(f"{int(row.orbit_dir):5d}  {band:4s}  {int(row.frames):6d}")
    sequences = part.loc[part["seq_id"].fillna("") != "", "seq_id"].nunique()
    lines.append(f"sequences: {sequences}")
    return "\n".join(lines)


def _time_text(value: Any) -> str:
    return "" if pd.isna(value) else pd.Timestamp(value).isoformat()


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(str(item) for item in row) + " |" for row in rows)
    return lines


def stats_text(df: pd.DataFrame) -> str:
    """Produce the complete human-readable statistics report."""
    lines = ["# JIRAM frame statistics", "", f"Total frames: {len(df)}", ""]
    lines.append("## Frames by band")
    lines.append("")
    band_counts = df["band"].fillna("null").value_counts(sort=False).sort_index()
    lines.extend(_markdown_table(["band", "frames"], [[key, int(value)] for key, value in band_counts.items()]))

    lines.extend(["", "## Per-orbit summary", ""])
    orbit_rows: list[list[Any]] = []
    for orbit, group in df.groupby("orbit_dir", sort=True, dropna=True):
        bands = group["band"].value_counts()
        sequences = group.loc[group["seq_id"].fillna("") != "", "seq_id"].nunique()
        orbit_rows.append(
            [
                int(orbit),
                len(group),
                int(bands.get("L", 0)),
                int(bands.get("M", 0)),
                sequences,
                _time_text(group["start_time"].min()),
                _time_text(group["start_time"].max()),
                int(group["img_size_bytes"].sum(skipna=True)),
            ]
        )
    lines.extend(
        _markdown_table(
            ["orbit", "frames", "L", "M", "sequences", "first start_time", "last start_time", "IMG bytes"],
            orbit_rows,
        )
    )

    sequence_first = (
        df.loc[df["seq_id"].fillna("") != ""]
        .sort_values(["seq_id", "seq_index"], kind="stable")
        .drop_duplicates("seq_id", keep="first")
    )
    sequence_lengths = sequence_first["seq_n"]
    length_bins = [
        ("1", sequence_lengths == 1),
        ("2", sequence_lengths == 2),
        ("3-5", sequence_lengths.between(3, 5)),
        ("6-10", sequence_lengths.between(6, 10)),
        ("11-20", sequence_lengths.between(11, 20)),
        ("21-50", sequence_lengths.between(21, 50)),
        ("51+", sequence_lengths >= 51),
    ]
    lines.extend(["", "## Sequence-length histogram", ""])
    lines.extend(_markdown_table(["seq_n", "sequences"], [[name, int(mask.sum())] for name, mask in length_bins]))

    gaps = df["seq_gap_s"].dropna()
    gap_bins = [
        ("<25", gaps < 25),
        ("25-35", (gaps >= 25) & (gaps < 35)),
        ("35-45 s", (gaps >= 35) & (gaps <= 45)),
    ]
    lines.extend(["", "## Within-sequence gap histogram", ""])
    lines.extend(_markdown_table(["seq_gap_s", "frames"], [[name, int(mask.sum())] for name, mask in gap_bins]))

    comparable = sequence_first["sequence_samples"].notna()
    mismatches = int(
        (
            sequence_first.loc[comparable, "seq_n"]
            != sequence_first.loc[comparable, "sequence_samples"]
        ).sum()
    )
    center_null = int(df["center_lat"].isna().sum())
    polar_fraction = float((df["center_lat"].abs() >= 60).sum() / len(df)) if len(df) else math.nan
    lines.extend(
        [
            "",
            f"Sequences with seq_n different from first-frame sequence_samples: {mismatches}",
            f"Frames with null center_lat: {center_null}",
            f"Fraction of frames with abs(center_lat) >= 60: {polar_fraction:.6f}",
        ]
    )
    return "\n".join(lines) + "\n"
