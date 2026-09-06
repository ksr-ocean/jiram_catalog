#!/usr/bin/env python
"""Generate the pedagogical-review figures from real data on the mirror.

Run with ``uv run python docs/pedagogy/make_figures.py`` from the
repository root. Reuses ``jiram_catalog`` modules and existing on-disk
products (raw archive frames, the fitted paper grid, the strip
library, the trackability report figure); does not modify any of them.
Writes PNGs (dpi 150) into ``docs/pedagogy/figures/``.

This script is part of the pedagogical-review deliverable itself
(docs/specs/2026-09-06_pedagogical_review.md) and is not part of the
jiram_catalog package or its own test/spec/gate loop.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from jiram_catalog.config import mirror_root, paper_data_root  # noqa: E402
from jiram_catalog.geometry import KernelSet, frame_geometry  # noqa: E402
from jiram_catalog.reproject import PAPER_GRID, reproject_frame  # noqa: E402
from jiram_catalog.stats2d import strip_statistics  # noqa: E402
from jiram_catalog.strips import read_strip  # noqa: E402
from jiram_catalog.vicar import read_vicar  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
FIGDIR = REPO / "docs" / "pedagogy" / "figures"
FIGDIR.mkdir(parents=True, exist_ok=True)
DPI = 150

MIRROR = mirror_root()
PAPER = paper_data_root()

# The orbit-4, M-band, n01a frame used throughout the gates: the first of
# the 48 published perijove-4 frames.
PRODUCT_ID = "JIR_IMG_RDR_2017033T114006_V02"
IMAGE_TIME = "2017-02-02T11:40:03.870"
ORBIT = 4


def _img_path(product_id: str, orbit: int) -> Path:
    return (
        MIRROR
        / "pds4"
        / "juno_jiram_bundle"
        / "data_calibrated"
        / f"orbit{orbit:02d}"
        / f"{product_id}.IMG"
    )


def _read_frame(product_id: str, orbit: int, lines: int = 128, samples: int = 432) -> np.ndarray:
    """Raw calibrated frame, little-endian float32 (see docs/decisions.md)."""
    raw = np.fromfile(_img_path(product_id, orbit), dtype="<f4")
    return raw.reshape(lines, samples)


def fig_a_frame_and_latitude(kernels: KernelSet) -> None:
    """(a) one raw 128x432 frame next to its per-pixel latitude map."""
    image = _read_frame(PRODUCT_ID, ORBIT)
    geo = frame_geometry(IMAGE_TIME, "M", kernels)
    lat = np.where(geo.on_planet, geo.lat, np.nan)

    fig, axes = plt.subplots(1, 2, figsize=(9.6, 4.4), dpi=DPI)
    im0 = axes[0].imshow(image, origin="lower", cmap="inferno", vmin=0.0, vmax=0.55)
    axes[0].set_title(f"raw radiance\n{PRODUCT_ID}")
    axes[0].set_xlabel("sample")
    axes[0].set_ylabel("line")
    fig.colorbar(im0, ax=axes[0], shrink=0.8, label="W m$^{-2}$ sr$^{-1}$ $\\mu$m$^{-1}$")

    im1 = axes[1].imshow(lat, origin="lower", cmap="viridis")
    axes[1].set_title("per-pixel planetocentric latitude\n(SPICE geometry engine)")
    axes[1].set_xlabel("sample")
    axes[1].set_ylabel("line")
    fig.colorbar(im1, ax=axes[1], shrink=0.8, label="latitude (deg)")

    fig.suptitle("Figure (a): one JIRAM camera frame and its computed geometry")
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.95))
    fig.savefig(FIGDIR / "fig_a_frame_and_latitude.png", dpi=DPI)
    plt.close(fig)
    print("wrote fig_a_frame_and_latitude.png")


def fig_b_paper_vs_reprojection(kernels: KernelSet) -> None:
    """(b) the paper's n01a map beside our reprojection, with the difference."""
    paper, label = read_vicar(PAPER / "n01_15km" / "n01a.map")
    image = _read_frame(PRODUCT_ID, ORBIT)
    geo = frame_geometry(str(label["IMAGE_TIME"]), "M", kernels)
    ours, weight = reproject_frame(image, geo, PAPER_GRID, paper.shape)

    rows = np.where(weight.sum(axis=1) > 0)[0]
    cols = np.where(weight.sum(axis=0) > 0)[0]
    pad = 15
    r0, r1 = max(rows.min() - pad, 0), min(rows.max() + pad, paper.shape[0])
    c0, c1 = max(cols.min() - pad, 0), min(cols.max() + pad, paper.shape[1])
    paper_crop = paper[r0:r1, c0:c1]
    ours_crop = ours[r0:r1, c0:c1]
    weight_crop = weight[r0:r1, c0:c1]
    both = (weight_crop > 0) & (paper_crop != 0)
    diff = np.where(both, ours_crop - paper_crop, np.nan)
    ncc = float(np.corrcoef(ours_crop[both], paper_crop[both])[0, 1])

    vmax = float(np.nanpercentile(paper_crop[paper_crop != 0], 99))
    fig, axes = plt.subplots(1, 3, figsize=(13.5, 4.6), dpi=DPI)
    im0 = axes[0].imshow(
        np.where(paper_crop != 0, paper_crop, np.nan),
        origin="lower", cmap="inferno", vmin=0.0, vmax=vmax,
    )
    axes[0].set_title("published map (n01a)")
    fig.colorbar(im0, ax=axes[0], shrink=0.8)

    im1 = axes[1].imshow(
        np.where(weight_crop > 0, ours_crop, np.nan),
        origin="lower", cmap="inferno", vmin=0.0, vmax=vmax,
    )
    axes[1].set_title("our reprojection\n(inverse camera model, PAPER_GRID)")
    fig.colorbar(im1, ax=axes[1], shrink=0.8)

    lim = float(np.nanpercentile(np.abs(diff[np.isfinite(diff)]), 99)) if np.isfinite(diff).any() else 0.01
    im2 = axes[2].imshow(diff, origin="lower", cmap="RdBu_r", vmin=-lim, vmax=lim)
    axes[2].set_title(f"difference (ours - paper)\nNCC = {ncc:.4f}")
    fig.colorbar(im2, ax=axes[2], shrink=0.8)

    for axis in axes:
        axis.set_xlabel("sample")
        axis.set_ylabel("line")
    fig.suptitle(
        "Figure (b): reproducing one published perijove-4 map from the raw archive frame"
    )
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.94))
    fig.savefig(FIGDIR / "fig_b_paper_vs_reprojection.png", dpi=DPI)
    plt.close(fig)
    print(f"wrote fig_b_paper_vs_reprojection.png (NCC={ncc:.4f}, both.sum()={int(both.sum())})")


