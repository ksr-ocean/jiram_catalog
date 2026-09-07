"""PDS3 label parsing for JunoCam EDR/RDR products.

A JunoCam label is flat: ``RECORD_BYTES``/``FILE_RECORDS`` and a single
``OBJECT = IMAGE`` sit at the top level, with no enclosing ``FILE`` object of
the kind the JIRAM labels use.  One label yields one row; a failure stays a
row with ``parse_ok = False`` and the exception text in ``parse_error``, so a
bad label is visible in the index instead of silently missing from it.

Two derived quantities carry the framelet geometry.  An image is a vertical
strip of 128-line framelets in acquisition order with the bands cycling
within each frame in ``FILTER_NAME`` order, so

    n_framelet_rows = LINES / 128          (one per framelet, all bands)
    n_framelets     = LINES / 128 / n_bands  (frames, i.e. repeats)

``framelets_exact`` records whether ``LINES`` really divides; nothing is
rounded away silently.

``companded`` is not read straight off ``SAMPLE_BIT_MODE_ID``: both EDR and
RDR labels carry ``SAMPLE_BIT_MODE_ID = "SQROOT"``, but the RDR has already
inverted the onboard 12-to-8-bit companding and stores 16-bit linear DN.  The
flag is therefore true only when the mode is a companding mode *and* the
samples are still 8-bit.
"""

from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pvl

from .pds import parse_product_id

LOGGER = logging.getLogger(__name__)

#: One framelet is 128 detector lines (EDR/RDR SIS section 4.3.1.1).
FRAMELET_LINES = 128
#: Sample bit modes that leave the data companded.
COMPANDING_MODES = {"SQROOT"}

IDENTITY_COLUMNS = [
    "product_id",
    "product_stem",
    "level",
    "volume",
    "orbit",
    "orbit_from_id",
    "orbit_from_path",
    "doy_dir",
    "version",
    "filter_code",
    "path",
    "label_path",
    "file_spec",
    "target",
]
TIME_COLUMNS = [
    "start_time",
    "stop_time",
    "image_time",
    "duration_s",
    "sclk_start",
    "sclk_stop",
    "interframe_delay_s",
    "exposure_ms",
    "tdi_stages",
]
IMAGE_COLUMNS = [
    "filters",
    "n_bands",
    "n_framelets",
    "n_framelet_rows",
    "framelets_exact",
    "lines",
    "samples",
    "sample_bits",
    "sample_type",
    "sample_bit_mode_id",
    "companded",
    "record_bytes",
    "file_records",
    "img_file_name",
    "img_size_bytes",
    "img_present",
    "md5",
    "sampling_factor",
    "compression_type",
]
CONTEXT_COLUMNS = [
    "focal_plane_temperature_k",
    "sub_sc_lat",
    "sub_sc_lon",
    "sc_altitude_km",
    "solar_distance_km",
    "mission_phase_name",
    "processing_level_id",
    "data_set_id",
    "standard_data_product_id",
    "product_creation_time",
    "source_product_id",
    "software_name",
    "rationale_desc",
]
STATUS_COLUMNS = ["parse_ok", "parse_error", "label_json"]
IMAGE_ROW_COLUMNS = (
    IDENTITY_COLUMNS + TIME_COLUMNS + IMAGE_COLUMNS + CONTEXT_COLUMNS + STATUS_COLUMNS
)

INTEGER_DTYPES = {
    "orbit": "Int16",
    "orbit_from_id": "Int16",
    "orbit_from_path": "Int16",
    "version": "Int16",
    "tdi_stages": "Int16",
    "n_bands": "Int16",
    "n_framelets": "Int32",
    "n_framelet_rows": "Int32",
    "lines": "Int32",
    "samples": "Int32",
    "sample_bits": "Int16",
    "record_bytes": "Int32",
    "file_records": "Int32",
    "img_size_bytes": "Int64",
    "sampling_factor": "Int16",
}
BOOLEAN_COLUMNS = {"framelets_exact", "companded", "img_present", "parse_ok"}
DATETIME_COLUMNS = {"start_time", "stop_time", "image_time", "product_creation_time"}
FLOAT_COLUMNS = {
    "duration_s",
    "interframe_delay_s",
    "exposure_ms",
    "focal_plane_temperature_k",
    "sub_sc_lat",
    "sub_sc_lon",
    "sc_altitude_km",
    "solar_distance_km",
}
STRING_COLUMNS = set(IMAGE_ROW_COLUMNS) - (
    set(INTEGER_DTYPES) | BOOLEAN_COLUMNS | DATETIME_COLUMNS | FLOAT_COLUMNS
)


def _raw(value: Any) -> Any:
    return getattr(value, "value", value)


def _units(value: Any) -> str:
    return str(getattr(value, "units", "") or "").strip().lower()


def _number(value: Any) -> float:
    value = _raw(value)
    if value is None or (isinstance(value, str) and value.strip().upper() in {"", "N/A", "NULL"}):
        return math.nan
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _integer(value: Any) -> int | None:
    number = _number(value)
    return None if math.isnan(number) else int(number)


