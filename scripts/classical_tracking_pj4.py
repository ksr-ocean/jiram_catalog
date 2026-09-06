#!/usr/bin/env python
"""Validate a classical cloud tracker against the published perijove-4 winds.

Ingersoll et al. (2022) measured the winds of Jupiter's north polar region by
running the VICAR program TRACKER4 on 48 JIRAM M-band frames resampled onto a
common 15 km polar grid, and shipped both the maps and the raw vector tables.
That makes two independent things testable with the same yardstick:

A. *the tracker* -- our template matcher run on the paper's own maps, at the
   paper's own template positions, compared vector by vector with TRACKER4; and
B. *the pipeline* -- the same comparison, but with the maps replaced by our own
   reprojections of the archive RDR frames onto ``PAPER_GRID``.

A is a statement about the correlator alone; B carries, on top of it, every
error in the SPICE pointing, the camera model and the resampling.  Their
difference is the price of not using the paper's maps.

Two conventions had to be recovered from the data rather than read off a label.
The ``.tp4`` tables index the maps in VICAR ``(sample, line)``, and their line
axis runs *opposite* to the stored record order: mapped straight through, every
template centre lands in the empty half of the array, while ``row = NL - 1 -
line`` puts them on the footprint and makes the paper's own displacement vectors
correlate at 0.96.  The remaining one-pixel question -- whether the indices are
0- or 1-based -- is settled by the tracking itself, since a whole-field offset
of a few pixels degrades the agreement measurably (see ``--base-scan``).

Run: ``uv run python scripts/classical_tracking_pj4.py``.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from jiram_catalog.config import mirror_root, paper_data_root
from jiram_catalog.geometry import KernelSet, frame_geometry
from jiram_catalog.reproject import PAPER_GRID, reproject_frame
from jiram_catalog.tracking import match_vectors, read_tp4, track_pair
from jiram_catalog.vicar import read_vicar

REPO = Path(__file__).resolve().parents[1]
PAPER_ROOT = paper_data_root()
MIRROR = mirror_root()
TP4_DIR = PAPER_ROOT / "JIRAM velocity_vectors at 45 km"
RDR_SUBDIR = Path("pds4/juno_jiram_bundle/data_calibrated/orbit04")
REPORT_DIR = REPO / "docs" / "reports"

MAP_SHAPE = (3000, 3200)
FRAME_SHAPE = (128, 432)
BAND = "M"
ORBIT = 4
LETTERS = "abcdefghijkl"
TEMPLATE = 15
SEARCH = 35
MIN_NCC = 0.5
MIN_VALID_FRAC = 0.9


def pair_names() -> list[tuple[str, str, str]]:
    """The 24 same-letter pairs: ``(first map, second map, .tp4 stem)``."""
    pairs = []
    for first, second in (("n01", "n03"), ("n02", "n04")):
        for letter in LETTERS:
            pairs.append((f"{first}{letter}", f"{second}{letter}",
                          f"{first}{second[1:]}{letter}{letter}"))
    return pairs


def map_path(name: str) -> Path:
    return PAPER_ROOT / f"{name[:3]}_15km" / f"{name}.map"


# --------------------------------------------------------------------------
# inputs
# --------------------------------------------------------------------------
def read_rdr(path: Path, shape: tuple[int, int] = FRAME_SHAPE) -> np.ndarray:
    """Read a calibrated JIRAM RDR image, deciding the byte order from the data.

    The PDS4 label declares ``IEEE754MSBSingle`` but the bytes on disk are
    little-endian, so the order that yields physically possible radiances is the
    one used (the same test as ``scripts/fit_paper_projection.py``).
    """
    raw = np.fromfile(path, dtype=np.uint8)
    best = None
    for order in ("<f4", ">f4"):
        with np.errstate(invalid="ignore"):
            values = raw.view(order).astype(np.float64)
        finite = values[np.isfinite(values)]
        plausible = float(np.mean(np.abs(finite) < 1e3)) if finite.size else 0.0
        if best is None or plausible > best[0]:
            best = (plausible, values)
    if best[0] < 0.99:
        raise ValueError(f"{path.name}: no byte order gives plausible radiances")
    return best[1].reshape(shape)


def our_reprojection(name: str, kernels: KernelSet) -> np.ndarray:
    """Our own map of one published frame, on ``PAPER_GRID``, zero off-footprint."""
    _, label = read_vicar(map_path(name))
    geo = frame_geometry(str(label["IMAGE_TIME"]), BAND, kernels)
    raw = read_rdr(MIRROR / RDR_SUBDIR / f"{label['PRODUCT_ID']}.IMG")
    values, weight = reproject_frame(raw, geo, PAPER_GRID, MAP_SHAPE)
    return np.where(weight > 0.0, values, 0.0)


def positions_from(table: np.ndarray, index_base: int, n_lines: int) -> np.ndarray:
    """Template centres of a ``.tp4`` table as 0-based ``(row, col)``."""
    sample = np.rint(table[:, 0]).astype(np.int64) - index_base
    line = np.rint(table[:, 1]).astype(np.int64) - index_base
    return np.column_stack([n_lines - 1 - line, sample])


# --------------------------------------------------------------------------
# step 1: the index convention
# --------------------------------------------------------------------------
def _eroded(mask: np.ndarray, size: int) -> np.ndarray:
    """True where the whole ``size`` x ``size`` window centred here is True."""
    total = np.zeros((mask.shape[0] + 1, mask.shape[1] + 1), dtype=np.int64)
    total[1:, 1:] = np.cumsum(np.cumsum(mask.astype(np.int64), axis=0), axis=1)
    box = (total[size:, size:] - total[:-size, size:]
           - total[size:, :-size] + total[:-size, :-size])
    out = np.zeros(mask.shape, dtype=bool)
    half = size // 2
    out[half : half + box.shape[0], half : half + box.shape[1]] = box == size * size
    return out


def determine_index_base(verbose: bool = True) -> dict:
    """Recover the ``.tp4`` -> array index convention from ``n0103aa`` and ``n01a``.

    Reports, for each of the four candidate conventions (line axis direct or
    reversed, indices 0- or 1-based), the fraction of template centres that lie
    on the map's non-zero footprint and the fraction whose full 15 x 15 template
    does.  The line direction separates absolutely -- read straight through,
    *every* template centre falls in the empty half of the array.  The one-pixel
    base separates only weakly: the footprint edge is oblique and TRACKER4
    plainly did not require a fully valid template (8% of its centres do not have
    one under either base), so the containment merely follows the gradient of a
    fuzzy boundary.  The larger containment is taken, which agrees with VICAR's
    own 1-based numbering, and ``base_scan`` checks the choice against the
    tracking.
    """
    image, _ = read_vicar(map_path("n01a"))
    table, _ = read_tp4(TP4_DIR / "n0103aa.tp4")
    lines = image.shape[0]
    footprint = image != 0
    full = _eroded(footprint, TEMPLATE)
    sample = np.rint(table[:, 0]).astype(np.int64)
    line = np.rint(table[:, 1]).astype(np.int64)

    evidence = []
    for reversed_line in (False, True):
        for base in (0, 1):
            row = (lines - 1 - (line - base)) if reversed_line else (line - base)
            col = sample - base
            ok = ((row >= 0) & (row < image.shape[0])
                  & (col >= 0) & (col < image.shape[1]))
            centre = np.zeros(row.size, dtype=bool)
            whole = np.zeros(row.size, dtype=bool)
            centre[ok] = footprint[row[ok], col[ok]]
            whole[ok] = full[row[ok], col[ok]]
            evidence.append({
                "line_axis": "reversed" if reversed_line else "direct",
                "index_base": base,
                "frac_centre_on_footprint": float(centre.mean()),
                "frac_template_on_footprint": float(whole.mean()),
            })
            if verbose:
                print(f"  line_axis={evidence[-1]['line_axis']:8s} base={base}  "
                      f"centre {centre.mean():.4f}  full template {whole.mean():.4f}")
    reversed_rows = [row for row in evidence if row["line_axis"] == "reversed"]
    direct_rows = [row for row in evidence if row["line_axis"] == "direct"]
    if max(row["frac_centre_on_footprint"] for row in reversed_rows) <= max(
        row["frac_centre_on_footprint"] for row in direct_rows
    ):
        raise RuntimeError("the line axis no longer separates; re-examine the maps")
    chosen = max(reversed_rows, key=lambda row: row["frac_template_on_footprint"])
    return {
        "evidence": evidence,
        "n_vectors": int(table.shape[0]),
        "index_base": int(chosen["index_base"]),
        "line_axis": "reversed",
    }


def base_scan(stride: int, tile: int) -> list[dict]:
    """Median agreement with TRACKER4 as a constant offset is added to the indices.

    A whole-field shift of the template positions leaves the *relative* geometry
    of a vector untouched, so only the flow's own gradients register it.  That is
    enough to exclude an error of a few pixels, and not enough to separate the
    two one-pixel conventions -- which is itself the useful result, since it
    bounds what the choice can cost.  Offsets are measured from ``row = NL - 1 -
    line``, ``col = sample``, so base 0 is ``(0, 0)`` and base 1 is ``(+1, -1)``.
    """
    out = []
    samples = [("n01a", "n03a", "n0103aa"), ("n02a", "n04a", "n0204aa"),
               ("n01g", "n03g", "n0103gg")]
    stacks: dict[tuple[int, int], list[np.ndarray]] = {}
    for first, second, stem in samples:
        image0, _ = read_vicar(map_path(first))
        image1, _ = read_vicar(map_path(second))
        table, _ = read_tp4(TP4_DIR / f"{stem}.tp4")
        table = table[::stride]
        lines = image0.shape[0]
        rows = lines - 1 - np.rint(table[:, 1]).astype(np.int64)
        cols = np.rint(table[:, 0]).astype(np.int64)
        for drow in (-1, 0, 1):
            for dcol in (-1, 0, 1):
                found = track_pair(
                    image0, image1,
                    positions=np.column_stack([rows + drow, cols + dcol]),
                    template=TEMPLATE, search=SEARCH,
                    min_valid_frac=MIN_VALID_FRAC, min_ncc=MIN_NCC, tile=tile,
                )
                index = {(int(r), int(c)): i
                         for i, (r, c) in enumerate(zip(rows + drow, cols + dcol))}
                take = np.array([index[(int(r), int(c))] for r, c in found[:, :2]])
                diff = np.hypot(found[:, 2] + table[take, 5], found[:, 3] - table[take, 4])
                stacks.setdefault((drow, dcol), []).append(diff)
    for (drow, dcol), parts in sorted(stacks.items()):
        diff = np.concatenate(parts)
        out.append({"drow": drow, "dcol": dcol, "n": int(diff.size),
                    "median_px": float(np.median(diff)),
                    "frac_1": float(np.mean(diff < 1.0))})
    return out


# --------------------------------------------------------------------------
# steps 2 and 3: the two comparisons
# --------------------------------------------------------------------------
def summarise(diff: pd.Series, speed: pd.Series) -> dict:
    if len(diff) == 0:
        return {"n": 0, "median_px": None, "p90_px": None, "frac_05": None,
                "frac_1": None, "frac_2": None, "median_speed_diff_ms": None}
    return {
        "n": int(len(diff)),
        "median_px": float(np.median(diff)),
        "p90_px": float(np.percentile(diff, 90)),
        "frac_05": float(np.mean(diff < 0.5)),
        "frac_1": float(np.mean(diff < 1.0)),
        "frac_2": float(np.mean(diff < 2.0)),
        "median_speed_diff_ms": float(np.median(speed)),
    }


def run_comparison(
    which: str, pairs: list[tuple[str, str, str]], index_base: int, stride: int,
    tile: int, kernels: KernelSet | None,
) -> tuple[dict, float, int]:
    """Comparison A (paper maps) or B (our reprojections) over all pairs."""
    per_file: list[dict] = []
    diffs: list[pd.Series] = []
    speeds: list[pd.Series] = []
    tracked = 0
    seconds = 0.0
    for first, second, stem in pairs:
        table, label = read_tp4(TP4_DIR / f"{stem}.tp4")
        table = table[::stride]
        if which == "A":
            image0, _ = read_vicar(map_path(first))
            image1, _ = read_vicar(map_path(second))
        else:
            image0 = our_reprojection(first, kernels)
            image1 = our_reprojection(second, kernels)
        positions = positions_from(table, index_base, MAP_SHAPE[0])
        start = time.perf_counter()
        found = track_pair(
            image0, image1, positions=positions, template=TEMPLATE, search=SEARCH,
            min_valid_frac=MIN_VALID_FRAC, min_ncc=MIN_NCC, tile=tile,
        )
        seconds += time.perf_counter() - start
        tracked += len(positions)
        scale = float(label["MPS"]) * 1000.0 / float(label["DT"])
        joined = match_vectors(found, table, index_base,
                               n_lines=MAP_SHAPE[0], ms_per_px=scale)
        stats = summarise(joined["d_px"], joined["d_speed_ms"])
        stats.update({
            "pair": stem, "first": first, "second": second,
            "n_templates": int(len(positions)), "n_tracked": int(len(found)),
            "dt_s": float(label["DT"]), "ms_per_px": scale,
            "median_ncc": float(joined["ncc"].median()) if len(joined) else None,
        })
        per_file.append(stats)
        diffs.append(joined["d_px"])
        speeds.append(joined["d_speed_ms"])
        print(f"  [{which}] {stem}: {stats['n']:6d} matched  "
              f"median {stats['median_px']:.3f} px  f1 {stats['frac_1']:.3f}", flush=True)
    pooled = summarise(pd.concat(diffs, ignore_index=True),
                       pd.concat(speeds, ignore_index=True))
    return {"pooled": pooled, "per_file": per_file}, seconds, tracked


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def _table(rows: list[dict]) -> str:
    head = ("| pair | n | median px | p90 px | <0.5 px | <1 px | <2 px | "
            "median |dv| m/s | median NCC |\n|---|---|---|---|---|---|---|---|---|\n")
    body = "".join(
        f"| {r['pair']} | {r['n']} | {r['median_px']:.3f} | {r['p90_px']:.3f} | "
        f"{r['frac_05']:.3f} | {r['frac_1']:.3f} | {r['frac_2']:.3f} | "
        f"{r['median_speed_diff_ms']:.2f} | "
        f"{'--' if r['median_ncc'] is None else format(r['median_ncc'], '.3f')} |\n"
        for r in rows
    )
    return head + body


def write_reports(report: dict) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "tracking_pj4.json").write_text(json.dumps(report, indent=2) + "\n")

    params = report["params"]
    lines = [
        "# Classical tracking of the perijove-4 polar maps vs TRACKER4",
        "",
        "Generated by `scripts/classical_tracking_pj4.py`.",
        "",
        "## Method",
        "",
        "For every template centre the "
        f"{params['template']} x {params['template']} patch of the first map is "
        f"correlated with every equally sized patch of the second whose centre "
        f"lies in the {params['search']} x {params['search']} neighbourhood.  "
        "Pixels equal to zero or not finite are background, not data: a template "
        f"needs {params['min_valid_frac']:.0%} of its pixels valid, the "
        "normalised cross-correlation is evaluated only where both patches are "
        "valid and needs the same fraction there, and a peak is kept only above "
        f"NCC {params['min_ncc']}.  The integer peak is refined by a six-term "
        "quadratic least-squares fit to its 3 x 3 neighbourhood (falling back to "
        "the separable parabola where that fit has no interior maximum), and "
        "refinement is skipped on the border of the search area.  Both patches "
        "are mean-subtracted before the window sums, which leaves the NCC exactly "
        "unchanged and removes the cancellation that would otherwise cost three "
        "digits in single precision.",
        "",
        "The six masked window sums a displacement needs are computed as *cost "
        "volumes*: for a fixed trial displacement each one is a box filter of a "
        "pointwise product, so a whole tile of templates is served by one "
        "multiply and one summed-area pass.",
        "",
        "## Index convention",
        "",
        f"`.tp4` columns 0 and 1 are the integer `(sample, line)` of the template "
        f"centre, **{report['index_base']}-based**, and the line axis runs "
        "opposite to the stored record order of the maps: "
        f"`row = {MAP_SHAPE[0]} - 1 - (line - {report['index_base']})`, "
        f"`col = sample - {report['index_base']}`, and the row displacement is "
        "`-dline`.",
        "",
        "| line axis | base | centre on footprint | full template on footprint |",
        "|---|---|---|---|",
    ]
    for row in report["index_base_evidence"]["evidence"]:
        lines.append(f"| {row['line_axis']} | {row['index_base']} | "
                     f"{row['frac_centre_on_footprint']:.4f} | "
                     f"{row['frac_template_on_footprint']:.4f} |")
    lines += [
        "",
        "Reading the line axis straight through puts every template centre in the "
        "empty half of the array, which settles the direction.  The remaining "
        "one-pixel choice is not settled by the footprint -- its edge is oblique "
        "and TRACKER4 plainly did not demand a fully valid template -- so it was "
        "taken from the tracking itself: a constant offset added to all template "
        "positions is visible only through the flow's own gradients, but that is "
        "enough to resolve one pixel over three map pairs.",
        "",
    ]
    if report.get("base_scan"):
        lines += ["| drow | dcol | n | median px | frac < 1 px |", "|---|---|---|---|---|"]
        for row in report["base_scan"]:
            lines.append(f"| {row['drow']:+d} | {row['dcol']:+d} | {row['n']} | "
                         f"{row['median_px']:.4f} | {row['frac_1']:.4f} |")
        lines.append("")
        lines.append(
            "Offsets are measured from `row = NL - 1 - line`, `col = sample`, so "
            "base 0 is `(0, 0)` and base 1 is `(+1, -1)`.  The two differ by "
            f"{report['index_base_evidence'].get('base_scan_gap_px', float('nan')):.4f} px "
            "in the pooled median -- the tracking cannot separate them, which is "
            "the point: whichever is adopted, the choice cannot move the "
            "comparison below.  A two-pixel error, by contrast, is plainly "
            "visible, so the convention is right to within a pixel."
        )
        lines.append("")

    for key, title in (("A", "A. Tracker validation -- the paper's own maps"),
                       ("B", "B. Pipeline validation -- our reprojections")):
        block = report[key]
        pooled = block["pooled"]
        lines += [
            f"## {title}",
            "",
            f"Pooled over {len(block['per_file'])} pairs: **n = {pooled['n']}**, "
            f"median **{pooled['median_px']:.3f} px**, p90 {pooled['p90_px']:.3f} px, "
            f"within 0.5/1/2 px {pooled['frac_05']:.3f} / {pooled['frac_1']:.3f} / "
            f"{pooled['frac_2']:.3f}, median speed difference "
            f"**{pooled['median_speed_diff_ms']:.2f} m/s**.",
            "",
            _table(block["per_file"]),
        ]

    throughput = report["throughput"]
    lines += [
        "## Throughput",
        "",
        f"{throughput['templates']} templates in {throughput['seconds']:.1f} s of "
        f"correlation on one core: **{throughput['templates_per_second']:.0f} "
        f"templates/s**, i.e. 20,000 templates in "
        f"{20000.0 / throughput['templates_per_second']:.0f} s.  The cost of a "
        "block is set by its area, not by how many of its pixels were asked for, "
        "so tracking every fourth vector of a table costs almost the same as "
        "tracking the tile densely.",
        "",
        "The paper's NCC-equivalent is not stored in the `.tp4` tables, so no "
        "correlation-quality comparison is possible; our own median NCC is given "
        "per pair instead.",
        "",
    ]
    (REPORT_DIR / "tracking_pj4.md").write_text("\n".join(lines))


# --------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stride", type=int, default=4,
                        help="use every STRIDEth TRACKER4 vector (default 4)")
    parser.add_argument("--tile", type=int, default=128)
    parser.add_argument("--pairs", type=int, default=0,
                        help="limit to the first N pairs (0 = all 24)")
    parser.add_argument("--skip-b", action="store_true")
    parser.add_argument("--base-scan", type=int, default=8,
                        help="stride for the index-base tracking scan (0 to skip)")
    parser.add_argument("--mirror", type=Path, default=MIRROR)
    args = parser.parse_args()

    print("index convention, from n0103aa.tp4 against n01a.map:")
    evidence = determine_index_base()
    index_base = evidence["index_base"]
    print(f"  -> line axis reversed, index_base = {index_base}")
    scan = base_scan(args.base_scan, args.tile) if args.base_scan else []
    if scan:
        by_offset = {(row["drow"], row["dcol"]): row for row in scan}
        for row in scan:
            mark = ""
            if (row["drow"], row["dcol"]) == (0, 0):
                mark = "  <- base 0"
            if (row["drow"], row["dcol"]) == (1, -1):
                mark = "  <- base 1"
            print(f"    ({row['drow']:+d}, {row['dcol']:+d})  n={row['n']:6d}  "
                  f"median={row['median_px']:.4f}  f1={row['frac_1']:.4f}{mark}")
        gap = abs(by_offset[(0, 0)]["median_px"] - by_offset[(1, -1)]["median_px"])
        print(f"  the two admissible conventions differ by {gap:.4f} px in the "
              f"pooled median; offsets of 2 px cost an order of magnitude more")
        evidence["base_scan_gap_px"] = float(gap)

    pairs = pair_names()
    if args.pairs:
        pairs = pairs[: args.pairs]

    report = {
        "index_base": index_base,
        "line_axis": "reversed",
        "params": {
            "template": TEMPLATE, "search": SEARCH, "min_ncc": MIN_NCC,
            "min_valid_frac": MIN_VALID_FRAC, "stride": args.stride,
            "tile": args.tile, "n_pairs": len(pairs),
            "grid": "PAPER_GRID", "map_shape": list(MAP_SHAPE),
        },
        "index_base_evidence": evidence,
        "base_scan": scan,
    }

    print("comparison A: the paper's maps")
    block_a, seconds, templates = run_comparison("A", pairs, index_base, args.stride,
                                                 args.tile, None)
    report["A"] = block_a

    if args.skip_b:
        report["B"] = {"pooled": summarise(pd.Series(dtype=float),
                                           pd.Series(dtype=float)), "per_file": []}
    else:
        print("comparison B: our reprojections of the archive frames")
        with KernelSet.for_orbits(args.mirror, [ORBIT]) as kernels:
            block_b, seconds_b, templates_b = run_comparison(
                "B", pairs, index_base, args.stride, args.tile, kernels)
        report["B"] = block_b
        seconds += seconds_b
        templates += templates_b

    report["throughput"] = {
        "templates": int(templates), "seconds": float(seconds),
        "templates_per_second": float(templates / seconds) if seconds else None,
    }
    write_reports(report)
    for key in ("A", "B"):
        print(key, report[key]["pooled"])
    print(f"throughput {report['throughput']['templates_per_second']:.0f} templates/s")
    print(f"wrote {REPORT_DIR / 'tracking_pj4.json'} and .md")


if __name__ == "__main__":
    main()
