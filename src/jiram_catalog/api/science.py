"""Bounded analysis endpoints; every image enters through policy-aware loaders."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import numpy as np
import pandas as pd
import xarray as xr
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import science as core
from ..stats2d import _interpolate, strip_statistics
from . import data
from .io_guard import NetCDFRoute

router = APIRouter(prefix="/api/science", tags=["science"], route_class=NetCDFRoute)
MAX_COMPARE_PX = 512


class ImageRef(BaseModel):
    kind: Literal["stack", "strip"]
    id: str = Field(min_length=1, max_length=256)
    t: int = Field(default=0, ge=0)
    band: str | None = None
    norm: str = "none"


class CompareRequest(BaseModel):
    left: ImageRef
    right: ImageRef
    speed_m_s: float = Field(default=30, ge=0, le=10000, allow_inf_nan=False)
    navigation_error_px: float | None = Field(default=None, ge=0, allow_inf_nan=False)


class PopulationRequest(BaseModel):
    strip_ids: list[str] = Field(min_length=1, max_length=100)
    band: str | None = None
    norm: str = "none"
    k_min: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    k_max: float | None = Field(default=None, gt=0, allow_inf_nan=False)


class MatchesRequest(BaseModel):
    product_id: str = Field(min_length=1, max_length=256)
    max_dt_s: float = Field(default=3600, ge=0, le=86400*30, allow_inf_nan=False)
    min_overlap: float = Field(default=.1, ge=0, le=1, allow_inf_nan=False)
    limit: int = Field(default=20, ge=1, le=100)


def _stack(mirror: Path, identifier: str) -> xr.Dataset:
    from .stacks import resolve
    try:
        return data.open_stack(resolve(mirror, identifier))
    except (ValueError, FileNotFoundError, KeyError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc


def _strip(mirror: Path, identifier: str) -> xr.Dataset:
    # The old general-purpose reader also accepts local paths. Science routes
    # intentionally accept only an index identifier, never a client file path.
    if Path(identifier).name != identifier or identifier in (".", "..") or identifier.endswith(".nc"):
        raise ValueError("strip must be a catalog identifier, not a path")
    return data.open_strip(mirror, identifier)


@router.get("/stacks/{stack_id:path}/readiness")
def readiness(request: Request, stack_id: str, band: str | None = None,
              norm: str = "none", dt_tol: float = Query(.05, ge=0, lt=1),
              min_frames: int = Query(3, ge=3, le=1000)) -> dict[str, Any]:
    return core.stack_readiness(_stack(request.app.state.mirror, stack_id),
        band=band, norm=norm, dt_tol=dt_tol, min_frames=min_frames)


def _open_ref(mirror: Path, ref: ImageRef) -> tuple[xr.Dataset, dict[str, Any]]:
    whole = _stack(mirror, ref.id) if ref.kind == "stack" else _strip(mirror, ref.id)
    selected = core.select_physical_band(whole, ref.band)
    if ref.kind == "stack":
        if ref.t >= selected.sizes.get("time", 0):
            raise ValueError("time index outside the stack")
        selected = selected.isel(time=ref.t)
        stamp = pd.Timestamp(selected.time.values)
    else:
        if ref.t != 0:
            raise ValueError("a strip has one image; its time index is zero")
        stamp = pd.to_datetime(selected.attrs.get("time_mid", selected.attrs.get("time_start")))
    km = core.validate_grid(selected)
    return selected, core.json_safe({**ref.model_dump(), "time": stamp,
        "instrument": selected.attrs.get("instrument", "JIRAM"),
        "band": selected.attrs.get("band"), "units": selected.image.attrs.get("units"),
        "km_per_px": km, "shape": list(selected.image.shape),
        "sources": core.source_ids(selected), "projection": selected.attrs.get("projection")})


def equivalent_grids(left: xr.Dataset, right: xr.Dataset) -> tuple[bool, str]:
    if left.image.shape != right.image.shape:
        return False, "different image shapes; no common grid has been constructed"
    if not np.isclose(float(left.attrs["km_per_px"]), float(right.attrs["km_per_px"])):
        return False, "different map resolutions; resampling is required"
    for name in ("x_km", "y_km"):
        if name not in left or name not in right:
            return False, "map coordinates are missing; equal array shapes do not establish registration"
        if not np.allclose(left[name].values, right[name].values, atol=1e-5, rtol=1e-7):
            return False, "different map coordinate extents; resampling is required"
    projection = left.attrs.get("projection")
    if projection and projection == right.attrs.get("projection"):
        return True, "identical projection metadata and physical grid coordinates"
    if all(name in d for name in ("lat", "lon_east") for d in (left, right)):
        # All geographic cells, evaluated in bounded row blocks. No assertion
        # based on coincident corners or a few sampled points.
        for start in range(0, left.sizes["y"], 64):
            a, b = left.isel(y=slice(start, start+64)), right.isel(y=slice(start, start+64))
            la, lb = np.asarray(a.lat.values), np.asarray(b.lat.values)
            oa, ob = np.asarray(a.lon_east.values), np.asarray(b.lon_east.values)
            finite = np.isfinite(la) & np.isfinite(lb) & np.isfinite(oa) & np.isfinite(ob)
            if not finite.any() or not np.array_equal(np.isfinite(la), np.isfinite(lb)):
                return False, "geographic grid support differs"
            if not np.allclose(la[finite], lb[finite], atol=1e-5, rtol=0):
                return False, "latitude grids differ; geographic resampling is required"
            if np.max(np.abs((oa[finite]-ob[finite]+180)%360-180)) > 1e-5:
                return False, "longitude grids differ; geographic resampling is required"
        return True, "equivalent per-pixel latitude/longitude and physical grid coordinates"
    return False, "projection equivalence cannot be established from available metadata"


def register_images(left: np.ndarray, right: np.ndarray,
                    left_valid: np.ndarray, right_valid: np.ndarray) -> dict[str, Any]:
    """Masked NCC on a bounded grid; positive shift is left-to-right motion.

    FFTs form cross-products, but the NCC mean/variance are recomputed for
    each shifted mask intersection. Edges never wrap around the image.
    """
    a, b = np.asarray(left, float), np.asarray(right, float)
    ma, mb = np.asarray(left_valid, bool), np.asarray(right_valid, bool)
    result = dict(dy_px=None, dx_px=None, correlation=None, status="unassessed",
                  method="masked normalized cross-correlation", shift_convention="left image to right image")
    if min(a.shape) < 4 or min(ma.sum(), mb.sum()) < 16:
        return dict(result, reason="insufficient valid pixels for registration")
    a = np.where(ma, a-np.mean(a[ma]), 0.)
    b = np.where(mb, b-np.mean(b[mb]), 0.)
    shape = tuple(2*n-1 for n in a.shape)
    def corr(x: np.ndarray, y: np.ndarray) -> np.ndarray:
        values = np.fft.irfftn(np.fft.rfftn(x, s=shape, axes=(0,1))*np.conj(np.fft.rfftn(y, s=shape, axes=(0,1))), s=shape, axes=(0,1)).real
        return np.roll(values, (a.shape[0]-1, a.shape[1]-1), axis=(0, 1))
    count = np.maximum(np.round(corr(mb, ma)), 0.)
    sa, sb = corr(mb, a), corr(b, ma)
    aa, bb = corr(mb, a*a), corr(b*b, ma)
    ab = corr(b, a)
    n = np.maximum(count, 1.)
    denominator = np.sqrt(np.maximum(aa-sa*sa/n, 0)*np.maximum(bb-sb*sb/n, 0))
    with np.errstate(divide="ignore", invalid="ignore"):
        score = (ab-sa*sb/n)/denominator
    yy, xx = np.indices(shape)
    dy, dx = yy-(a.shape[0]-1), xx-(a.shape[1]-1)
    radius = min(64, max(1, min(a.shape)//4))
    keep = (count >= max(16, .2*min(ma.sum(), mb.sum()))) & (np.abs(dy)<=radius) & (np.abs(dx)<=radius)
    keep &= np.isfinite(score) & (denominator > max(float(np.max(denominator))*1e-12, 1e-30))
    if not keep.any():
        return dict(result, reason="scene has insufficient variance or shifted common support")
    scores = np.where(keep, score, -np.inf)
    iy, ix = np.unravel_index(np.argmax(scores), scores.shape)
    if abs(int(dy[iy,ix])) == radius or abs(int(dx[iy,ix])) == radius:
        return dict(result, status="boundary", reason="peak reaches bounded search radius", search_radius_px=radius)
    return dict(result, status="measured", dy_px=float(dy[iy,ix]), dx_px=float(dx[iy,ix]),
                correlation=float(np.clip(scores[iy,ix], -1, 1)), search_radius_px=radius)


@router.post("/compare")
def compare(request: Request, body: CompareRequest) -> dict[str, Any]:
    try:
        left, lm = _open_ref(request.app.state.mirror, body.left)
        right, rm = _open_ref(request.app.state.mirror, body.right)
        same, reason = equivalent_grids(left, right)
        dt = None if not lm["time"] or not rm["time"] else float((pd.Timestamp(rm["time"])-pd.Timestamp(lm["time"])).total_seconds())
        result: dict[str, Any] = dict(compatible=same, reasons=[] if same else [reason], dt_s=dt,
            common_valid_frac=None, registration=dict(dy_px=None, dx_px=None, correlation=None, status="incompatible"),
            predicted_displacement_px=None, navigation_error_px=body.navigation_error_px,
            velocity_uncertainty_m_s=None, left=lm, right=rm,
            provenance=dict(policy_version=core.POLICY_VERSION, software_revision=core.software_revision(),
                grid_comparison=reason, interpolation="none", left=core.provenance(left, norm=body.left.norm),
                right=core.provenance(right, norm=body.right.norm),
                cautions=["Image registration measures pattern displacement, not validated wind.",
                          "Cross-band and cross-instrument morphology can bias registration."]))
        if not same:
            return core.json_safe(result)
        stride = max(1, int(np.ceil(max(left.image.shape)/MAX_COMPARE_PX)))
        # Normalize at native resolution so a flat:sigma parameter is not
        # accidentally reinterpreted in downsampled pixels.
        a, ma, _ = core.normalise_frame(left, body.left.norm)
        b, mb, _ = core.normalise_frame(right, body.right.norm)
        cut = (slice(None,None,stride), slice(None,None,stride))
        common = ma & mb
        result["common_valid_frac"] = float(common.mean())
        reg = register_images(a[cut], b[cut], ma[cut], mb[cut])
        for name in ("dy_px", "dx_px"):
            if reg.get(name) is not None:
                reg[name] *= stride
        reg.update(sample_stride=stride, sample_km_per_px=lm["km_per_px"]*stride,
                   displacement_units="native map pixels", search_radius_native_px=reg.get("search_radius_px",0)*stride)
        result["registration"] = reg
        if dt is not None:
            result["predicted_displacement_px"] = body.speed_m_s*abs(dt)/(lm["km_per_px"]*1000)
            if dt != 0 and body.navigation_error_px is not None:
                result["velocity_uncertainty_m_s"] = np.sqrt(2)*body.navigation_error_px*lm["km_per_px"]*1000/abs(dt)
                result["provenance"]["navigation_error_assumption"] = "user-supplied independent 1-sigma error per image; sqrt(2)*error*pixel_size/abs(dt)"
        return core.json_safe(result)
    except (ValueError, KeyError, FileNotFoundError, OSError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _source_stamp(ds: xr.Dataset) -> dict[str, Any]:
    path = ds.encoding.get("source")
    result: dict[str, Any] = dict(source=path, sources=core.source_ids(ds),
                               quality_policy=ds.attrs.get("quality_policy"))
    if path:
        stat = Path(path).stat()
        result.update(size=stat.st_size, mtime_ns=stat.st_mtime_ns)
    return result


def _mean_error(values: list[np.ndarray]) -> tuple[np.ndarray, np.ndarray | None]:
    matrix = np.stack(values)
    valid = np.isfinite(matrix)
    count = valid.sum(axis=0)
    total = np.where(valid, matrix, 0.).sum(axis=0)
    mean = np.divide(total, count, out=np.full(total.shape,np.nan), where=count>0)
    if len(values)<2:
        return mean, None
    squares = np.where(valid, (matrix-mean)**2, 0.).sum(axis=0)
    variance = np.divide(squares, count-1, out=np.full(total.shape,np.nan), where=count>1)
    return mean, np.sqrt(variance/np.maximum(count,1))


@router.post("/population")
def population(request: Request, body: PopulationRequest) -> dict[str, Any]:
    if body.k_min is not None and body.k_max is not None and body.k_min >= body.k_max:
        raise HTTPException(400, "k_min must be less than k_max")
    root = request.app.state.mirror
    excluded: list[dict[str,str]] = []
    candidates = []
    stamps = []
    for identifier in sorted(set(body.strip_ids)):
        try:
            ds = _strip(root, identifier)
            selected = core.select_physical_band(ds, body.band)
            km = core.validate_grid(selected)
            ids = core.source_ids(selected)
            if not ids:
                raise ValueError("source observation identifiers are missing; independence cannot be assessed")
            if selected.attrs.get("orbit") is None:
                raise ValueError("perijove/pass metadata are missing; independent-pass statistics are unavailable")
            candidates.append((identifier, selected, ids, km))
            stamps.append(_source_stamp(ds))
        except (ValueError, KeyError, FileNotFoundError, OSError) as exc:
            excluded.append(dict(id=identifier, reason=str(exc)))
        except HTTPException as exc:
            if exc.status_code not in (403,404):
                raise
            excluded.append(dict(id=identifier, reason=str(exc.detail)))
    recipe = dict(**body.model_dump(), policy_version=core.POLICY_VERSION,
        software_revision=core.software_revision(), sources=stamps,
        current_exclusions=excluded.copy(),
        independence="per-pass mean of strips, then equal-weight mean across passes; shared observations counted once",
        spectral_kernel="stats2d; forward FFT; Hann taper; angular wavenumber rad/m",
        fit="positive finite bins; unweighted log10 regression, inside Nyquist by default",
        uncertainty="standard error across independent passes, unknown with fewer than two passes")
    key = hashlib.sha256(json.dumps(core.json_safe(recipe),sort_keys=True).encode()).hexdigest()
    cache = Path(root)/"gui_cache"/"research"/f"population_{key}.json"
    # No stale in-memory/synthetic fixture cache: source mtimes must be known.
    cacheable = bool(stamps) and all(s.get("mtime_ns") is not None for s in stamps)
    if cacheable and cache.is_file():
        try:
            return json.loads(cache.read_text())
        except (OSError, ValueError):
            pass
    groups: dict[tuple[str,str,str,float,str], list[dict[str,Any]]] = {}
    used: dict[tuple[str,str,str,float,str], set[str]] = {}
    # Prefer the highest available version before considering shared-source
    # overlap. A strip partially sharing sources is conservatively excluded.
    candidates.sort(key=lambda c: max(core.product_version(v) for v in c[2]), reverse=True)
    for identifier, ds, ids, km in candidates:
        try:
            image, valid, norm = core.normalise_frame(ds, body.norm)
            if not valid.any():
                raise ValueError("no valid pixels remain after the physical-band and illumination mask")
            instrument = str(ds.attrs.get("instrument","JIRAM"))
            resolution = float(ds.attrs.get("resolution_class",km))
            if not np.isfinite(resolution) or resolution <= 0:
                raise ValueError("native resolution class must be positive and finite")
            group = (instrument, str(ds.attrs["band"]), norm, resolution, str(ds.image.attrs["units"]))
            observations = {core.observation_id(v) for v in ids}
            previous = used.setdefault(group, set())
            if observations & previous:
                raise ValueError("duplicate version or shared source observation already represented by a preferred strip")
            transformed = ds.copy(deep=False)
            transformed["image"] = (("y","x"), image)
            transformed["valid"] = (("y","x"), valid)
            stats = strip_statistics(transformed, norm="none")
            previous.update(observations)
            groups.setdefault(group, []).append(dict(id=identifier, ds=ds, stats=stats,
                pass_id=str(ds.attrs["orbit"]), observations=observations,
                diagnostics=core.mask_diagnostics(image,valid,km*1000),
                provenance=core.provenance(ds,norm=norm)))
        except (ValueError, KeyError, OSError) as exc:
            excluded.append(dict(id=identifier,reason=str(exc)))
    output=[]
    for (instrument,band,norm,resolution,units), entries in groups.items():
        reference=entries[0]["stats"]
        k=np.asarray(reference.k.values)
        r=np.asarray(reference.r.values)
        passes=sorted({e["pass_id"] for e in entries})
        per_pass_e=[]
        per_pass_s=[]
        for pass_id in passes:
            members=[e["stats"] for e in entries if e["pass_id"]==pass_id]
            per_pass_e.append(_mean_error([_interpolate(k,s.k.values,s.E.values) for s in members])[0])
            per_pass_s.append(_mean_error([_interpolate(r,s.r.values,s.S2.values) for s in members])[0])
        E,stderr=_mean_error(per_pass_e)
        S2,_=_mean_error(per_pass_s)
        nyquist=min(float(e["stats"].attrs["k_nyq"]) for e in entries)
        fit_max=nyquist if body.k_max is None else min(body.k_max,nyquist)
        output.append(dict(instrument=instrument,band=band,norm=norm,resolution_class=resolution,units=units,
            n_observations=len(set().union(*(e["observations"] for e in entries))),n_passes=len(passes),
            k=k,E=E,E_stderr=stderr,S2=S2,r_m=r,
            fit=core.fit_spectrum(k,E,body.k_min,fit_max),
            diagnostics=[dict(id=e["id"],**e["diagnostics"]) for e in entries],
            provenance=dict(strips=[dict(id=e["id"],**e["provenance"]) for e in entries],
                passes=passes,weights="equal per pass; equal strip weights within pass",sample_stride=1,
                k_units="rad m-1",E_units=f"({entries[0]['ds'].image.attrs.get('units')})^2 m rad-1",
                cautions=["Spatially overlapping strips in one pass are not treated as independent replicates.",
                          "Interpolation and masks affect spectral shape; slope uncertainty is not wind uncertainty."])))
    response=core.json_safe(dict(groups=output,excluded=excluded,recipe=recipe))
    if cacheable:
        cache.parent.mkdir(parents=True,exist_ok=True)
        temporary=cache.with_suffix(f".{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(response,allow_nan=False))
            os.replace(temporary,cache)
        finally:
            temporary.unlink(missing_ok=True)
    return response


def _longitude_arc(row: dict[str,Any]) -> tuple[float,float] | None:
    try:
        span=float(row.get("lon_span_deg",np.nan))
        if np.isfinite(span) and span>=359.9:
            return 0.,360.
        for lo_name,hi_name in (("lon_min_east","lon_max_east"),("min_lon_east","max_lon_east")):
            lo,hi=float(row.get(lo_name,np.nan)),float(row.get(hi_name,np.nan))
            if np.isfinite(lo) and np.isfinite(hi):
                return lo%360,(hi-lo)%360
        values=row.get("fp_lon")
        # Empty optional outlines must not hide separately available corners.
        if (values is None or not hasattr(values,"__len__") or isinstance(values,str)
                or len(values)==0):
            values=[row.get(f"c{i}_lon",np.nan) for i in range(1,5)]
        angles=np.asarray(values,float)
        angles=np.sort(angles[np.isfinite(angles)]%360)
        if len(angles)<2:
            return None
        gaps=np.diff(np.r_[angles,angles[0]+360])
        index=int(np.argmax(gaps))
        return float(angles[(index+1)%len(angles)]),float(360-gaps[index])
    except (ValueError,TypeError):
        return None


def _bbox(row: dict[str,Any]) -> tuple[float,float,float,float] | None:
    try:
        lo=float(row.get("min_lat",np.nan));hi=float(row.get("max_lat",np.nan))
    except (ValueError,TypeError):
        return None
    arc=_longitude_arc(row)
    if not np.isfinite(lo) or not np.isfinite(hi) or hi<=lo or arc is None or arc[1]<=0:
        return None
    return lo,hi,*arc


def bbox_overlap(left: tuple[float,float,float,float],right: tuple[float,float,float,float]) -> float:
    """Approximate spherical box intersection / smaller box area, seam-safe."""
    lo=max(left[0],right[0]);hi=min(left[1],right[1])
    if hi<=lo:
        return 0.
    def segments(start: float,span: float) -> list[tuple[float,float]]:
        end=start+span
        return [(start,min(end,360))]+([(0.,end-360)] if end>360 else [])
    longitude=sum(max(0,min(b,d)-max(a,c)) for a,b in segments(left[2],left[3]) for c,d in segments(right[2],right[3]))
    area=longitude*(np.sin(np.radians(hi))-np.sin(np.radians(lo)))
    smaller=min(v[3]*(np.sin(np.radians(v[1]))-np.sin(np.radians(v[0]))) for v in (left,right))
    return float(np.clip(area/smaller,0,1)) if smaller>0 else 0.


@router.post("/matches")
def matches(request: Request,body: MatchesRequest) -> dict[str,Any]:
    from .catalog import catalog_frame
    table=catalog_frame(request.app.state.mirror)
    selected=table.loc[table.product_id.astype(str)==body.product_id]
    if selected.empty:
        raise HTTPException(404,"reference product is not in the eligible mapped catalog")
    reference=selected.iloc[0].to_dict()
    boxes=[value for row in selected.to_dict("records") if (value:=_bbox(row)) is not None]
    limitations=["Bounding-box approximation, not exact pixel overlap; overlap is intersection / smaller spherical box area.",
                 "Candidates include only locally mapped, policy-eligible catalog data.",
                 "Coincident footprints do not establish common cloud altitude or usable cross-instrument winds."]
    result=dict(items=[],reference={k:reference.get(k) for k in ("product_id","instrument","start_time","band")},limitations=limitations)
    if not boxes:
        limitations.append("Reference footprint bounds are insufficient to estimate overlap.")
        return core.json_safe(result)
    stamp=pd.Timestamp(reference["start_time"])
    candidates=table.loc[table.instrument!=reference.get("instrument","JIRAM")].copy()
    candidates["delta"]=(pd.to_datetime(candidates.start_time)-stamp).dt.total_seconds()
    candidates=candidates.loc[candidates.delta.abs()<=body.max_dt_s].sort_values("delta",key=abs)
    found: dict[str,dict[str,Any]]={}
    for row in candidates.to_dict("records"):
        identity=core.observation_id(row["product_id"])
        other=_bbox(row)
        overlap=0. if other is None else max(bbox_overlap(box,other) for box in boxes)
        if overlap<body.min_overlap or overlap<=0:
            continue
        half=str(row.get("half",""))
        bands=([half] if row.get("instrument")=="JIRAM" and half in ("L","M")
               else str(row.get("band","")).split(";"))
        if identity in found:
            entry=found[identity]
            entry["bands"]=sorted(set(entry["bands"]+bands))
            entry["overlap_fraction"]=max(entry["overlap_fraction"],overlap)
        else:
            found[identity]=dict(product_id=row["product_id"],instrument=row["instrument"],dt_s=row["delta"],
                overlap_fraction=overlap,overlap_method="maximum band-pair spherical box intersection / smaller box; approximate",
                native_pixel_km=row.get("median_pixel_km"),bands=bands)
    result["items"]=list(found.values())[:body.limit]
    return core.json_safe(result)


@router.get("/stacks/{stack_id:path}/vectors")
def vectors(request: Request,stack_id: str,t: int=Query(0,ge=0)) -> dict[str,Any]:
    from .stacks import resolve
    root=request.app.state.mirror
    stack=_stack(root,stack_id)
    if t>=stack.sizes.get("time",0):
        raise HTTPException(404,"time index outside stack")
    path=resolve(root,stack_id)
    stamp=pd.Timestamp(stack.time.values[t])
    rejected=[]
    # Only sibling tracking products with explicit basis/time/grid metadata.
    # Published TP4 line/sample vectors lack a general stack association; the
    # dedicated validation script remains their reliable reader.
    for candidate in sorted(path.parent.glob("*tracking*.nc"))+sorted(path.parent.glob("*vectors*.nc")):
        if candidate==path:
            continue
        try:
            with xr.open_dataset(candidate,engine="netcdf4") as ds:
                if not all(v in ds for v in ("x_km","y_km","u","v")):
                    raise ValueError("missing physical coordinates or u/v")
                if ds.attrs.get("vector_basis")!="map_xy":
                    raise ValueError("vector basis is not explicitly map_xy")
                if ds.attrs.get("source_stack") not in (stack_id,str(path)):
                    raise ValueError("source stack association is missing or differs")
                if not stack.attrs.get("projection") or ds.attrs.get("projection")!=stack.attrs.get("projection"):
                    raise ValueError("projection metadata do not match")
                if any(ds[name].attrs.get("units")!="m s-1" for name in ("u","v")):
                    raise ValueError("velocity units are not explicit m s-1")
                if any(ds[name].attrs.get("units")!="km" for name in ("x_km","y_km")):
                    raise ValueError("vector coordinate units are not explicit km")
                if "time" in ds.dims:
                    indices=np.flatnonzero(pd.DatetimeIndex(ds.time.values)==stamp)
                    if not len(indices):
                        raise ValueError("requested observation time is absent")
                    ds=ds.isel(time=int(indices[0]))
                elif pd.to_datetime(ds.attrs.get("time_start"))!=stamp:
                    raise ValueError("vector time association is missing or differs")
                x,y,u,v=xr.broadcast(ds.x_km,ds.y_km,ds.u,ds.v)
                if x.size>2_000_000:
                    raise ValueError("vector grid exceeds the bounded overlay limit")
                xx,yy,uu,vv=[np.asarray(a.values).ravel() for a in (x,y,u,v)]
                keep=np.isfinite(xx)&np.isfinite(yy)&np.isfinite(uu)&np.isfinite(vv)
                indices=np.flatnonzero(keep)
                stride=max(1,int(np.ceil(len(indices)/2000)))
                indices=indices[::stride]
                return core.json_safe(dict(status="available",units="m s-1",
                    provenance=dict(source=str(candidate.relative_to(root)),source_stack=stack_id,
                        vector_basis="map_xy",time=stamp,sample_stride=stride,
                        association="explicit source_stack, projection, units and time"),
                    features=[dict(x_km=xx[i],y_km=yy[i],u=uu[i],v=vv[i]) for i in indices]))
        except (OSError,ValueError,KeyError) as exc:
            rejected.append(dict(source=candidate.name,reason=str(exc)))
    return dict(status="unassessed",units="m s-1",features=[],
        reason="No existing vector product has matching time, map basis and source-stack metadata.",
        provenance=dict(rejected=rejected,published_fallback="Published TP4 vectors are read by scripts/classical_tracking_pj4.py; generic stack association is not established."))
