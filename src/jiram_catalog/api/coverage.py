"""Safe archive discovery, processing coverage and external reference records."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Query, Request

from ..config import mirror_root
from ..junocam.policy import (POLICY_VERSION, assess_rows, observation_id,
                              observation_table, preferred_rows)
from ..junocam.quality import load_quality_config
from . import data
from .catalog import json_safe
from .io_guard import serialized_io

router = APIRouter(prefix="/api", tags=["coverage"])
REFERENCES = [
    dict(id="mission-juno-maps", title="Mission Juno JunoCam Maps Archive",
         url="https://www.missionjuno.swri.edu/junocam/think-tank/maps-archive", kind="external_reference",
         description="Global and polar reference composites; intensity and color balance are arbitrary and seam/contrast adjustments are applied. Navigation uncertainty can be a few pixels. Use original single-image products for quantitative analysis.",
         quantitative_ready=False, coordinate_note="Planetocentric latitude and System 3 longitude. Global maps: equirectangular, 10 pixels/degree. Polar maps: azimuthal equidistant with orientation and scale varying by map; check each ReadMe."),
    dict(id="pds-junocam-atm-ml", title="PDS JunoCam atmospheric machine-learning calibrated collection",
         url="https://pds.nasa.gov/ds-view/pds/viewBundle.jsp?identifier=urn:nasa:pds:junocam_atm-ml-calib&version=1.1",
         kind="derived_calibration", quantitative_ready=False,
         description="PJ13–36 learned image translation to five HST-equivalent bands. Flattened 256-pixel tiles span 16,000 km (62.5 km/pixel); this is a derived comparison source, not native JunoCam radiance or validated wind truth.",
         coordinate_note="Mosaics use 0.1-degree planetographic grids. Convert latitude and verify longitude conventions before comparison to the catalog's planetocentric east-positive grids."),
    dict(id="pds-junocam-errata", title="PDS JunoCam archive errata",
         url="https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/ERRATA.TXT",
         kind="quality_provenance", quantitative_ready=False,
         description="Cumulative instrument, radiometric, timing and product-specific issues informing failure-exclusion-v1.",
         coordinate_note="Metadata reference; no image plane."),
]


def _read(path: Path) -> pd.DataFrame:
    return pd.read_parquet(path) if path.exists() else pd.DataFrame(columns=["product_id", "orbit"])


def _rdr(table: pd.DataFrame) -> pd.DataFrame:
    return table.loc[table.level.astype(str).str.upper() == "RDR"] if "level" in table else table


def _identity(table: pd.DataFrame) -> pd.DataFrame:
    table = table.copy()
    table["observation_id"] = table.product_id.astype(str).map(observation_id)
    return table


def _count(table: pd.DataFrame, orbit: int, mask: pd.Series | None = None) -> int:
    if table.empty or "orbit" not in table:
        return 0
    keep = pd.to_numeric(table.orbit, errors="coerce") == orbit
    if mask is not None:
        keep &= mask
    return int(table.loc[keep, "observation_id"].nunique())


def _bool(table: pd.DataFrame, column: str) -> pd.Series:
    return table[column].fillna(False).astype(bool) if column in table else pd.Series(False, index=table.index)


def _status(table: pd.DataFrame, status: str) -> pd.Series:
    return table.quality_status == status if "quality_status" in table else pd.Series(False, index=table.index)


def archive_table(mirror: str | Path | None, instrument: str) -> pd.DataFrame:
    """Preferred archive-known observations with safe, local evidence joined."""
    root = mirror_root(mirror)
    if instrument.lower() == "junocam":
        from ..junocam.pds import manifest_files_path
        known = _rdr(_read(manifest_files_path(root)))
        local = _rdr(observation_table(root))
        columns = [c for c in local.columns if c not in known.columns or c == "product_id"]
        if known.empty:
            table = local
        else:
            table = known.merge(local[columns], on="product_id", how="outer")
        table = preferred_rows(table)
        records = table.to_dict("records")
        assessments = assess_rows(records)
        table["quality_status"] = [a["status"] for a in assessments]
        table["quality_reason"] = ["; ".join(a["reasons"]) for a in assessments]
        return _identity(table)
    manifest = _read(root / "manifest/manifest.parquet")
    if "family" in manifest:
        manifest = manifest.loc[manifest.family.astype(str).str.upper() == "IMG"]
    if "ext" in manifest:
        manifest = manifest.loc[manifest.ext.astype(str).str.upper().str.lstrip(".") == "IMG"]
    indexed = _read(root / "index/frames.parquet")
    if "orbit_dir" in indexed:
        indexed["orbit"] = indexed.orbit_dir
    if manifest.empty:
        table = indexed
    else:
        columns = [c for c in indexed if c not in manifest or c == "product_id"]
        table = manifest.merge(indexed[columns], on="product_id", how="outer")
    table = preferred_rows(table)
    table["quality_status"] = "unassessed"
    table["quality_reason"] = "Instrument-failure exclusion currently assesses JunoCam only"
    return _identity(table)


def coverage_payload(mirror: str | Path | None) -> dict[str, Any]:
    root = mirror_root(mirror)
    rows, sources = [], []
    paths = ["manifest/manifest.parquet", "index/frames.parquet", "index/frames_geo.parquet",
             "junocam/manifest/manifest_files.parquet", "junocam/index/junocam_images.parquet",
             "junocam/index/junocam_quality.parquet", "junocam/index/junocam_geo.parquet", "strips/strips.parquet"]
    for name in paths:
        path = root / name
        if path.exists():
            sources.append(dict(name=name, updated_utc=datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()))
    try:
        strips = data.strips_table(root)
    except FileNotFoundError:
        strips = pd.DataFrame()
    stack_counts: dict[tuple[str, int], int] = {}
    for path in data.stack_paths(root):
        try:
            ds = data.open_stack(path)
            instrument = str(ds.attrs.get("instrument", "JIRAM"))
            orbits = set(int(o) for o in ds.orbit.values) if "orbit" in ds else set()
            for orbit in orbits:
                stack_counts[(instrument, orbit)] = stack_counts.get((instrument, orbit), 0) + 1
        except Exception:
            # No pixel read occurs here. A withheld stack contributes no usable product.
            continue
    for instrument in ("JIRAM", "JunoCam"):
        known = archive_table(root, instrument)
        if instrument == "JunoCam":
            local = _identity(preferred_rows(_rdr(observation_table(root))))
            geometry = local
        else:
            local = _identity(preferred_rows(_read(root / "index/frames.parquet")))
            if "orbit_dir" in local:
                local["orbit"] = local.orbit_dir
            geometry = _identity(_read(root / "index/frames_geo.parquet"))
            if "orbit" not in geometry or geometry.orbit.isna().all():
                geometry = geometry.drop(columns="orbit", errors="ignore").merge(local[["product_id", "orbit"]], on="product_id", how="left")
        orbit_values = pd.concat([known.get("orbit", pd.Series(dtype=float)), local.get("orbit", pd.Series(dtype=float))])
        for orbit in sorted(pd.to_numeric(orbit_values, errors="coerce").dropna().astype(int).unique()):
            is_jc = instrument == "JunoCam"
            eligible = _count(known, orbit, _status(known, "eligible")) if is_jc else 0
            excluded = _count(known, orbit, _status(known, "excluded")) if is_jc else 0
            unassessed = _count(known, orbit, _status(known, "unassessed"))
            nstrips = 0
            if not strips.empty:
                strip_instrument = strips.instrument.astype(str) if "instrument" in strips else pd.Series("JIRAM", index=strips.index)
                nstrips = int(((strips.orbit == orbit) & (strip_instrument == instrument)).sum())
            narchive = _count(known, orbit)
            rows.append(dict(instrument=instrument, orbit=int(orbit), archive_known=narchive,
                             labels_indexed=_count(local, orbit), pixels_local=_count(local, orbit, _bool(local, "img_present")),
                             geometry_available=_count(geometry, orbit, _bool(geometry, "geo_ok")),
                             quality_assessed=eligible + excluded, eligible=eligible, excluded=excluded, unassessed=unassessed,
                             stacks=stack_counts.get((instrument, orbit), 0), strips=nstrips,
                             status="partial" if _count(local, orbit) < narchive else "indexed",
                             pixels_local_basis="img_present index snapshot; not a fresh filesystem verification"))
    policy = load_quality_config().get("access_policy", {})
    return json_safe(dict(policy_version=POLICY_VERSION, generated_utc=datetime.now(timezone.utc).isoformat(),
                          sources=sources, rows=rows, policy=dict(summary=policy.get("summary", ""), exclusions=policy.get("exclusions", [])), references=REFERENCES))


@router.get("/coverage")
@serialized_io
def coverage(request: Request) -> dict[str, Any]:
    return coverage_payload(request.app.state.mirror)


@router.get("/archive")
def archive(request: Request, instrument: str = Query(default="JunoCam", pattern="^(?i:JIRAM|JunoCam)$"),
            orbit: int | None = None, q: str = "", offset: int = Query(default=0, ge=0),
            limit: int = Query(default=100, ge=1, le=200)) -> dict[str, Any]:
    table = archive_table(request.app.state.mirror, instrument)
    if orbit is not None:
        table = table.loc[pd.to_numeric(table.orbit, errors="coerce") == orbit]
    if q:
        columns = [c for c in ("product_id", "rationale_desc", "target", "target_name", "filters") if c in table]
        text = table[columns].fillna("").astype(str).agg(" ".join, axis=1)
        table = table.loc[text.str.contains(q, case=False, regex=False)]
    items = []
    for row in table.iloc[offset:offset + limit].to_dict("records"):
        items.append(dict(product_id=row["product_id"], observation_id=row["observation_id"], orbit=row.get("orbit"),
                          start_time=row.get("start_time"), bands=row.get("filters", row.get("bands", row.get("filter_code", row.get("band")))),
                          target=row.get("target", row.get("target_name")), source_url=row.get("url_img", row.get("url")),
                          label_url=row.get("url_lbl"), status=row.get("quality_status"), reason=row.get("quality_reason")))
    return json_safe(dict(total=len(table), items=items, offset=offset, limit=limit))


@router.get("/references")
def references() -> dict[str, Any]:
    return {"items": REFERENCES}