def _string(value: Any, *, normalize: bool = False) -> str | None:
    value = _raw(value)
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() in {"N/A", "NULL"}:
        return None
    return " ".join(text.split()) if normalize else text


def _datetime(value: Any) -> datetime | None:
    value = _raw(value)
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).strip()
        if not text or text.upper() in {"N/A", "NULL"}:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            return None
    if result.tzinfo is not None:
        result = result.astimezone(timezone.utc).replace(tzinfo=None)
    return result


def _seconds(value: Any) -> float:
    """A duration keyword in seconds, honouring the label's unit suffix."""
    number = _number(value)
    units = _units(value)
    if math.isnan(number):
        return math.nan
    if units in {"ms", "msec", "millisecond", "milliseconds"}:
        return number / 1000.0
    return number


def _milliseconds(value: Any) -> float:
    """A duration keyword in milliseconds, honouring the label's unit suffix."""
    number = _number(value)
    units = _units(value)
    if math.isnan(number):
        return math.nan
    if units in {"s", "sec", "second", "seconds"}:
        return number * 1000.0
    return number


def load_pvl(path: Path) -> Any:
    """Load a label, rejecting a truncated one the way the mirror check does."""
    try:
        module = pvl.load(path)
    except Exception:
        module = pvl.load(path, grammar=pvl.grammar.OmniGrammar())
    nonblank = [
        line.strip()
        for line in path.read_text(errors="replace").splitlines()
        if line.strip()
    ]
    if not nonblank or nonblank[-1] != "END":
        raise ValueError("label is truncated: last non-blank line is not END")
    return module


def _jsonable(value: Any) -> Any:
    value_raw = _raw(value)
    units = _units(value)
    if isinstance(value_raw, datetime):
        text = value_raw.isoformat()
        return f"{text} <{units}>" if units else text
    if isinstance(value_raw, (list, tuple)):
        return [_jsonable(item) for item in value_raw]
    if hasattr(value_raw, "items"):
        return {str(key): _jsonable(item) for key, item in value_raw.items()}
    if isinstance(value_raw, (int, float, bool)) or value_raw is None:
        return f"{value_raw} <{units}>" if units else value_raw
    text = " ".join(str(value_raw).split())
    return f"{text} <{units}>" if units else text


def label_to_json(module: Any) -> str:
    """Every keyword of the label, flattened one level per group, as JSON."""
    return json.dumps({str(key): _jsonable(value) for key, value in module.items()}, sort_keys=True)


def _filters(value: Any) -> list[str]:
    value = _raw(value)
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    else:
        try:
            items = list(value)
        except TypeError:
            items = [value]
    return [text for item in items if (text := _string(item))]


def framelet_counts(lines: int | None, n_bands: int) -> tuple[int | None, int | None, bool]:
    """``(n_framelet_rows, n_framelets, exact)`` from ``LINES`` and band count.

    ``n_framelets`` counts *frames* (repeats of the band cycle); the flag says
    whether ``LINES`` divides by ``128 * n_bands`` without remainder.
    """
    if lines is None or lines <= 0:
        return None, None, False
    rows = lines // FRAMELET_LINES
    exact_rows = lines % FRAMELET_LINES == 0
    if n_bands <= 0:
        return rows, None, False
    frames = rows // n_bands
    exact = exact_rows and rows % n_bands == 0
    return rows, frames, exact


def _empty_row(product_id: str) -> dict[str, Any]:
    row: dict[str, Any] = {column: None for column in IMAGE_ROW_COLUMNS}
    for column in FLOAT_COLUMNS:
        row[column] = np.nan
    row.update(
        product_id=product_id,
        parse_ok=False,
        parse_error="",
        label_json="",
        framelets_exact=False,
        companded=False,
        img_present=False,
    )
    return row


def _identity_from_product_id(product_id: str) -> dict[str, Any]:
    parsed = parse_product_id(product_id)
    if parsed is None:
        return {}
    return {
        "product_stem": parsed["stem"],
        "level": parsed["level"],
        "orbit_from_id": int(parsed["orbit"]),
        "doy_dir": parsed["doy"],
        "version": int(parsed["version"]),
        "filter_code": parsed["filter_code"],
    }