def _contour(ax, values, xs, ys, levels, *, period=None, **kwargs):
    """Minimal graticule-style contouring (period wraps an angle, e.g. longitude)."""
    if period is not None:
        for level in levels:
            half = 0.5 * period
            wrapped = (values - level + half) % period - half
            wrapped = np.where(np.abs(wrapped) > 0.5 * half, np.nan, wrapped)
            if np.isfinite(wrapped).any():
                finite = wrapped[np.isfinite(wrapped)]
                if finite.min() <= 0.0 <= finite.max():
                    ax.contour(xs, ys, wrapped, levels=[0.0], **kwargs)
    else:
        finite = values[np.isfinite(values)]
        keep = [lv for lv in levels if finite.size and finite.min() <= lv <= finite.max()]
        if keep:
            ax.contour(xs, ys, values, levels=keep, **kwargs)


STRIP_ID = "24_M_2019360T202519_01"


def fig_c_strip_graticule() -> None:
    """(c) one mid-latitude strip with its graticule."""
    ds = read_strip(MIRROR, STRIP_ID)
    image = np.where(np.asarray(ds["valid"].values), np.asarray(ds["image"].values), np.nan)
    x = np.asarray(ds["x_km"].values)
    y = np.asarray(ds["y_km"].values)
    lat = np.asarray(ds["lat"].values)
    lon = np.asarray(ds["lon_east"].values)

    fig, ax = plt.subplots(figsize=(7.2, 8.4), dpi=DPI)
    vmax = float(np.nanpercentile(image, 99))
    im = ax.imshow(
        image, origin="lower", cmap="inferno", vmin=0.0, vmax=vmax,
        extent=(x[0], x[-1], y[0], y[-1]), aspect="equal",
    )
    lat_levels = np.arange(np.floor(np.nanmin(lat) / 2.0) * 2.0, np.nanmax(lat) + 2.0, 2.0)
    lon_levels = np.arange(0.0, 360.0, 10.0)
    _contour(ax, lat, x, y, lat_levels, colors="#8fd3ff", linewidths=0.6, alpha=0.85)
    _contour(ax, lon, x, y, lon_levels, period=360.0, colors="#ffd166", linewidths=0.6, alpha=0.85)
    ax.set_xlabel("local east (km)")
    ax.set_ylabel("local north (km)")
    ax.set_title(
        f"Figure (c): strip {STRIP_ID}\n"
        f"orbit {ds.attrs['orbit']}, band {ds.attrs['band']}, "
        f"{ds.attrs['km_per_px']:g} km/px, centre {ds.attrs['center_lat']:.1f} N, "
        f"{ds.attrs['center_lon_east']:.1f} E\n"
        "(blue: latitude every 2 deg; gold: longitude every 10 deg)"
    )
    fig.colorbar(im, ax=ax, shrink=0.75, label="W m$^{-2}$ sr$^{-1}$ $\\mu$m$^{-1}$")
    fig.tight_layout()
    fig.savefig(FIGDIR / "fig_c_strip_graticule.png", dpi=DPI)
    plt.close(fig)
    print("wrote fig_c_strip_graticule.png")


