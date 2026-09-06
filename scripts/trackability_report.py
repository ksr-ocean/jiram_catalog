#!/usr/bin/env python3
"""Build the trackability report: which passes and latitude bands support
cloud-velocity retrieval from repeated JIRAM views.

For every unit-of-analysis frame (see ``jiram_catalog.trackability``) this
finds candidate repeat views of the same orbit and band half within a 90 s -
6 h window and a generous boresight-separation proxy, quantifies whether the
expected cloud displacement is resolvable at three reference wind speeds, and
writes:

- ``<mirror>/index/trackability_pairs.parquet``
- ``<mirror>/index/trackability_frames.parquet``
- ``docs/reports/trackability.md``
- ``docs/reports/figures/trackability_heatmap.png``

Usage:
    uv run python scripts/trackability_report.py [--mirror PATH] [--orbits 4 9 ...]
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import LogNorm

from jiram_catalog import trackability as tb
from jiram_catalog.geo import frames_with_geo
from jiram_catalog.pds import mirror_root

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = REPO_ROOT / "docs" / "reports" / "trackability.md"
FIGURE_PATH = REPO_ROOT / "docs" / "reports" / "figures" / "trackability_heatmap.png"
SPEEDS = tuple(int(u) for u in tb.REFERENCE_SPEEDS_MS)


def _write_parquet(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.tmp")
    df.to_parquet(temporary, index=False)
    temporary.replace(path)


def _markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def _band_table(frames: pd.DataFrame) -> list[str]:
    rows = []
    for band in tb.LAT_BAND_NAMES:
        part = frames.loc[frames["lat_band"] == band]
        n = len(part)
        best_dt = part.loc[part["has_partner"], "best_dt_s"]
        rows.append(
            [
                band,
                str(n),
                str(int(part["has_partner"].sum())),
                *(str(int(part[f"trackable_{u}"].sum())) for u in SPEEDS),
                "-" if best_dt.empty else f"{best_dt.median():.1f}",
                "-" if n == 0 else f"{part['median_pixel_km'].median():.2f}",
            ]
        )
    headers = [
        "lat band",
        "frames",
        "with partner",
        "trackable@10",
        "trackable@30",
        "trackable@100",
        "median best dt (s)",
        "median pixel km",
    ]
    return _markdown_table(headers, rows)


def _orbit_table(frames: pd.DataFrame) -> list[str]:
    pivot = (
        frames.groupby(["orbit_dir", "lat_band"], observed=False)["trackable_30"]
        .sum()
        .unstack("lat_band", fill_value=0)
        .reindex(columns=list(tb.LAT_BAND_NAMES), fill_value=0)
    )
    n_frames = frames.groupby("orbit_dir").size()
    rows = []
    for orbit in sorted(pivot.index):
        counts = pivot.loc[orbit]
        rows.append(
            [str(int(orbit)), str(int(n_frames.loc[orbit]))]
            + [str(int(counts[band])) for band in tb.LAT_BAND_NAMES]
        )
    headers = ["orbit", "frames"] + [f"trackable@30 {band}" for band in tb.LAT_BAND_NAMES]
    return _markdown_table(headers, rows)


def _top_passes(frames: pd.DataFrame) -> list[str]:
    lines: list[str] = []
    counts = frames.groupby(["lat_band", "orbit_dir"], observed=False)["trackable_30"].sum()
    for band in tb.LAT_BAND_NAMES:
        lines.append(f"#### {band}")
        lines.append("")
        top = counts.loc[band].sort_values(ascending=False).head(10)
        rows = [[str(int(orbit)), str(int(count))] for orbit, count in top.items() if count > 0]
        if not rows:
            lines.append("(no trackable-30 frames in this band)")
        else:
            lines.extend(_markdown_table(["orbit", "trackable@30 frames"], rows))
        lines.append("")
    return lines


def _sequence_pair_section(seq_pairs: pd.DataFrame) -> tuple[list[str], pd.DataFrame]:
    repeats = seq_pairs.loc[seq_pairs["frac_a"] >= 0.5].sort_values(
        ["orbit_dir", "dt_start_s"], kind="stable"
    )
    lines = []
    rows = [
        [
            str(int(r.orbit_dir)),
            r.half,
            r.seq_id_a,
            r.seq_id_b,
            f"{r.dt_start_s:.1f}",
            str(int(r.n_pairs)),
            f"{r.frac_a:.2f}",
            f"{r.median_d_px_30:.2f}",
        ]
        for r in repeats.itertuples(index=False)
    ]
    headers = [
        "orbit",
        "half",
        "seq A",
        "seq B",
        "dt_start_s",
        "n_pairs",
        "frac_A",
        "median d_px@30",
    ]
    lines.extend(_markdown_table(headers, rows))
    lines.append("")
    lines.append("Deliberate repeat sequences per orbit:")
    lines.append("")
    per_orbit = repeats.groupby("orbit_dir").size().sort_index()
    count_rows = [[str(int(orbit)), str(int(count))] for orbit, count in per_orbit.items()]
    lines.extend(_markdown_table(["orbit", "repeat-sequence pairs"], count_rows))
    return lines, repeats


def _totals(frames: pd.DataFrame, pairs: pd.DataFrame, repeats: pd.DataFrame) -> list[str]:
    rows = [
        ["unit-of-analysis frames", str(len(frames))],
        ["frames with a partner", str(int(frames["has_partner"].sum()))],
        *[
            [f"frames trackable at {u} m/s", str(int(frames[f"trackable_{u}"].sum()))]
            for u in SPEEDS
        ],
        ["candidate pairs", str(len(pairs))],
        ["deliberate repeat-sequence pairs (frac_A >= 0.5)", str(len(repeats))],
        ["orbits represented", str(frames["orbit_dir"].nunique())],
    ]
    return _markdown_table(["quantity", "value"], rows)


def _write_heatmap(frames: pd.DataFrame, path: Path) -> None:
    pivot = (
        frames.groupby(["lat_band", "orbit_dir"], observed=False)["trackable_30"]
        .sum()
        .unstack("orbit_dir", fill_value=0)
        .reindex(index=list(tb.LAT_BAND_NAMES))
    )
    orbits = pivot.columns.to_numpy()
    data = pivot.to_numpy(dtype=float)

    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(max(8.0, 0.12 * len(orbits)), 4.0))
    cmap = plt.get_cmap("viridis").copy()
    cmap.set_under("#dddddd")
    # LogNorm masks non-positive values as invalid rather than clipping them
    # "under", so the zero cells need set_bad, not just set_under.
    cmap.set_bad("#dddddd")
    positive = data[data > 0]
    vmax = float(positive.max()) if positive.size else 1.0
    norm = LogNorm(vmin=0.5, vmax=max(vmax, 1.0))
    image = ax.imshow(data, aspect="auto", origin="lower", cmap=cmap, norm=norm)
    ax.set_yticks(range(len(tb.LAT_BAND_NAMES)))
    ax.set_yticklabels(tb.LAT_BAND_NAMES)
    step = max(1, len(orbits) // 30)
    ax.set_xticks(range(0, len(orbits), step))
    ax.set_xticklabels([str(int(o)) for o in orbits[::step]], rotation=90, fontsize=6)
    ax.set_xlabel("orbit")
    ax.set_ylabel("latitude band")
    ax.set_title("Trackable-30 frame counts (log scale; grey = 0)")
    fig.colorbar(image, ax=ax, label="trackable-30 frames")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def build_report(mirror: str | Path | None, orbits: list[int] | None) -> None:
    root = mirror_root(mirror)
    raw = frames_with_geo(root, orbits)
    unit = tb.select_unit_of_analysis(raw)
    pairs = tb.build_pairs(unit)
    frames = tb.add_per_frame_stats(unit, pairs)
    seq_pairs = tb.build_sequence_pairs(unit, pairs)

    _write_parquet(frames, root / "index" / "trackability_frames.parquet")
    _write_parquet(pairs, root / "index" / "trackability_pairs.parquet")
    _write_heatmap(frames, FIGURE_PATH)

    seq_section, repeats = _sequence_pair_section(seq_pairs)

    lines = [
        "# JIRAM trackability report",
        "",
        "## Method",
        "",
        "Unit of analysis: rows of `frames_with_geo` with `geo_ok`, "
        "`on_planet_frac >= 0.3`, `bore_emission <= 70`. A candidate pair is "
        "two frames of the same orbit and band half, 90 s to 6 h apart, "
        f"whose boresights fall within `{tb.SEPARATION_FACTOR:g} * "
        "min(median_pixel_km)` km of each other (sphere radius 69911 km). "
        "**This is an overlap proxy, not an exact footprint intersection**: "
        "it is deliberately generous so it rarely misses a genuine repeat "
        "view, at the cost of also admitting boresight-near pairs whose "
        "footprints do not truly overlap and missing footprint overlaps "
        "whose boresights are far apart (e.g. a fast polar scan). Expected "
        "displacement at reference speed U is "
        "`d_px = U * dt / (1000 * max_pixel_km)`; a pair is trackable at U "
        f"when `{tb.DPX_MIN} <= d_px <= {tb.DPX_MAX}`. `best_dt_s` (revised "
        "2026-09-05) is the smallest dt among a frame's partners trackable "
        "at 30 m/s; NaN when it has none.",
        "",
        "**Judgment call (nearest-partner-per-sequence reduction)**: the "
        "spec's separation threshold alone (`0.5 * 432 * min(pixel_km)`, "
        "implemented literally here) is wide enough that, in PJ4's dense "
        "polar mosaics, one frame's boresight falls within range of several "
        "neighbouring raster positions in the next repeat sequence, not "
        "only its true same-position repeat. Verified numerically on the "
        "paper's own PJ4 fixture: the closest wrong-position pair is nearer "
        "than the farthest correct-position pair, so no fixed threshold can "
        "separate the two cases while keeping every correct-position pair. "
        "`build_pairs` therefore keeps, for each frame and each other "
        "sequence within the window, only its single closest match. This is "
        "an addition to the threshold, not a replacement for it -- the "
        "threshold still gates whether a sequence contributes a partner at "
        "all. See `src/jiram_catalog/trackability.py` module docstring.",
        "",
        "## Per latitude band",
        "",
    ]
    lines.extend(_band_table(frames))
    lines.extend(["", "## Per orbit (trackable-30 frame counts by band)", ""])
    lines.extend(_orbit_table(frames))
    lines.extend(["", "## Top 10 passes per band, by trackable-30 count", ""])
    lines.extend(_top_passes(frames))
    lines.extend(
        [
            "## Deliberate repeat sequences (sequence pairs with frac_A >= 0.5)",
            "",
        ]
    )
    lines.extend(seq_section)
    lines.extend(["", "## Totals", ""])
    lines.extend(_totals(frames, pairs, repeats))
    lines.append("")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mirror", default=None)
    parser.add_argument("--orbits", type=int, nargs="+", default=None)
    args = parser.parse_args()
    build_report(args.mirror, args.orbits)
    print(f"wrote {REPORT_PATH}")
    print(f"wrote {FIGURE_PATH}")


if __name__ == "__main__":
    main()