def parse_label(
    path: str | Path,
    root: str | Path | None = None,
    volume: str | None = None,
    file_spec: str | None = None,
) -> dict[str, Any]:
    """Parse one JunoCam label into a flat index row.

    ``root`` is the JunoCam mirror root (``<mirror>/junocam``); ``path`` and
    ``label_path`` in the row are relative to it so the index survives the
    mirror being moved.  ``volume``/``file_spec`` come from the manifest when
    available and are otherwise inferred from the file's own location.
    """
    label = Path(path)
    fallback_id = label.stem
    row = _empty_row(fallback_id)
    row.update(_identity_from_product_id(fallback_id))
    root_path = Path(root).resolve() if root is not None else None

    def relative(target: Path) -> str:
        if root_path is None:
            return target.name
        try:
            return str(target.resolve().relative_to(root_path))
        except (ValueError, OSError):
            return str(target)

    resolved_spec = file_spec or label.name
    row.update(
        volume=volume or "",
        file_spec=resolved_spec,
        label_path=relative(label),
        orbit_from_path=(
            int(match.group(1))
            if (match := re.search(r"ORBIT_(\d+)", resolved_spec.upper()))
            else None
        ),
    )
    try:
        module = load_pvl(label)
        image = module.get("IMAGE") or {}

        product_id = _string(module.get("PRODUCT_ID")) or fallback_id
        row.update(product_id=product_id)
        row.update(_identity_from_product_id(product_id))

        filters = _filters(module.get("FILTER_NAME"))
        lines = _integer(image.get("LINES"))
        rows_count, frames, exact = framelet_counts(lines, len(filters))
        record_bytes = _integer(module.get("RECORD_BYTES"))
        file_records = _integer(module.get("FILE_RECORDS"))
        img_size = (
            record_bytes * file_records
            if record_bytes is not None and file_records is not None
            else None
        )
        img_name = _string(module.get("^IMAGE")) or f"{product_id}.IMG"
        img_path = label.parent / Path(img_name).name
        sample_bits = _integer(image.get("SAMPLE_BITS"))
        mode_id = _string(module.get("SAMPLE_BIT_MODE_ID"))
        start_time = _datetime(module.get("START_TIME"))
        stop_time = _datetime(module.get("STOP_TIME"))
        orbit_label = _integer(module.get("ORBIT_NUMBER"))

        row.update(
            orbit=orbit_label if orbit_label is not None else row.get("orbit_from_id"),
            path=relative(img_path),
            target=_string(module.get("TARGET_NAME")),
            start_time=start_time,
            stop_time=stop_time,
            image_time=_datetime(module.get("IMAGE_TIME")),
            duration_s=(
                (stop_time - start_time).total_seconds()
                if start_time is not None and stop_time is not None
                else math.nan
            ),
            sclk_start=_string(module.get("SPACECRAFT_CLOCK_START_COUNT")),
            sclk_stop=_string(module.get("SPACECRAFT_CLOCK_STOP_COUNT")),
            interframe_delay_s=_seconds(module.get("INTERFRAME_DELAY")),
            exposure_ms=_milliseconds(module.get("EXPOSURE_DURATION")),
            tdi_stages=_integer(module.get("JNO:TDI_STAGES_COUNT")),
            filters=";".join(filters),
            n_bands=len(filters),
            n_framelets=frames,
            n_framelet_rows=rows_count,
            framelets_exact=bool(exact),
            lines=lines,
            samples=_integer(image.get("LINE_SAMPLES")),
            sample_bits=sample_bits,
            sample_type=_string(image.get("SAMPLE_TYPE")),
            sample_bit_mode_id=mode_id,
            companded=bool(
                mode_id is not None
                and mode_id.upper() in COMPANDING_MODES
                and sample_bits is not None
                and sample_bits <= 8
            ),
            record_bytes=record_bytes,
            file_records=file_records,
            img_file_name=Path(img_name).name,
            img_size_bytes=img_size,
            img_present=bool(
                img_size is not None
                and img_path.is_file()
                and img_path.stat().st_size == img_size
            ),
            md5=_string(image.get("MD5_CHECKSUM")),
            sampling_factor=_integer(module.get("SAMPLING_FACTOR")),
            compression_type=_string(module.get("COMPRESSION_TYPE")),
            focal_plane_temperature_k=_number(module.get("FOCAL_PLANE_TEMPERATURE")),
            sub_sc_lat=_number(module.get("SUB_SPACECRAFT_LATITUDE")),
            sub_sc_lon=_number(module.get("SUB_SPACECRAFT_LONGITUDE")),
            sc_altitude_km=_number(module.get("SPACECRAFT_ALTITUDE")),
            solar_distance_km=_number(module.get("SOLAR_DISTANCE")),
            mission_phase_name=_string(module.get("MISSION_PHASE_NAME")),
            processing_level_id=_string(module.get("PROCESSING_LEVEL_ID"), normalize=True),
            data_set_id=_string(module.get("DATA_SET_ID"), normalize=True),
            standard_data_product_id=_string(
                module.get("STANDARD_DATA_PRODUCT_ID"), normalize=True
            ),
            product_creation_time=_datetime(module.get("PRODUCT_CREATION_TIME")),
            source_product_id=_string(module.get("SOURCE_PRODUCT_ID")),
            software_name=_string(module.get("SOFTWARE_NAME")),
            rationale_desc=_string(module.get("RATIONALE_DESC"), normalize=True),
            label_json=label_to_json(module),
            parse_ok=True,
            parse_error="",
        )
        return row
    except Exception as exc:  # a bad label is a row, not a lost product
        row["parse_error"] = " ".join(f"{type(exc).__name__}: {exc}".split())
        LOGGER.debug("label parse failed for %s: %s", label, row["parse_error"])
        return row
