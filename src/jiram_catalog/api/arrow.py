"""DataFrame to Arrow IPC bytes, with the contract's column types.

The catalog is 47,600 rows wide enough that JSON would cost the browser
several megabytes and a parse; an Arrow IPC stream is the same table in a
tenth of the bytes and lands in the front end as typed columns.  The
schema is written out explicitly rather than inferred, because the
contract names the type of every column and the front end indexes into
the buffers by that type.
"""

from __future__ import annotations

import io
import logging
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.ipc as ipc

LOGGER = logging.getLogger(__name__)

#: MIME type of an Arrow IPC stream.
ARROW_MEDIA_TYPE = "application/vnd.apache.arrow.stream"


def to_ipc(table: pa.Table) -> bytes:
    """Serialise an Arrow table as one IPC stream."""
    sink = io.BytesIO()
    with ipc.new_stream(sink, table.schema) as writer:
        writer.write_table(table)
    return sink.getvalue()


def _column(values: Any, dtype: pa.DataType) -> pa.Array:
    """One column cast to ``dtype``, with pandas' missing values honoured."""
    series = pd.Series(values)
    if pa.types.is_string(dtype):
        text = series.astype(object).where(series.notna(), None)
        return pa.array(text.tolist(), type=pa.string())
    if pa.types.is_boolean(dtype):
        return pa.array(series.fillna(False).astype(bool).to_numpy(), type=pa.bool_())
    if pa.types.is_floating(dtype):
        numeric = pd.to_numeric(series, errors="coerce").to_numpy(dtype=np.float64, na_value=np.nan)
        # NaN, not null: the contract says "NaN if missing", and a NaN
        # survives the trip into a JavaScript typed array as a NaN while
        # a null would need a validity buffer the front end must consult.
        return pa.array(numeric.astype(np.float32 if pa.types.is_float32(dtype) else np.float64))
    if pa.types.is_integer(dtype):
        numeric = pd.to_numeric(series, errors="coerce")
        return pa.array(numeric.fillna(0).to_numpy(dtype=np.int64), type=dtype)
    return pa.array(series.tolist(), type=dtype)


def frame_to_table(frame: pd.DataFrame, schema: pa.Schema) -> pa.Table:
    """Build the table ``schema`` describes out of ``frame``.

    Columns the frame does not carry become all-missing columns of the
    right type, so a mirror without the trackability table still answers
    with the contract's shape instead of a short table the front end
    would have to test for.
    """
    arrays = []
    for field in schema:
        if field.name in frame.columns:
            arrays.append(_column(frame[field.name], field.type))
        else:
            LOGGER.debug("column %s is absent; filling with missing", field.name)
            arrays.append(_column(pd.Series([None] * len(frame)), field.type))
    return pa.Table.from_arrays(arrays, schema=schema)


def frame_to_ipc(frame: pd.DataFrame, schema: pa.Schema) -> bytes:
    """:func:`frame_to_table` serialised as an IPC stream."""
    return to_ipc(frame_to_table(frame, schema))


def epoch_ms(values: Any) -> np.ndarray:
    """Datetimes as int64 epoch milliseconds UTC; ``NaT`` becomes ``0``.

    ``pandas`` carries nanoseconds and the browser's ``Date`` carries
    milliseconds, so the conversion has to happen somewhere; doing it
    here means the front end never sees a unit it has to guess.
    """
    times = pd.to_datetime(pd.Series(values), errors="coerce")
    if getattr(times.dtype, "tz", None) is not None:
        times = times.dt.tz_convert("UTC").dt.tz_localize(None)
    # ``as_unit`` first: a parquet column may carry seconds, microseconds or
    # nanoseconds, and ``astype("int64")`` would then hand back that unit's
    # count rather than milliseconds -- a silent factor of a thousand.
    counts = times.dt.as_unit("ms").astype("int64").to_numpy()
    return np.where(times.isna().to_numpy(), 0, counts).astype(np.int64)


def infer_schema(frame: pd.DataFrame) -> pa.Schema:
    """A schema for an arbitrary table: datetimes as ``<name>_ms`` int64.

    Used for the strip index, whose columns the contract does not
    enumerate; every other type is left to ``pyarrow``'s inference.
    """
    fields: list[pa.Field] = []
    for name in frame.columns:
        column = frame[name]
        if pd.api.types.is_datetime64_any_dtype(column):
            fields.append(pa.field(f"{name}_ms", pa.int64()))
        elif pd.api.types.is_bool_dtype(column):
            fields.append(pa.field(str(name), pa.bool_()))
        elif pd.api.types.is_integer_dtype(column):
            fields.append(pa.field(str(name), pa.int64()))
        elif pd.api.types.is_float_dtype(column):
            fields.append(pa.field(str(name), pa.float64()))
        else:
            fields.append(pa.field(str(name), pa.string()))
    return pa.schema(fields)


def generic_ipc(frame: pd.DataFrame) -> bytes:
    """An arbitrary table as IPC, datetimes converted to ``<name>_ms``."""
    prepared = pd.DataFrame(index=range(len(frame)))
    for name in frame.columns:
        column = frame[name]
        if pd.api.types.is_datetime64_any_dtype(column):
            prepared[f"{name}_ms"] = epoch_ms(column)
        else:
            prepared[str(name)] = column.to_numpy()
    return frame_to_ipc(prepared, infer_schema(frame))