def fig_d_spectrum_structure() -> None:
    """(d) the isotropic spectrum and a structure function of that strip."""
    ds = read_strip(MIRROR, STRIP_ID)
    stats = strip_statistics(ds)

    k = np.asarray(stats["k"].values)
    energy = np.asarray(stats["E"].values)
    inside = np.asarray(stats["inside_disc"].values)
    keep = inside & (k > 0) & np.isfinite(energy) & (energy > 0)

    r = np.asarray(stats["r"].values) / 1000.0
    s2 = np.asarray(stats["S2"].values)
    keep_s2 = np.isfinite(s2) & (s2 > 0)

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.4), dpi=DPI)
    axes[0].loglog(k[keep], energy[keep], "-", color="#2a6fdb")
    axes[0].set_xlabel("angular wavenumber $k$ (rad m$^{-1}$)")
    axes[0].set_ylabel("$E(k)$ (variance rad$^{-1}$ m)")
    axes[0].set_title("isotropic (shell) spectrum")
    axes[0].grid(True, which="both", alpha=0.3)

    axes[1].loglog(r[keep_s2], s2[keep_s2], "-", color="#d84a4a")
    axes[1].set_xlabel("lag $r$ (km)")
    axes[1].set_ylabel("$S_2(r)$")
    axes[1].set_title("second-order structure function")
    axes[1].grid(True, which="both", alpha=0.3)

    fig.suptitle(f"Figure (d): masked statistics of strip {STRIP_ID}")
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.93))
    fig.savefig(FIGDIR / "fig_d_spectrum_structure.png", dpi=DPI)
    plt.close(fig)
    print("wrote fig_d_spectrum_structure.png")


def fig_e_trackability_heatmap() -> None:
    """(e) the trackability heatmap: copy the existing report figure."""
    source = REPO / "docs" / "reports" / "figures" / "trackability_heatmap.png"
    target = FIGDIR / "fig_e_trackability_heatmap.png"
    shutil.copyfile(source, target)
    print(f"copied {source} -> {target}")


def _box(ax, xy, w, h, text, *, fc="#eef3fb", ec="#3060a0"):
    from matplotlib.patches import FancyBboxPatch

    patch = FancyBboxPatch(
        xy, w, h, boxstyle="round,pad=0.02,rounding_size=0.02",
        linewidth=1.2, edgecolor=ec, facecolor=fc,
    )
    ax.add_patch(patch)
    ax.text(
        xy[0] + w / 2, xy[1] + h / 2, text, ha="center", va="center", fontsize=9.5,
    )


