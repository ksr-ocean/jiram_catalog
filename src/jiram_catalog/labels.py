"""Parsing of the PDS3 labels embedded in the PDS4 JIRAM archive."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pvl

IDENTITY_COLUMNS = [
    "product_id",
    "orbit",
    "orbit_dir",
    "version",
    "family",
    "band",
    "geom_band",
    "label_path",
    "img_file_name",
    "img_size_bytes",
    "img_present",
    "md5_checksum",
    "parse_ok",
    "parse_error",
]
TIME_COLUMNS = [
    "start_time",
    "stop_time",
    "exposure_s",
    "sclk_start",
    "sclk_stop",
]
BOOKKEEPING_COLUMNS = [
    "sequence_number",
    "sequence_samples",
    "command_file_name",
    "mission_phase_name",
    "instrument_mode_id",
    "instrument_mode_desc",
    "channel_id",
    "frame_type",
    "data_quality_id",
    "calibration_source_id",
    "source_product_id",
    "spice_kernels",
]
IMAGE_COLUMNS = ["lines", "samples", "sample_type", "sample_bits", "unit"]
SPACECRAFT_COLUMNS = [
    "sub_sc_lat",
    "sub_sc_lon",
    "sub_sc_azimuth",
    "sc_altitude_km",
    "target_center_distance_km",
    "sub_solar_lat",
    "sub_solar_lon",
    "sub_solar_azimuth",
    "sc_sun_distance_km",
    "sc_target_pos_x_km",
    "sc_target_pos_y_km",
    "sc_target_pos_z_km",
    "sc_target_vel_x_kms",
    "sc_target_vel_y_kms",
    "sc_target_vel_z_kms",
    "ra_deg",
    "dec_deg",
    "twist_deg",
    "celestial_north_clock_angle_deg",
    "q_w",
    "q_x",
    "q_y",
    "q_z",
    "target_name",
    "target_pixels",
    "target_presence_flag",
]
GEOMETRY_COLUMNS = [
    "incidence_angle",
    "emission_angle",
    "phase_angle",
    "slant_distance_km",
    "min_lat",
    "center_lat",
    "max_lat",
    "westernmost_lon",
    "center_lon",
    "easternmost_lon",
    "h_pixel_scale_m",
    "v_pixel_scale_m",
    "north_azimuth",
    "line_first_pixel",
    "sample_first_pixel",
]
SEQUENCE_COLUMNS = ["seq_id", "seq_index", "seq_n", "seq_gap_s"]
FRAME_COLUMNS = (
    IDENTITY_COLUMNS
    + TIME_COLUMNS
    + BOOKKEEPING_COLUMNS
    + IMAGE_COLUMNS
    + SPACECRAFT_COLUMNS
    + GEOMETRY_COLUMNS
    + SEQUENCE_COLUMNS
)

INTEGER_DTYPES = {
    "orbit": "Int16",
    "orbit_dir": "Int16",
    "img_size_bytes": "Int64",
    "sequence_number": "Int16",
    "sequence_samples": "Int16",
    "lines": "Int32",
    "samples": "Int32",
    "sample_bits": "Int16",
    "target_pixels": "Int64",
    "seq_index": "Int32",
    "seq_n": "Int32",
}
BOOLEAN_COLUMNS = {"img_present", "parse_ok"}
DATETIME_COLUMNS = {"start_time", "stop_time"}
FLOAT_COLUMNS = {
    "exposure_s",
    *SPACECRAFT_COLUMNS[:-3],
    *GEOMETRY_COLUMNS,
    "seq_gap_s",
}
STRING_COLUMNS = set(FRAME_COLUMNS) - (
    set(INTEGER_DTYPES) | BOOLEAN_COLUMNS | DATETIME_COLUMNS | FLOAT_COLUMNS
)

_SCALAR_MAP = {
    "sub_sc_lat": "SUB_SPACECRAFT_LATITUDE",
    "sub_sc_lon": "SUB_SPACECRAFT_LONGITUDE",
    "sub_sc_azimuth": "SUB_SPACECRAFT_AZIMUTH",
    "sc_altitude_km": "SPACECRAFT_ALTITUDE",
    "target_center_distance_km": "TARGET_CENTER_DISTANCE",
    "sub_solar_lat": "SUB_SOLAR_LATITUDE",
    "sub_solar_lon": "SUB_SOLAR_LONGITUDE",
    "sub_solar_azimuth": "SUB_SOLAR_AZIMUTH",
    "sc_sun_distance_km": "SPACECRAFT_SOLAR_DISTANCE",
    "ra_deg": "RIGHT_ASCENSION",
    "dec_deg": "DECLINATION",
    "twist_deg": "TWIST_ANGLE",
    "celestial_north_clock_angle_deg": "CELESTIAL_NORTH_CLOCK_ANGLE",
}
_GEOMETRY_MAP = {
    "incidence_angle": "INCIDENCE_ANGLE",
    "emission_angle": "EMISSION_ANGLE",
    "phase_angle": "PHASE_ANGLE",
    "slant_distance_km": "SLANT_DISTANCE",
    "min_lat": "MINIMUM_LATITUDE",
    "center_lat": "CENTER_LATITUDE",
    "max_lat": "MAXIMUM_LATITUDE",
    "westernmost_lon": "WESTERNMOST_LONGITUDE",
    "center_lon": "CENTER_LONGITUDE",
    "easternmost_lon": "EASTERNMOST_LONGITUDE",
    "h_pixel_scale_m": "HORIZONTAL_PIXEL_SCALE",
    "v_pixel_scale_m": "VERTICAL_PIXEL_SCALE",
    "north_azimuth": "NORTH_AZIMUTH",
    "line_first_pixel": "LINE_FIRST_PIXEL",
    "sample_first_pixel": "SAMPLE_FIRST_PIXEL",
}


def _empty_row(product_id: str) -> dict[str, Any]:
    row = {column: None for column in FRAME_COLUMNS}
    for column in FLOAT_COLUMNS:
        row[column] = np.nan
    row.update(
        product_id=product_id,
        parse_ok=False,
        parse_error="",
        seq_id="",
        seq_index=-1,
        seq_n=0,
        seq_gap_s=np.nan,
    )
    return row


def _load_pvl(path: Path) -> Any:
    try:
        module = pvl.load(path)
    except Exception:
        module = pvl.load(path, grammar=pvl.grammar.OmniGrammar())
    nonblank = [line.strip() for line in path.read_text(errors="replace").splitlines() if line.strip()]
    if not nonblank or nonblank[-1] != "END":
        raise ValueError("label is truncated: last non-blank line is not END")
    return module


def _raw(value: Any) -> Any:
    return getattr(value, "value", value)


def _number(value: Any) -> float:
    value = _raw(value)
    if value is None or (isinstance(value, str) and value.strip().upper() == "N/A"):
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
    result = str(value).strip()
    if not result or result.upper() == "N/A":
        return None
    return " ".join(result.split()) if normalize else result


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        result = datetime.fromisoformat(text)
    if result.tzinfo is not None:
        result = result.astimezone(timezone.utc).replace(tzinfo=None)
    return result


def _vector(module: Any, key: str, length: int) -> list[float]:
    value = module.get(key)
    if not isinstance(value, (list, tuple)) or len(value) < length:
        return [math.nan] * length
    return [_number(item) for item in value[:length]]


def _directory_orbit(path: Path) -> int | None:
    match = re.fullmatch(r"orbit(\d{2})", path.parent.name)
    return int(match.group(1)) if match else None


def parse_label(
    path: str | Path,
    mirror: str | Path | None = None,
    orbit_dir: int | None = None,
) -> dict[str, Any]:
    """Parse one IMG label into a flat frame row; failures remain data rows."""
    label = Path(path)
    fallback_product_id = label.stem
    row = _empty_row(fallback_product_id)
    try:
        module = _load_pvl(label)
        file_object = module.get("FILE") or {}
        image_object = file_object.get("IMAGE") or {}
        history_object = file_object.get("HISTORY") or {}

        product_id = _string(module.get("PRODUCT_ID")) or fallback_product_id
        version_match = re.search(r"_(V\d+)$", product_id)
        version_value = _string(module.get("PRODUCT_VERSION_ID"))
        version = version_match.group(1) if version_match else (
            f"V{version_value}" if version_value else None
        )
        start_time = _datetime(module.get("START_TIME"))
        stop_time = _datetime(module.get("STOP_TIME"))
        exposure_s = (
            (stop_time - start_time).total_seconds()
            if start_time is not None and stop_time is not None
            else math.nan
        )
        lines = _integer(image_object.get("LINES"))
        samples = _integer(image_object.get("LINE_SAMPLES"))
        mode_description = _string(
            module.get("INSTRUMENT_MODE_DESC"), normalize=True
        )
        description = mode_description or ""
        has_m = "M-Band" in description
        has_l = "L-Band" in description
        if (has_m and has_l) or lines == 256:
            band = "LM"
        elif has_m:
            band = "M"
        elif has_l:
            band = "L"
        else:
            band = "none"

        m_group = module.get("M_BAND_PARAMETERS") or {}
        l_group = module.get("L_BAND_PARAMETERS") or {}
        if not math.isnan(_number(m_group.get("CENTER_LATITUDE"))):
            geom_band, geometry = "M", m_group
        elif not math.isnan(_number(l_group.get("CENTER_LATITUDE"))):
            geom_band, geometry = "L", l_group
        else:
            geom_band, geometry = "none", {}

        records = _integer(file_object.get("FILE_RECORDS"))
        record_bytes = _integer(file_object.get("RECORD_BYTES"))
        image_size = (
            records * record_bytes
            if records is not None and record_bytes is not None
            else None
        )
        image_name = _string(file_object.get("^IMAGE"))
        resolved_orbit_dir = orbit_dir if orbit_dir is not None else _directory_orbit(label)
        if mirror is not None:
            mirror_path = Path(mirror)
            try:
                label_path = str(label.resolve().relative_to(mirror_path.resolve()))
            except ValueError:
                label_path = str(label)
        else:
            label_path = str(label)
        image_path = label.parent / image_name if image_name else None
        image_present = bool(
            image_path is not None
            and image_size is not None
            and image_path.is_file()
            and image_path.stat().st_size == image_size
        )

        row.update(
            product_id=product_id,
            orbit=_integer(module.get("ORBIT_NUMBER")),
            orbit_dir=resolved_orbit_dir,
            version=version,
            family="IMG",
            band=band,
            geom_band=geom_band,
            label_path=label_path,
            img_file_name=image_name,
            img_size_bytes=image_size,
            img_present=image_present,
            md5_checksum=_string(module.get("MD5_CHECKSUM")),
            parse_ok=True,
            parse_error="",
            start_time=start_time,
            stop_time=stop_time,
            exposure_s=exposure_s,
            sclk_start=_string(module.get("SPACECRAFT_CLOCK_START_COUNT")),
            sclk_stop=_string(module.get("SPACECRAFT_CLOCK_STOP_COUNT")),
            sequence_number=_integer(module.get("SEQUENCE_NUMBER")),
            sequence_samples=_integer(module.get("SEQUENCE_SAMPLES")),
            command_file_name=_string(module.get("COMMAND_FILE_NAME")),
            mission_phase_name=_string(module.get("MISSION_PHASE_NAME")),
            instrument_mode_id=_string(module.get("INSTRUMENT_MODE_ID")),
            instrument_mode_desc=mode_description,
            channel_id=_string(module.get("CHANNEL_ID")),
            frame_type=_string(file_object.get("FRAME_TYPE")),
            data_quality_id=_string(file_object.get("DATA_QUALITY_ID")),
            calibration_source_id=_string(
                history_object.get("CALIBRATION_SOURCE_ID")
            ),
            source_product_id=_string(history_object.get("SOURCE_PRODUCT_ID")),
            lines=lines,
            samples=samples,
            sample_type=_string(image_object.get("SAMPLE_TYPE")),
            sample_bits=_integer(image_object.get("SAMPLE_BITS")),
            unit=_string(file_object.get("UNIT")),
            target_name=_string(module.get("TARGET_NAME")),
            target_pixels=_integer(module.get("TARGET_PIXELS")),
            target_presence_flag=_string(module.get("JNO:TARGET_PRESENCE_FLAG")),
        )
        for column, key in _SCALAR_MAP.items():
            row[column] = _number(module.get(key))
        position = _vector(module, "SC_TARGET_POSITION_VECTOR", 3)
        velocity = _vector(module, "SC_TARGET_VELOCITY_VECTOR", 3)
        quaternion = _vector(module, "QUATERNION", 4)
        for column, value in zip(
            ["sc_target_pos_x_km", "sc_target_pos_y_km", "sc_target_pos_z_km"],
            position,
            strict=True,
        ):
            row[column] = value
        for column, value in zip(
            [
                "sc_target_vel_x_kms",
                "sc_target_vel_y_kms",
                "sc_target_vel_z_kms",
            ],
            velocity,
            strict=True,
        ):
            row[column] = value
        for column, value in zip(
            ["q_w", "q_x", "q_y", "q_z"], quaternion, strict=True
        ):
            row[column] = value
        for column, key in _GEOMETRY_MAP.items():
            row[column] = _number(geometry.get(key))

        kernels = file_object.get("SPICE_FILE_NAME")
        if isinstance(kernels, str):
            kernel_values = [kernels]
        elif kernels is None:
            kernel_values = []
        else:
            kernel_values = list(kernels)
        row["spice_kernels"] = ";".join(
            sorted(value for item in kernel_values if (value := _string(item)))
        )
        return row
    except Exception as exc:
        resolved_orbit_dir = orbit_dir if orbit_dir is not None else _directory_orbit(label)
        if mirror is not None:
            try:
                failed_label_path = str(
                    label.resolve().relative_to(Path(mirror).resolve())
                )
            except ValueError:
                failed_label_path = str(label)
        else:
            failed_label_path = str(label)
        row.update(
            orbit_dir=resolved_orbit_dir,
            family="IMG",
            label_path=failed_label_path,
        )
        row["parse_error"] = " ".join(f"{type(exc).__name__}: {exc}".split())
        return row
