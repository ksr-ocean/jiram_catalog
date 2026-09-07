"""Evidence-based, fail-closed JunoCam access policy.

Processing versions are provenance, not independent observations. Assessments
never infer recovery from a broad mission epoch or from a legacy A/B/C grade.
Original files and source indices remain unchanged.
"""
from __future__ import annotations

import math
import re
import threading
from functools import wraps
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ..config import mirror_root
from .quality import config_path, load_quality_config

POLICY_VERSION = "failure-exclusion-v1"
_VERSION = re.compile(r"_V(\d+)$")
_CACHE: dict[tuple, pd.DataFrame] = {}
_POLICY_LOCK = threading.RLock()


def _single_flight(function):
    @wraps(function)
    def locked(*args, **kwargs):
        with _POLICY_LOCK:
            return function(*args, **kwargs)
    return locked


def observation_id(product_id: str) -> str:
    return _VERSION.sub("", str(product_id))


def native_version(product_id: str) -> int:
    match = _VERSION.search(str(product_id))
    return int(match.group(1)) if match else 0


def preferred_rows(table: pd.DataFrame, id_column: str = "product_id") -> pd.DataFrame:
    """Highest numeric version per identity, in stable chronological order."""
    if table.empty:
        return table.copy()
    work = table.copy()
    work["_observation"] = work[id_column].astype(str).map(observation_id)
    work["_version"] = work[id_column].astype(str).map(native_version)
    work["_position"] = np.arange(len(work))
    work = work.sort_values("_version", kind="stable").drop_duplicates("_observation", keep="last")
    if "start_time" in work:
        work["_time"] = pd.to_datetime(work["start_time"], errors="coerce", utc=True)
        work = work.sort_values(["_time", "_position"], kind="stable").drop(columns="_time")
    else:
        work = work.sort_values("_position", kind="stable")
    return work.drop(columns=["_observation", "_version", "_position"]).reset_index(drop=True)


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return math.nan


def _true(value: Any) -> bool:
    return value is True or isinstance(value, np.bool_) and bool(value)


def _text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = value.strip()
    return "" if text.lower() in {"nan", "none", "null", "unknown", "n/a"} else text


def assess_observation(row: Mapping) -> dict:
    return _assess_observation(row, load_quality_config().get("access_policy", {}))


def assess_rows(rows: Iterable[Mapping]) -> list[dict]:
    """One config read per table batch; never perform filesystem I/O per row."""
    config = load_quality_config().get("access_policy", {})
    return [_assess_observation(row, config) for row in rows]


def _assess_observation(row: Mapping, config: Mapping) -> dict:
    orbit = _number(row.get("orbit"))
    identifier = _text(row.get("product_id"))
    reasons: list[str] = []
    navigation = "available" if _true(row.get("geo_ok")) else "unassessed"
    if row.get("geo_ok") is False or isinstance(row.get("geo_ok"), np.bool_) and not row["geo_ok"]:
        navigation = "failed"
    signal = "unassessed"
    calibration = "unassessed"
    sequence_match = re.search(r"_\d{2}[A-Z](\d+)_V\d+$", identifier)
    sequence = int(sequence_match.group(1)) if sequence_match else _number(row.get("sequence"))
    for rule in config.get("exclusions", []):
        matches = True
        if "orbit_min" in rule:
            matches = float(rule["orbit_min"]) <= orbit <= float(rule["orbit_max"])
        if "sequence_min" in rule:
            matches &= sequence >= float(rule["sequence_min"])
        if "sequence_max" in rule:
            matches &= sequence <= float(rule["sequence_max"])
        if "product_pattern" in rule:
            matches &= bool(re.search(rule["product_pattern"], identifier))
        if "product_ids" in rule:
            matches &= identifier in rule["product_ids"] or observation_id(identifier) in rule["product_ids"]
        if matches:
            reasons.append(f"{rule['reason']} [source: {rule['source']}]")
            calibration = "invalid" if rule.get("radiometry_invalid") else "affected"
    for flag in ("instrument_failure", "radiometry_invalid", "content_free", "corrupt", "corruption_flag", "bloom_flag"):
        if _true(row.get(flag)):
            reasons.append(f"Measured or labelled {flag.replace('_', ' ')}")
    rationale = " ".join(_text(row.get(k)) for k in ("rationale_desc", "quality_reason", "metrics_error", "parse_error"))
    patterns = config.get("rationale_patterns", [])
    for pattern in patterns:
        if re.search(pattern, rationale, re.IGNORECASE):
            reasons.append(f"Product label/measurement flags an instrument or signal problem: {rationale[:400]}")
            break
    values = {name: _number(row.get(name)) for name in ("streak_index", "saturation_frac", "zero_frac", "max_dn")}
    if values["max_dn"] <= 0 or values["zero_frac"] >= 0.999:
        reasons.append("No meaningful image signal (all black or at least 99.9% zero)")
    if values["saturation_frac"] >= 0.02:
        reasons.append("Saturation fraction is at least 2%")
    if values["streak_index"] >= 0.3:
        reasons.append("Measured streak index fails the 0.3 threshold")
    if row.get("metrics_ok") is False or isinstance(row.get("metrics_ok"), np.bool_) and not row["metrics_ok"]:
        reasons.append("Pixel metrics failed; image integrity is not established")
    if row.get("parse_ok") is False or isinstance(row.get("parse_ok"), np.bool_) and not row["parse_ok"]:
        reasons.append("Product label parsing failed")
    complete = _true(row.get("metrics_ok")) and all(math.isfinite(value) for value in values.values())
    complete &= 0 <= values["streak_index"] < 0.3 and 0 <= values["saturation_frac"] < 0.02 and 0 <= values["zero_frac"] < 0.999 and values["max_dn"] > 0
    documented = orbit in config.get("legacy_measured_orbits", [])
    evidence = _text(row.get("unaffected_evidence"))
    evidence_source = _text(row.get("unaffected_evidence_source"))
    # An evidence reference and rationale are both required for a new clearance.
    for clearance in config.get("clearances", []):
        ids = clearance.get("product_ids", [])
        stems = clearance.get("observation_ids", [])
        if identifier in ids or observation_id(identifier) in stems:
            reason, source = _text(clearance.get("reason")), _text(clearance.get("source"))
            if reason and source:
                evidence, evidence_source = reason, source
                break
    supported = documented or bool(evidence and evidence_source)
    if reasons:
        status, signal = "excluded", "failed"
    elif complete and supported:
        status, signal = "eligible", "measured_clean"
        calibration = "legacy_rdr_relative" if documented else "documented_unaffected"
        reasons.append("Complete clean pixel metrics in the validated PJ4 sample" if documented else f"Unaffected image evidence: {evidence} [source: {evidence_source}]")
    else:
        status = "unassessed"
        if not complete:
            reasons.append("Complete finite pixel quality metrics are not available")
        if not supported:
            reasons.append("No per-observation evidence establishes unaffected instrument/radiometric behavior; broad epochs do not establish recovery")
    if navigation == "failed":
        status = "excluded"
        reasons.append("Geometry assessment failed")
    return dict(status=status, reasons=reasons, navigation=navigation, signal=signal,
                calibration=calibration, policy_version=POLICY_VERSION)


