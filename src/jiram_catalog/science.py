"""Analysis preparation with explicit identity, units and sampling provenance.

Readiness is a data-format/cadence check, never a validation of wind accuracy.
The metadata pass stays lazy; common-footprint checks read one plane at a time.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
import subprocess
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

POLICY_VERSION = "science-2026-09-07-v1"
WIND_CAVEAT = "Format and cadence readiness does not validate navigation or wind accuracy."


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [json_safe(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return pd.Timestamp(value).isoformat()
    if isinstance(value, Path):
        return str(value)
    return value


@lru_cache(maxsize=1)
def software_revision() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
            text=True, stderr=subprocess.DEVNULL, timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def observation_id(product: str) -> str:
    return re.sub(r"_V\d+(?=(_|$))", "", str(product), flags=re.IGNORECASE)


def product_version(product: str) -> int:
    found = re.search(r"_V(\d+)(?:_|$)", str(product), re.IGNORECASE)
    return int(found.group(1)) if found else 0


def source_ids(dataset: xr.Dataset) -> list[str]:
    for name in ("product_ids", "product_id"):
        if name in dataset:
            return [str(v) for v in np.asarray(dataset[name].values).reshape(-1)]
        if name in dataset.attrs:
            values = dataset.attrs[name]
            return [str(v) for v in values] if isinstance(values, (list, tuple)) else [str(values)]
    return []


def provenance(dataset: xr.Dataset, *, norm: str = "none") -> dict[str, Any]:
    from .api.images import NIGHT_INCIDENCE_DEG

    sources = source_ids(dataset)
    return json_safe({
        "policy_version": POLICY_VERSION, "software_revision": software_revision(),
        "source_software": dataset.attrs.get("software"),
        "source_file": dataset.encoding.get("source"),
        "sources": sources,
        "observations": sorted({observation_id(v) for v in sources}),
        "versions": {v: product_version(v) for v in sources},
        "instrument": dataset.attrs.get("instrument", "JIRAM"),
        "band": dataset.attrs.get("band"), "norm": norm,
        "units": dataset.image.attrs.get("units", dataset.attrs.get("units_image")),
        "projection": dataset.attrs.get("projection"),
        "quality_policy": dataset.attrs.get("quality_policy"),
        "source_time_steps": dataset.attrs.get("source_time_steps"),
        "withheld_or_superseded_steps": dataset.attrs.get("withheld_or_superseded_steps"),
        "kernels": {k: v for k, v in dataset.attrs.items() if "kernel" in k.lower()},
        "mask": f"finite image AND supplied validity; JunoCam incidence < {NIGHT_INCIDENCE_DEG:g} degrees when present",
        "cautions": [WIND_CAVEAT],
    })


def select_physical_band(dataset: xr.Dataset, band: str | None) -> xr.Dataset:
    if "image" not in dataset:
        raise ValueError("dataset has no image")
    wanted = str(band).strip().upper() if band else None
    if wanted == "RGB":
        raise ValueError("RGB is a display composite; select one physical band")
    selected = dataset
    if "band" in dataset.image.dims:
        names = [str(v) for v in dataset.band.values]
        if wanted is None:
            if len(names) != 1:
                raise ValueError("select an explicit physical band for a multi-band image")
            wanted = names[0].upper()
        if wanted not in [v.upper() for v in names]:
            raise ValueError(f"unknown band {wanted!r}; available: {', '.join(names)}")
        selected = dataset.isel(band=[v.upper() for v in names].index(wanted), drop=True)
    else:
        native = str(dataset.attrs.get("band", "")).upper()
        if wanted and native and wanted != native:
            raise ValueError(f"requested {wanted} but dataset carries {native}")
        wanted = native or wanted or "unknown"
    selected = selected.copy(deep=False)
    selected.attrs = dict(dataset.attrs, band=wanted)
    selected.image.attrs = dict(dataset.image.attrs)
    selected.image.attrs.setdefault("units", dataset.attrs.get("units_image") or (
        "DN" if str(dataset.attrs.get("instrument", "JIRAM")).lower() == "junocam"
        else "W m-2 sr-1 um-1"))
    return selected


def _select_stack(dataset: xr.Dataset, band: str | None) -> tuple[xr.Dataset, int]:
    selected = select_physical_band(dataset, band)
    if set(selected.image.dims) != {"time", "y", "x"}:
        raise ValueError("stack image must have dimensions (time, y, x) after band selection")
    if min(selected.sizes.get("y", 0), selected.sizes.get("x", 0)) < 2:
        raise ValueError("analysis needs at least two pixels on each spatial axis")
    if "time" not in selected.coords:
        raise ValueError("stack has no time coordinate")
    times = pd.DatetimeIndex(pd.to_datetime(selected.time.values)).as_unit("ns")
    if times.isna().any():
        raise ValueError("stack contains unknown observation times")
    if "source_time_index" not in selected.coords:
        selected = selected.assign_coords(source_time_index=("time", np.arange(len(times))))
    chosen: dict[tuple[str, int], int] = {}
    ids = [str(v) for v in selected.product_id.values] if "product_id" in selected else []
    for i, stamp in enumerate(times.asi8):
        identity = observation_id(ids[i]) if len(ids) == len(times) else "time"
        # Processing may correct the label epoch by milliseconds; that does
        # not create another physical observation.
        key = (identity, 0) if len(ids) == len(times) else (identity, int(stamp))
        prior = chosen.get(key)
        if prior is None or (ids and product_version(ids[i]) > product_version(ids[prior])):
            chosen[key] = i
    indices = sorted(chosen.values(), key=lambda i: times.asi8[i])
    removed = len(times) - len(indices)
    selected = selected.isel(time=indices).transpose("time", "y", "x", missing_dims="ignore")
    return selected, removed


def validate_grid(dataset: xr.Dataset) -> float:
    km = float(dataset.attrs.get("km_per_px", np.nan))
    if not np.isfinite(km) or km <= 0:
        raise ValueError("positive finite km_per_px is required")
    for name in ("x_km", "y_km"):
        if name in dataset:
            gaps = np.diff(np.asarray(dataset[name].values, dtype=float))
            if gaps.size and (not np.isfinite(gaps).all() or not np.allclose(gaps, km, rtol=1e-4, atol=1e-6)):
                raise ValueError("analysis requires a regular square grid with increasing x_km/y_km")
    return km


def normalise_frame(dataset: xr.Dataset, norm: str = "none") -> tuple[np.ndarray, np.ndarray, str]:
    from .api.images import NIGHT_INCIDENCE_DEG, normalise_plane, norm_label, parse_norm
    name, parameter = parse_norm(norm)
    if str(dataset.attrs.get("instrument", "JIRAM")).lower() == "jiram" and name in ("lambert", "minnaert"):
        raise ValueError("reflected-light illumination transforms are not supported for JIRAM thermal radiance")
    if name in ("lambert", "minnaert") and "incidence" not in dataset:
        raise ValueError(f"{name} requires measured incidence angles")
    if name == "minnaert" and "emission" not in dataset:
        raise ValueError("minnaert requires measured emission angles")
    image = np.asarray(dataset.image.values, dtype=np.float64)
    if image.ndim != 2:
        raise ValueError("normalisation requires one physical image plane")
    valid = np.isfinite(image)
    if "valid" in dataset:
        valid &= np.asarray(dataset.valid.values, dtype=bool)
    sun = np.asarray(dataset.incidence.values) if "incidence" in dataset else None
    view = np.asarray(dataset.emission.values) if "emission" in dataset else None
    if str(dataset.attrs.get("instrument", "JIRAM")).lower() == "junocam" and sun is not None:
        valid &= np.isfinite(sun) & (sun < NIGHT_INCIDENCE_DEG)
    # Flattening is an optional image transform for thermal data, too; solar
    # incidence must not remove the nightside emission it measures.
    if str(dataset.attrs.get("instrument", "JIRAM")).lower() == "jiram":
        sun = None
    image, valid = normalise_plane(image, valid=valid, incidence=sun, emission=view, norm=name, parameter=parameter)
    return image, valid, norm_label(name, parameter)


def prepare_stack(dataset: xr.Dataset, band: str | None = None, norm: str = "none") -> xr.Dataset:
    selected, removed = _select_stack(dataset, band)
    validate_grid(selected)
    images, masks = [], []
    label = norm
    for i in range(selected.sizes["time"]):
        values, valid, label = normalise_frame(selected.isel(time=i), norm)
        images.append(values.astype(np.float32))
        masks.append(valid)
    if not images:
        raise ValueError("stack contains no observations")
    out = selected.copy(deep=False)
    out["image"] = (("time", "y", "x"), np.stack(images), dict(selected.image.attrs))
    out["valid"] = (("time", "y", "x"), np.stack(masks))
    out.attrs = dict(selected.attrs, norm=label, n_versions_removed=removed)
    out.attrs["analysis_provenance"] = json.dumps(provenance(out, norm=label), sort_keys=True)
    return out


def stack_readiness(dataset: xr.Dataset, band: str | None = None, norm: str = "none",
                    dt_tol: float = 0.05, min_frames: int = 3) -> dict[str, Any]:
    from .export_goflow import constant_cadence_runs
    result: dict[str, Any] = dict(ready=False, reasons=[], n_observations=0,
        n_versions_removed=0, band=band, norm=norm, units=None, km_per_px=None,
        times=[], gaps_s=[], runs=[], n_realizations=0, provenance={})
    try:
        if not np.isfinite(dt_tol) or not 0 <= dt_tol < 1:
            raise ValueError("dt_tol must be finite and in [0, 1)")
        if min_frames < 3:
            raise ValueError("optical-flow export needs at least three frames")
        selected, removed = _select_stack(dataset, band)
        km = validate_grid(selected)
        times = pd.DatetimeIndex(selected.time.values).as_unit("ns")
        gaps = np.diff(times.asi8).astype(float) / 1e9
        result.update(n_observations=len(times), n_versions_removed=removed,
            band=selected.attrs["band"], units=selected.image.attrs["units"],
            km_per_px=km, times=[v.isoformat() for v in times], gaps_s=gaps,
            provenance=provenance(selected, norm=norm))
        if len(times) < min_frames:
            raise ValueError(f"needs {min_frames} independent observations; found {len(times)}")
        if np.any(gaps <= 0):
            raise ValueError("observation cadence must be strictly positive; simultaneous observations remain")
        candidates = constant_cadence_runs(selected.time.values, dt_tol=dt_tol, min_frames=min_frames)
        if not candidates:
            raise ValueError("no run has the required constant cadence")
        for first, last in candidates:
            common = None
            for i in range(first, last + 1):
                _, valid, label = normalise_frame(selected.isel(time=i), norm)
                result["norm"] = label
                common = valid.copy() if common is None else common & valid
            frac = float(common.mean())
            if frac > 0:
                result["runs"].append(dict(first=first, last=last, dt_s=float(np.median(gaps[first:last])),
                    common_valid_frac=frac, n_frames=last-first+1))
        if not result["runs"]:
            raise ValueError("no constant-cadence run has pixels valid in every frame")
        result["n_realizations"] = len(result["runs"])
        result["ready"] = True
        result["provenance"]["norm"] = result["norm"]
    except (ValueError, KeyError, TypeError) as exc:
        result["reasons"].append(str(exc))
    return json_safe(result)


def mask_diagnostics(image: np.ndarray, valid: np.ndarray, dx_m: float) -> dict[str, Any]:
    values = np.asarray(image, dtype=float)
    mask = np.asarray(valid, dtype=bool) & np.isfinite(values)
    if values.ndim != 2 or mask.shape != values.shape or not np.isfinite(dx_m) or dx_m <= 0:
        raise ValueError("diagnostics require a 2D image/mask and positive pixel size")
    # Connected horizontal runs, unioned with the preceding row (four-neighbour).
    parents: list[int] = []
    def root(i: int) -> int:
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    previous: list[tuple[int, int, int]] = []
    for row in mask:
        edges = np.diff(np.r_[False, row, False].astype(np.int8))
        current = []
        previous_index = 0
        for left, right in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            label = len(parents)
            parents.append(label)
            while previous_index < len(previous) and previous[previous_index][1] <= left:
                previous_index += 1
            for a, b, other in previous[previous_index:]:
                if a >= right:
                    break
                parents[root(other)] = root(label)
            current.append((int(left), int(right), label))
        previous = current
    components = len({root(i) for i in range(len(parents))})
    interior = mask.copy()
    interior[0, :] = interior[-1, :] = False
    interior[:, 0] = interior[:, -1] = False
    interior[1:, :] &= mask[:-1, :]
    interior[:-1, :] &= mask[1:, :]
    interior[:, 1:] &= mask[:, :-1]
    interior[:, :-1] &= mask[:, 1:]
    boundary = float((mask & ~interior).sum() / max(mask.sum(), 1))
    pool = values[mask]
    dynamic = float(np.ptp(np.percentile(pool, [1, 99]))) if pool.size else None
    pair = mask[1:] & mask[:-1]
    delta = np.abs(np.diff(values, axis=0))
    row_counts = pair.sum(axis=1)
    row_jump = np.divide(np.where(pair, delta, 0).sum(axis=1), row_counts,
                         out=np.full(len(pair), np.nan), where=row_counts > 0)
    good = row_jump[np.isfinite(row_jump)]
    seam = float(np.max(good) / max(float(np.median(good)), 1e-30)) if good.size else None
    cautions = ["Mask edges convolve the spectrum; these diagnostics do not correct leakage.",
                "Nyquist is a sampling bound; physical effective resolution is unmeasured."]
    if components > 1:
        cautions.append("Disconnected valid regions can increase mask leakage.")
    if boundary > .25:
        cautions.append("A large fraction of valid pixels touch the mask boundary.")
    if seam is not None and seam > 5:
        cautions.append("Large row discontinuity: inspect seams, real edges and illumination.")
    return json_safe(dict(valid_frac=float(mask.mean()), components=components,
        boundary_fraction=boundary, scene_dynamic_range=dynamic,
        row_discontinuity_ratio=seam, cautions=cautions,
        scale={"pixel_m": dx_m, "nyquist_wavelength_m": 2*dx_m,
               "field_extent_m": [values.shape[0]*dx_m, values.shape[1]*dx_m],
               "effective_resolution_m": None}))


def fit_spectrum(k: np.ndarray, E: np.ndarray, k_min: float | None = None,
                 k_max: float | None = None) -> dict[str, Any]:
    x, y = np.asarray(k, dtype=float), np.asarray(E, dtype=float)
    if x.shape != y.shape:
        raise ValueError("k and E must have equal shapes")
    valid = np.isfinite(x) & np.isfinite(y) & (x > 0) & (y > 0)
    if k_min is not None:
        valid &= x >= k_min
    if k_max is not None:
        valid &= x <= k_max
    result = dict(slope=None, intercept=None, n_bins=int(valid.sum()), k_min=k_min,
                  k_max=k_max, standard_error=None,
                  caveats=["Unweighted log-log fit; bins are correlated by taper and mask.",
                           "Fit error is regression error, not independent-observation uncertainty."])
    if valid.sum() < 3:
        result["caveats"].append("At least three positive finite bins are required.")
        return json_safe(result)
    lx, ly = np.log10(x[valid]), np.log10(y[valid])
    if np.ptp(lx) <= 0:
        result["caveats"].append("Wavenumbers need a nonzero range.")
        return json_safe(result)
    slope, intercept = np.polyfit(lx, ly, 1)
    residual = ly - (slope*lx + intercept)
    error = np.sqrt(np.sum(residual**2)/(len(lx)-2)/np.sum((lx-lx.mean())**2))
    result.update(slope=float(slope), intercept=float(intercept),
                  k_min=float(x[valid].min()), k_max=float(x[valid].max()), standard_error=float(error))
    return json_safe(result)