def fig_f_spice_and_pinhole() -> None:
    """(f) a sketch of the SPICE frame chain and of the pinhole model."""
    fig, axes = plt.subplots(1, 2, figsize=(12.5, 5.2), dpi=DPI)

    # -- left: the SPICE frame chain -----------------------------------
    ax = axes[0]
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 8)
    ax.axis("off")
    ax.set_title("SPICE frame chain (geometry.band_geometry)", fontsize=11)

    _box(ax, (0.3, 6.2), 3.0, 1.1, "band frame\n(JUNO_JIRAM_I_MBAND)\npixel ray, apparent")
    _box(ax, (3.9, 6.2), 3.0, 1.1, "J2000\npxform(frame, J2000, et)")
    _box(ax, (7.4, 6.2), 2.3, 1.1, "aberrate(...,\ninverse=True)\ngeometric ray")
    ax.annotate("", xy=(3.85, 6.75), xytext=(3.35, 6.75), arrowprops=dict(arrowstyle="->"))
    ax.annotate("", xy=(7.35, 6.75), xytext=(6.95, 6.75), arrowprops=dict(arrowstyle="->"))

    _box(ax, (0.3, 4.4), 3.0, 1.1, "IAU_JUPITER\npxform(J2000, IAU_JUPITER,\ntrgepc)")
    ax.annotate(
        "", xy=(1.8, 5.55), xytext=(8.5, 6.15),
        arrowprops=dict(arrowstyle="->", connectionstyle="arc3,rad=0.25", color="#555"),
    )
    ax.text(5.1, 5.85, "geometric ray, IAU_JUPITER frame", fontsize=8, color="#555")

    _box(ax, (3.9, 4.4), 3.0, 1.1, "ellipsoid_intercept\n(obspos, ray, radii)")
    ax.annotate("", xy=(3.85, 4.95), xytext=(3.35, 4.95), arrowprops=dict(arrowstyle="->"))

    _box(ax, (7.4, 4.4), 2.3, 1.1, "spoint (km)\nlat, lon, emission,\nincidence, phase")
    ax.annotate("", xy=(7.35, 4.95), xytext=(6.95, 4.95), arrowprops=dict(arrowstyle="->"))

    _box(
        ax, (0.5, 2.2), 9.0, 1.5,
        "obspos = base_point - geometric(apparent_vec)\n"
        "sincpt's srfvec and every pixel ray are *apparent* (LT+S);\n"
        "both are un-aberrated the same way before the ellipsoid intercept\n"
        "(the fix that took the geometry engine from 4e-3 deg to 8e-5 deg agreement)",
        fc="#fff2e0", ec="#c07a1a",
    )
    ax.annotate("", xy=(5.0, 3.7), xytext=(5.0, 4.35), arrowprops=dict(arrowstyle="->", color="#c07a1a"))

    _box(
        ax, (0.5, 0.3), 9.0, 1.4,
        "reproject.project_to_pixels runs this chain backwards:\n"
        "(lat, lon) -> surface point -> IAU_JUPITER -> J2000 (forward aberration)\n"
        "-> band frame -> pinhole inverse -> fractional (line, sample)",
        fc="#eafaf1", ec="#1c8a5a",
    )

    # -- right: the pinhole model ---------------------------------------
    ax = axes[1]
    ax.set_xlim(-1.4, 3.4)
    ax.set_ylim(-1.6, 1.6)
    ax.axis("off")
    ax.set_title("pixel_directions: pinhole model in the band frame", fontsize=11)

    # detector plane at x=2 (schematic), focal point at origin
    ax.plot([2.0, 2.0], [-1.1, 1.1], color="#333", linewidth=1.5)
    ax.text(2.05, 1.2, "detector\n(128 lines x 432 samples)", fontsize=8, ha="left")
    ax.plot(0, 0, "o", color="#333", markersize=5)
    ax.text(0.05, -0.22, "focal point\n(band frame origin)", fontsize=8)

    rays_y = [0.9, 0.45, 0.0, -0.45, -0.9]
    for y in rays_y:
        ax.plot([0, 2.0], [0, y], color="#2a6fdb", linewidth=0.9, alpha=0.85)
    ax.annotate(
        "", xy=(2.0, 0.9), xytext=(0, 0),
        arrowprops=dict(arrowstyle="->", color="#2a6fdb"),
    )
    ax.text(1.05, 0.62, "d = (x, y, 1) / |(x, y, 1)|", fontsize=8.5, color="#2a6fdb", rotation=22)

    ax.annotate("", xy=(2.0, 0.0), xytext=(0.0, 0.0), arrowprops=dict(arrowstyle="->", color="#333"))
    ax.text(1.0, 0.06, "boresight (0,0,1)", fontsize=8)

    ax.annotate(
        "", xy=(2.3, 0.9), xytext=(2.3, 0.0),
        arrowprops=dict(arrowstyle="<->", color="#555"),
    )
    ax.text(2.35, 0.45, "x = ifov * (lines/2 + 0.5 - line)", fontsize=7.5, color="#555")
    ax.text(-1.35, 1.35, "y (samples) points out of the page;\n" "x (lines) is the spin direction", fontsize=8)

    fig.suptitle("Figure (f): the SPICE engine's forward chain, and its inverse in reproject.py")
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.93))
    fig.savefig(FIGDIR / "fig_f_spice_and_pinhole.png", dpi=DPI)
    plt.close(fig)
    print("wrote fig_f_spice_and_pinhole.png")


def main() -> None:
    kernels = KernelSet.for_orbits(MIRROR, [ORBIT])
    try:
        fig_a_frame_and_latitude(kernels)
        fig_b_paper_vs_reprojection(kernels)
    finally:
        kernels.unload()
    fig_c_strip_graticule()
    fig_d_spectrum_structure()
    fig_e_trackability_heatmap()
    fig_f_spice_and_pinhole()

    manifest = sorted(p.name for p in FIGDIR.glob("*.png"))
    print(f"\n{len(manifest)} figure(s) in {FIGDIR}: {manifest}")


if __name__ == "__main__":
    main()