def policy_signature(mirror: str | Path | None) -> tuple:
    root = mirror_root(mirror)
    paths = [root / "junocam/index" / name for name in ("junocam_images.parquet", "junocam_quality.parquet", "junocam_geo.parquet")]
    paths.extend([root / "junocam/manifest/manifest_files.parquet",
                  root / "junocam/manifest/manifest.parquet", config_path()])
    stats = []
    for path in paths:
        try:
            stat = path.stat()
            stats.append((str(path), stat.st_mtime_ns, stat.st_size))
        except OSError:
            stats.append((str(path), None))
    return tuple(stats)


@_single_flight
def observation_table(mirror: str | Path | None) -> pd.DataFrame:
    """All indexed versions with merged evidence; no original table is edited."""
    signature = policy_signature(mirror)
    if signature in _CACHE:
        return _CACHE[signature].copy()
    root = mirror_root(mirror) / "junocam/index"
    table = pd.DataFrame(columns=["product_id"])
    for name in ("junocam_images.parquet", "junocam_quality.parquet", "junocam_geo.parquet"):
        path = root / name
        if not path.exists():
            continue
        part = pd.read_parquet(path).drop_duplicates("product_id", keep="last")
        if table.empty:
            table = part.copy()
        else:
            table = table.merge(part, on="product_id", how="outer", suffixes=("", "_new"))
            # Coalesce shared columns in one reconstruction. Repeated drops
            # trigger many tiny Arrow-backed column-index operations in pandas.
            columns = {}
            for column in table.columns:
                if not column.endswith("_new"):
                    secondary = column + "_new"
                    columns[column] = (table[column].combine_first(table[secondary])
                                       if secondary in table else table[column])
            table = pd.DataFrame(columns, index=table.index)
    table["observation_id"] = table.product_id.astype(str).map(observation_id)
    table["native_version"] = table.product_id.astype(str).map(native_version)
    # Archive-known newer versions are authoritative even when not yet local.
    identities = table[["product_id"]]
    manifest_root = root.parent / "manifest"
    manifest_path = manifest_root / "manifest_files.parquet"
    if not manifest_path.exists():
        manifest_path = manifest_root / "manifest.parquet"
    if manifest_path.exists():
        known = pd.read_parquet(manifest_path)
        if "level" in known:
            known = known.loc[known.level.astype(str).str.upper() == "RDR"]
        identities = pd.concat([identities, known[["product_id"]]], ignore_index=True)
    preferred = set(preferred_rows(identities).product_id)
    table["is_preferred"] = table.product_id.isin(preferred)
    assessments = assess_rows(table.to_dict("records"))
    table["quality_assessment"] = assessments
    table["quality_status"] = [a["status"] for a in assessments]
    table["quality_reason"] = ["; ".join(a["reasons"]) for a in assessments]
    _CACHE.clear()
    _CACHE[signature] = table
    return table.copy()


def eligible_images(mirror: str | Path | None, *, preferred: bool = True) -> pd.DataFrame:
    table = observation_table(mirror)
    if preferred:
        table = table.loc[table.is_preferred]
    table = table.loc[table.quality_status == "eligible"]
    if "level" in table:
        table = table.loc[table.level.astype(str).str.upper() == "RDR"]
    return preferred_rows(table) if preferred else table.reset_index(drop=True)
