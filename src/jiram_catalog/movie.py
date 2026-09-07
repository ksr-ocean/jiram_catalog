"""Render a region stack as a movie, one video frame per time step.

The point of the movie is to see whether a stack is *navigated*: a scene that
sits still while the spacecraft swings around it is a good reprojection, and
one that wobbles is not.  So the stretch is computed once over the whole stack
rather than per frame -- a per-frame stretch hides exactly the brightness
drift that reveals a bad frame -- and every frame is drawn on the same axes at
the same scale, with the time stamp and a scale bar burned in.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from uuid import uuid4

import matplotlib

matplotlib.use("Agg")  # no display on a compute node; must precede pyplot

import imageio.v2 as imageio  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import xarray as xr  # noqa: E402

LOGGER = logging.getLogger(__name__)

__all__ = ["stretch_limits", "render_frames", "write_movie"]

#: Longest side of a rendered frame, in pixels.
MAX_SIDE_PX = 1600
#: Rendered frames are a whole number of macroblocks on a side, so that the
#: H.264 encoder stores what it was handed instead of rescaling it.
MACRO_BLOCK = 16
DPI = 100.0
BACKGROUND = "#0b0b10"
FOREGROUND = "#e8e8ee"
#: Round scale-bar lengths, km.
BAR_STEPS_KM: tuple[float, ...] = (100.0, 200.0, 500.0, 1000.0, 2000.0, 5000.0, 10000.0)
#: Above this many pixels the percentile stretch is computed on a subsample;
#: the difference is far below the precision a display stretch needs.
STRETCH_SAMPLE = 40_000_000


def stretch_limits(
    stack: xr.Dataset, percentiles: tuple[float, float] = (1.0, 99.0), *, norm: str | None = None
) -> tuple[float, float]:
    """Robust display limits over every valid pixel of the whole stack.

    Sampled one time step at a time, and strided once a stack is large enough
    that the whole cube would not fit beside itself in memory; a display
    stretch cannot tell the difference.
    """
    steps = int(stack.sizes["time"])
    budget = max(1, STRETCH_SAMPLE // max(steps, 1))
    samples: list[np.ndarray] = []
    for index in range(steps):
        frame = _image_plane(stack, index, norm).ravel()
        stride = max(1, int(np.ceil(frame.size / budget)))
        frame = frame[::stride]
        samples.append(frame[np.isfinite(frame)])
    values = np.concatenate(samples) if samples else np.empty(0)
    if values.size == 0:
        return 0.0, 1.0
    low, high = (float(value) for value in np.percentile(values, percentiles))
    if not high > low:
        high = low + 1.0
    return low, high


def _image_plane(stack: xr.Dataset, index: int, norm: str | None) -> np.ndarray:
    """Normalize only the current frame, keeping movie memory independent of cube size."""
    if norm is not None:
        from .science import normalise_frame
        image, valid, _ = normalise_frame(stack.isel(time=index), norm)
        return np.where(valid, image, np.nan).astype(np.float32)
    image = np.asarray(stack["image"][index].values, dtype=np.float32)
    if "valid" in stack:
        image = np.where(np.asarray(stack.valid[index].values, dtype=bool), image, np.nan)
    return image


def _frame_size(rows: int, cols: int) -> tuple[int, int]:
    """Rendered ``(width, height)`` in pixels, a multiple of the macroblock."""
    scale = min(1.0, MAX_SIDE_PX / max(rows, cols))
    width = max(MACRO_BLOCK, int(round(cols * scale / MACRO_BLOCK)) * MACRO_BLOCK)
    height = max(MACRO_BLOCK, int(round(rows * scale / MACRO_BLOCK)) * MACRO_BLOCK)
    return width, height


def _label(stack: xr.Dataset, index: int) -> str:
    stamp = pd.Timestamp(np.asarray(stack["time"].values)[index])
    parts = [f"{stamp.strftime('%Y-%m-%d %H:%M:%S')} UTC"]
    for name in ("seq_id", "product_id"):
        if name in stack.coords:
            value = str(np.asarray(stack[name].values)[index])
            if value and value not in parts:
                parts.append(value)
    if "n_frames" in stack.coords:
        parts.append(f"n={int(np.asarray(stack['n_frames'].values)[index])}")
    return "   ".join(parts)


def _scale_bar(axes, cols: int, rows: int, km_per_px: float) -> None:
    width_km = cols * km_per_px
    candidates = [step for step in BAR_STEPS_KM if step <= 0.3 * width_km]
    bar_km = candidates[-1] if candidates else width_km / 4.0
    bar_px = bar_km / km_per_px
    x0 = 0.04 * cols
    y0 = 0.045 * rows
    axes.plot(
        [x0, x0 + bar_px],
        [y0, y0],
        color=FOREGROUND,
        linewidth=2.5,
        solid_capstyle="butt",
    )
    axes.text(
        x0 + bar_px / 2.0,
        y0 + 0.018 * rows,
        f"{bar_km:g} km",
        color=FOREGROUND,
        ha="center",
        va="bottom",
        fontsize=9,
    )


def _iter_frames(
    stack: xr.Dataset,
    *,
    percentiles: tuple[float, float] = (1.0, 99.0),
    cmap: str = "gray",
    norm: str | None = None,
):
    """Yield one RGB frame at a time without retaining the full video."""
    rows, cols = int(stack.sizes["y"]), int(stack.sizes["x"])
    width, height = _frame_size(rows, cols)
    low, high = stretch_limits(stack, percentiles, norm=norm)
    km_per_px = float(stack.attrs.get("km_per_px", 1.0))
    colours = plt.get_cmap(cmap).with_extremes(bad=(0.0, 0.0, 0.0, 0.0))

    figure = plt.figure(figsize=(width / DPI, height / DPI), dpi=DPI, facecolor=BACKGROUND)
    axes = figure.add_axes((0.0, 0.0, 1.0, 1.0))
    axes.set_facecolor(BACKGROUND)
    axes.set_axis_off()
    try:
        for index in range(int(stack.sizes["time"])):
            axes.clear()
            axes.set_facecolor(BACKGROUND)
            axes.set_axis_off()
            image = _image_plane(stack, index, norm)
            axes.imshow(
                np.ma.masked_invalid(image),
                origin="lower",
                cmap=colours,
                vmin=low,
                vmax=high,
                interpolation="nearest",
            )
            axes.set_xlim(-0.5, cols - 0.5)
            axes.set_ylim(-0.5, rows - 0.5)
            axes.text(
                0.5,
                0.985,
                _label(stack, index),
                transform=axes.transAxes,
                color=FOREGROUND,
                ha="center",
                va="top",
                fontsize=10,
            )
            _scale_bar(axes, cols, rows, km_per_px)
            figure.canvas.draw()
            yield np.asarray(figure.canvas.buffer_rgba())[..., :3].copy()
    finally:
        plt.close(figure)


def render_frames(stack: xr.Dataset, *, percentiles: tuple[float, float] = (1.0, 99.0),
                  cmap: str = "gray", norm: str | None = None) -> list[np.ndarray]:
    """One RGB array per time step; the writer uses the streaming iterator."""
    return list(_iter_frames(stack, percentiles=percentiles, cmap=cmap, norm=norm))


def _write_movie(
    stack: xr.Dataset,
    path: str | Path,
    *,
    fps: float = 4.0,
    percentiles: tuple[float, float] = (1.0, 99.0),
    cmap: str = "gray",
    norm: str | None = None,
) -> dict[str, Any]:
    """Write ``stack`` as an ``.mp4`` (ffmpeg) or ``.gif``; returns a summary."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = int(stack.sizes.get("time", 0))
    if not count:
        raise ValueError("the stack has no time steps to render")
    if not np.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be positive and finite")
    width, height = _frame_size(int(stack.sizes["y"]), int(stack.sizes["x"]))
    frames = _iter_frames(stack, percentiles=percentiles, cmap=cmap, norm=norm)
    suffix = target.suffix.lower()
    if suffix == ".gif":
        imageio.mimwrite(target, list(frames), format="GIF", duration=1000.0 / fps, loop=0)
    elif suffix in (".mp4", ".m4v", ".mov", ".avi", ".mkv", ".webm"):
        # imageio-ffmpeg ships its own ffmpeg; no system binary is needed.
        with imageio.get_writer(
            target,
            format="FFMPEG",
            mode="I",
            fps=fps,
            codec="libx264",
            quality=8,
            macro_block_size=MACRO_BLOCK,
            ffmpeg_log_level="error",
        ) as writer:
            for frame in frames:
                writer.append_data(frame)
    else:
        raise ValueError(f"unsupported movie suffix: {target.suffix!r}")
    LOGGER.info("wrote %d frame(s) to %s", count, target)
    return {
        "path": target,
        "frames": count,
        "width": int(width),
        "height": int(height),
        "fps": float(fps),
        "duration_s": count / float(fps),
        "bytes": target.stat().st_size,
    }


def write_movie(stack: xr.Dataset, path: str | Path, *, fps: float = 4.0,
                percentiles: tuple[float, float] = (1.0, 99.0), cmap: str = "gray",
                norm: str | None = None) -> dict[str, Any]:
    """Publish a complete movie atomically, preserving a previous file on failure."""
    target = Path(path)
    temporary = target.with_name(f".{target.stem}.{uuid4().hex}{target.suffix}")
    try:
        result = _write_movie(stack, temporary, fps=fps, percentiles=percentiles, cmap=cmap, norm=norm)
        temporary.replace(target)
        result["path"] = target
        return result
    finally:
        temporary.unlink(missing_ok=True)
