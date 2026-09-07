"""``junocam_images.parquet``: one row per mirrored JunoCam label.

The index is built from the manifest's every-version file table rather than
from a directory walk, so a row always carries the volume and archive path it
came from even when the label's own keywords are sparse, a product mirrored
but absent from the manifest cannot silently appear, and a reprocessed
product appears once per version -- the ``_V01`` the archive still serves and
the ``_V02`` that superseded it are different labels and get different rows.

Rows for the orbits being rebuilt replace the previous ones; every other orbit
already in the table is left alone, so the index grows orbit by orbit as the
mirror does.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from multiprocessing import Pool
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .labels import (
    BOOLEAN_COLUMNS,
    DATETIME_COLUMNS,
    FLOAT_COLUMNS,
    IMAGE_ROW_COLUMNS,
    INTEGER_DTYPES,
    STRING_COLUMNS,
    parse_label,
)
from .mirror import junocam_root, mirrored_label_paths
from .pds import load_manifest_files

LOGGER = logging.getLogger(__name__)

ARROW_TYPES: dict[str, pa.DataType] = {
    **{column: pa.string() for column in STRING_COLUMNS},
    **{
        column: {"Int16": pa.int16(), "Int32": pa.int32(), "Int64": pa.int64()}[dtype]
        for column, dtype in INTEGER_DTYPES.items()
    },
    **{column: pa.bool_() for column in BOOLEAN_COLUMNS},
    **{column: pa.timestamp("ns") for column in DATETIME_COLUMNS},
    **{column: pa.float64() for column in FLOAT_COLUMNS},
}
IMAGE_SCHEMA = pa.schema([(column, ARROW_TYPES[column]) for column in IMAGE_ROW_COLUMNS])


def default_jobs() -> int:
    return min(8, os.cpu_count() or 1)


def index_path(mirror: str | Path | None = None) -> Path:
    return junocam_root(mirror) / "index" / "junocam_images.parquet"


def coerce_images(df: pd.DataFrame) -> pd.DataFrame:
    """Apply the exact index schema and column order before Parquet output."""
    result = df.reindex(columns=IMAGE_ROW_COLUMNS).copy()
    for column, dtype in INTEGER_DTYPES.items():
        result[column] = pd.to_numeric(result[column], errors="coerce").astype(dtype)
    for column in FLOAT_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="coerce").astype("float64")
    for column in DATETIME_COLUMNS:
        parsed = pd.to_datetime(result[column], errors="coerce")
        if getattr(parsed.dt, "tz", None) is not None:
            parsed = parsed.dt.tz_convert(None)
        result[column] = parsed
    for column in BOOLEAN_COLUMNS:
        result[column] = result[column].fillna(False).astype("bool")
    for column in STRING_COLUMNS:
        result[column] = result[column].fillna("").astype("str")
    return result


def _empty_images() -> pd.DataFrame:
    return coerce_images(
        pd.DataFrame({column: pd.Series(dtype="object") for column in IMAGE_ROW_COLUMNS})
    )


def _parse_task(task: tuple[str, str, str, str]) -> dict[str, Any]:
    label, root, volume, file_spec = task
    return parse_label(label, root, volume, file_spec)


def _write_images(df: pd.DataFrame, path: Path) -> None:
    arrays = [
        pa.array(df[column], type=IMAGE_SCHEMA.field(column).type, from_pandas=True)
        for column in IMAGE_ROW_COLUMNS
    ]
    temporary = path.with_suffix(".parquet.tmp")
    pq.write_table(pa.Table.from_arrays(arrays, schema=IMAGE_SCHEMA), temporary)
    temporary.replace(path)


def build_index(
    mirror: str | Path | None = None,
    orbits: Iterable[int] | None = None,
    jobs: int | None = None,
) -> pd.DataFrame:
    """Parse the mirrored labels of the selected orbits into the index."""
    root = junocam_root(mirror)
    output = index_path(mirror)
    output.parent.mkdir(parents=True, exist_ok=True)
    existing = coerce_images(pd.read_parquet(output)) if output.exists() else _empty_images()

    manifest = load_manifest_files(mirror)
    if orbits is None:
        selected = sorted(int(value) for value in manifest["orbit"].dropna().unique())
    else:
        selected = sorted(set(int(value) for value in orbits))
    wanted = manifest.loc[manifest["orbit"].isin(selected)]

    found = mirrored_label_paths(wanted, root)
    LOGGER.info(
        "parsing %d mirrored label(s) of %d selected product(s) in %d orbit(s)",
        len(found),
        len(wanted),
        len(selected),
    )
    tasks = [
        (str(path), str(root), str(row["volume"]), str(row["file_spec"]))
        for path, row in found
    ]
    worker_count = default_jobs() if jobs is None else int(jobs)
    if worker_count < 1:
        raise ValueError("--jobs must be at least 1")
    if worker_count == 1 or len(tasks) < 2:
        records = [_parse_task(task) for task in tasks]
    else:
        with Pool(processes=worker_count) as pool:
            records = pool.map(_parse_task, tasks, chunksize=8)

    new = coerce_images(pd.DataFrame.from_records(records)) if records else _empty_images()
    kept = existing.loc[~existing["orbit"].isin(selected)]
    combined = pd.concat([kept, new], ignore_index=True)
    combined = combined.drop_duplicates("product_id", keep="last")
    combined = combined.sort_values(
        ["orbit", "level", "start_time", "product_id"],
        kind="stable",
        na_position="last",
        ignore_index=True,
    )
    combined = coerce_images(combined)
    _write_images(combined, output)
    LOGGER.info("wrote %d index rows to %s", len(combined), output)
    return combined


def load_images(
    mirror: str | Path | None = None, orbits: Iterable[int] | None = None
) -> pd.DataFrame:
    path = index_path(mirror)
    if not path.exists():
        raise FileNotFoundError(f"JunoCam index not found: {path} (run `junocam index`)")
    images = coerce_images(pd.read_parquet(path))
    if orbits is not None:
        selected = sorted(set(int(value) for value in orbits))
        images = images.loc[images["orbit"].isin(selected)]
    return images.reset_index(drop=True)


def index_summary(df: pd.DataFrame, orbits: Iterable[int]) -> str:
    selected = sorted(set(int(value) for value in orbits))
    part = df.loc[df["orbit"].isin(selected)] if selected else df
    lines = [
        f"index rows: {len(part)}",
        f"parse failures: {int((~part['parse_ok']).sum())}",
        f"framelet-count mismatches: {int((~part['framelets_exact']).sum())}",
        f"images present: {int(part['img_present'].sum())}",
        "orbit  level  rows  images  bytes",
    ]
    for (orbit, level), group in part.groupby(["orbit", "level"], sort=True, dropna=False):
        present = group.loc[group["img_present"]]
        lines.append(
            f"{int(orbit):5d}  {str(level):5s}  {len(group):4d}  {len(present):6d}  "
            f"{int(present['img_size_bytes'].fillna(0).sum()):d}"
        )
    return "\n".join(lines)
