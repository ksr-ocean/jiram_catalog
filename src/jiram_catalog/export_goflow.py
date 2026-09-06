"""Export region stacks in the layout the optical-flow model reads.

The model wants what a synthetic generator gives it: a handful of consecutive
frames at one constant cadence, on a regular square grid, with invalid pixels
zero-encoded and a boolean mask beside them, plus the log-gradient field it
actually trains on.  A JIRAM region stack is none of those things to begin
with -- its time axis is whatever the orbit allowed -- so the export's real
work is to cut the stack into *runs* whose spacing is constant, and to write
each run as its own realization with its own ``dt_img_s``.

No truth velocities exist for real data, so ``u_mid``/``v_mid`` are absent by
construction; a consumer that needs them is looking at the wrong dataset.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import xarray as xr

LOGGER = logging.getLogger(__name__)

__all__ = [
    "constant_cadence_runs",
    "export_stack",
    "log_gradient",
    "write_realization",
]

#: Floor on the gradient magnitude before the logarithm, per metre.
GRADIENT_FLOOR = 1e-30
#: NetCDF deflate level for the exported cubes.
COMPRESSION_LEVEL = 4
UNITS_IMAGE = "W m-2 sr-1 um-1"
UNITS_VELOCITY = "m s-1"


def _seconds(times: np.ndarray) -> np.ndarray:
    """Time axis in seconds from its own first sample."""
    stamps = pd.to_datetime(np.asarray(times)).to_numpy("datetime64[ns]")
    return (stamps - stamps[0]).astype("timedelta64[ns]").astype(np.float64) / 1e9


def constant_cadence_runs(
    times: np.ndarray, *, dt_tol: float = 0.05, min_frames: int = 3
) -> list[tuple[int, int]]:
    """Maximal ``[first, last]`` runs of steps at one constant spacing.

    A run grows from the left while every spacing inside it stays within
    ``dt_tol`` *of the run's own median spacing*, which is what makes the test
    scale-free: a 30 s cadence and a 487 s cadence are held to the same
    relative jitter.  Runs shorter than ``min_frames`` frames are dropped, but
    the step that ended one is free to start the next.
    """
    if min_frames < 2:
        raise ValueError("min_frames must be at least 2")
    if len(np.asarray(times)) < 2:
        return []
    seconds = _seconds(times)
    gaps = np.diff(seconds)
    runs: list[tuple[int, int]] = []
    start = 0
    while start < gaps.size:
        stop = start
        while stop + 1 < gaps.size:
            candidate = gaps[start : stop + 2]
            median = float(np.median(candidate))
            if median <= 0.0:
                break
            if not np.all(np.abs(candidate - median) <= dt_tol * median):
                break
            stop += 1
        if float(np.median(gaps[start : stop + 1])) > 0.0 and stop - start + 2 >= min_frames:
            runs.append((start, stop + 1))
        start = stop + 1
    return runs


def log_gradient(image: np.ndarray, dx_m: float) -> np.ndarray:
    """``log10(max(|grad image|, floor))`` with the gradient per metre.

    Central differences in the interior, one-sided at the edges.  The image is
    expected to be zero-encoded already, which is what the model's own
    convention prescribes for missing data: a valid pixel next to a masked one
    therefore sees the mask as an edge, exactly as the model does.
    """
    if dx_m <= 0.0:
        raise ValueError("dx_m must be positive")
    values = np.asarray(image, dtype=np.float64)
    d_y, d_x = np.gradient(values, dx_m, axis=(-2, -1))
    magnitude = np.hypot(d_x, d_y)
    return np.log10(np.maximum(magnitude, GRADIENT_FLOOR))


def _crop_box(valid: np.ndarray) -> tuple[int, int, int, int]:
    always = valid.all(axis=0)
    if not always.any():
        raise ValueError("no pixel is valid in every frame of the run")
    rows = np.flatnonzero(always.any(axis=1))
    cols = np.flatnonzero(always.any(axis=0))
    return int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1


def _coordinate(stack: xr.Dataset, name: str, members: np.ndarray) -> list[str] | None:
    if name not in stack.coords:
        return None
    return [str(value) for value in np.asarray(stack[name].values)[members]]


def write_realization(
    stack: xr.Dataset,
    members: np.ndarray,
    directory: Path,
    *,
    crop_to_valid: bool = False,
) -> dict[str, Any]:
    """Write one run as ``realization.nc`` plus its ``manifest.json``."""
    directory.mkdir(parents=True, exist_ok=True)
    valid = np.asarray(stack["valid"].isel(time=members).values, dtype=bool)
    image = np.asarray(stack["image"].isel(time=members).values, dtype=np.float32)
    image = np.where(valid & np.isfinite(image), image, 0.0).astype(np.float32)

    km_per_px = float(stack.attrs.get("km_per_px", np.nan))
    dx_img_m = km_per_px * 1000.0
    # Gradients before the crop, so that a pixel on the crop boundary still
    # differences against its real neighbour rather than against the edge.
    loggrad = log_gradient(image, dx_img_m)
    loggrad = np.where(valid, loggrad, 0.0).astype(np.float32)
    if crop_to_valid:
        top, bottom, left, right = _crop_box(valid)
        cut = (slice(None), slice(top, bottom), slice(left, right))
        valid, image, loggrad = valid[cut], image[cut], loggrad[cut]
    else:
        top, left = 0, 0

    times = pd.to_datetime(np.asarray(stack["time"].values)[members])
    gaps = np.diff(_seconds(np.asarray(stack["time"].values)[members]))
    dt_img_s = float(np.median(gaps))

    dataset = xr.Dataset(
        data_vars={
            "image": (("frame", "y_img", "x_img"), image),
            "valid": (("frame", "y_img", "x_img"), valid),
            "loggrad": (("frame", "y_img", "x_img"), loggrad),
        },
        attrs={
            "dx_img_m": dx_img_m,
            "dt_img_s": dt_img_s,
            "units_velocity": UNITS_VELOCITY,
            "units_image": UNITS_IMAGE,
            "region": str(stack.attrs.get("region", "")),
            "band": str(stack.attrs.get("band", "")),
            "level": str(stack.attrs.get("level", "")),
            "projection": str(stack.attrs.get("projection", "")),
            "source": "jiram_catalog",
            "array_order": "C",
            "crop_origin": [int(top), int(left)],
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
    )
    dataset["image"].attrs.update(units=UNITS_IMAGE, long_name="reprojected radiance")
    dataset["loggrad"].attrs.update(
        units="log10(W m-2 sr-1 um-1 / m)", long_name="log10 of the image gradient"
    )
    dataset["valid"].attrs.update(long_name="pixel carries an observation")
    chunks = (1, image.shape[1], image.shape[2])
    encoding = {
        name: {"zlib": True, "complevel": COMPRESSION_LEVEL, "chunksizes": chunks}
        for name in ("image", "valid", "loggrad")
    }
    dataset.to_netcdf(
        directory / "realization.nc", format="NETCDF4", engine="netcdf4", encoding=encoding
    )

    product_ids = _coordinate(stack, "product_id", members)
    sequence_ids = _coordinate(stack, "seq_id", members)
    manifest = {
        "realization": directory.name,
        "n_frames": int(len(members)),
        "dt_img_s": dt_img_s,
        "dx_img_m": dx_img_m,
        "shape": [int(size) for size in image.shape],
        "crop_origin": [int(top), int(left)],
        "region": str(stack.attrs.get("region", "")),
        "band": str(stack.attrs.get("band", "")),
        "level": str(stack.attrs.get("level", "")),
        "frames": [
            {
                "index": int(members[position]),
                "product_id": None if product_ids is None else product_ids[position],
                "seq_id": None if sequence_ids is None else sequence_ids[position],
                "time": pd.Timestamp(times[position]).isoformat(),
            }
            for position in range(len(members))
        ],
        "gaps_s": [float(value) for value in gaps],
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def export_stack(
    stack: xr.Dataset,
    out_dir: str | Path,
    *,
    dt_tol: float = 0.05,
    min_frames: int = 3,
    crop_to_valid: bool = False,
    source: str | Path | None = None,
) -> dict[str, Any]:
    """Cut a stack into constant-cadence runs and write the dataset directory."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    times = np.asarray(stack["time"].values)
    runs = constant_cadence_runs(times, dt_tol=dt_tol, min_frames=min_frames)
    LOGGER.info("%d constant-cadence run(s) in %d time step(s)", len(runs), len(times))

    manifests: list[dict[str, Any]] = []
    for number, (first, last) in enumerate(runs):
        members = np.arange(first, last + 1)
        directory = root / f"r{number:05d}"
        manifests.append(
            write_realization(stack, members, directory, crop_to_valid=crop_to_valid)
        )

    km_per_px = float(stack.attrs.get("km_per_px", np.nan))
    specification = {
        "source": "jiram_catalog",
        "source_stack": None if source is None else str(source),
        "region": str(stack.attrs.get("region", "")),
        "band": str(stack.attrs.get("band", "")),
        "level": str(stack.attrs.get("level", "")),
        "projection": str(stack.attrs.get("projection", "")),
        "dx_img_m": km_per_px * 1000.0,
        "km_per_px": km_per_px,
        "dt_tol": float(dt_tol),
        "min_frames": int(min_frames),
        "crop_to_valid": bool(crop_to_valid),
        "units_image": UNITS_IMAGE,
        "units_velocity": UNITS_VELOCITY,
        "truth_velocities": False,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (root / "spec.json").write_text(json.dumps(specification, indent=2), encoding="utf-8")
    dataset_manifest = {
        "n_realizations": len(manifests),
        "realizations": [
            {
                "name": manifest["realization"],
                "n_frames": manifest["n_frames"],
                "dt_img_s": manifest["dt_img_s"],
                "dx_img_m": manifest["dx_img_m"],
                "shape": manifest["shape"],
            }
            for manifest in manifests
        ],
    }
    (root / "dataset_manifest.json").write_text(
        json.dumps(dataset_manifest, indent=2), encoding="utf-8"
    )
    return dataset_manifest
