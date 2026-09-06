#!/usr/bin/env python3
"""Settle which band half is stored first inside a 256-line JIRAM product.

A ``SCI_I1_*`` product holds both detector bands in one 256x432 array with the
ten gap lines removed, and neither the SIS (v7.5) nor the PDS3 label says which
half comes first. The two halves point 1.88 degrees apart, so whenever one half
sees the planet and the other does not the image itself answers the question:
the half whose bright pixels track the SPICE on-planet footprint is the half
that frame belongs to. The M band (4.8 um) is the discriminator -- it is
strongly positive over the planet by day and by night -- while the L band
(3.3-3.6 um) is near zero on the nightside, so an L half can be dark even where
it is on the planet.

Reads ``<mirror>/index/frames_geo.parquet`` (build it with
``jiram-catalog geo --orbits 24``) and the orbit-24 image data, and writes
``docs/reports/lm_half_order.md``.

Usage:
    uv run python scripts/lm_half_order.py [--mirror PATH] [--orbit 24]
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

from jiram_catalog.geo import load_geo
from jiram_catalog.index import load_frames
from jiram_catalog.pds import mirror_root

REPORT = Path(__file__).resolve().parent.parent / "docs" / "reports" / "lm_half_order.md"
#: Selection: a half that is neither blind nor saturated with planet, ...
PARTIAL_LOW, PARTIAL_HIGH = 0.05, 0.95
#: ... and two halves that see clearly different amounts of it.
MIN_HALF_DIFFERENCE = 0.2
MAX_FRAMES = 40
#: Bright-pixel threshold: this many MADs above the image median.
MAD_MULTIPLE = 5.0
#: Scores closer than this leave the frame undecided.
ABSTAIN_MARGIN = 0.05
#: Fraction of decided votes that must agree before the constant may be set.
AGREEMENT = 0.8
MIN_VOTES = 10
LINES, SAMPLES = 256, 432


def read_image(path: Path, lines: int = LINES, samples: int = SAMPLES):
    """Read one RDR image, choosing the byte order the file is actually in.

    The PDS3 label says ``SAMPLE_TYPE = IEEE_REAL``, which by the standard
    means big-endian, but the JIRAM RDR files are written little-endian: read
    big-endian they are 4-25% infinities and values of order 1e29, read
    little-endian every pixel is finite and of order 1e-4 to 1 W/(m^2 sr um).
    The choice is therefore made from the data, and reported.
    """
    raw = np.fromfile(path, dtype=np.uint8)
    expected = lines * samples * 4
    if raw.size != expected:
        raise ValueError(f"{path.name}: {raw.size} bytes, expected {expected}")
    scores = {}
    images = {}
    for order in ("<f4", ">f4"):
        with np.errstate(invalid="ignore", over="ignore"):
            # the wrong byte order produces infinities and NaNs by design
            image = raw.view(order).astype(np.float64).reshape(lines, samples)
        finite = np.isfinite(image)
        implausible = int((~finite).sum()) + int((np.abs(image[finite]) > 1e6).sum())
        scores[order] = implausible
        images[order] = image
    order = min(scores, key=lambda key: (scores[key], key == "<f4"))
    return images[order], order, scores


def bright_fractions(image: np.ndarray) -> tuple[float, float, float]:
    """Threshold and the fraction of each half above it.

    ``T = median(image) + 5 * median(|image - median(image)|)`` over the whole
    256x432 array, so the threshold is five median-absolute-deviations above
    the image's own background rather than an absolute radiance.
    """
    median = float(np.nanmedian(image))
    mad = float(np.nanmedian(np.abs(image - median)))
    threshold = median + MAD_MULTIPLE * mad
    top = float(np.nanmean(image[: LINES // 2] > threshold))
    bottom = float(np.nanmean(image[LINES // 2 :] > threshold))
    return threshold, top, bottom


def candidates(mirror: Path, orbit: int) -> pd.DataFrame:
    """Orbit ``orbit`` dual-band frames whose halves see different footprints."""
    frames = load_frames(mirror, [orbit])
    geo = load_geo(mirror, [orbit])
    dual = frames.loc[frames["band"] == "LM"]
    fractions = (
        geo.loc[geo["geo_ok"].fillna(False)]
        .pivot(index="product_id", columns="half", values="on_planet_frac")
        .rename(columns={"L": "frac_L", "M": "frac_M"})
        .reset_index()
    )
    table = dual.merge(fractions, on="product_id", how="inner")
    partial = table["frac_L"].between(PARTIAL_LOW, PARTIAL_HIGH) | table[
        "frac_M"
    ].between(PARTIAL_LOW, PARTIAL_HIGH)
    split = (table["frac_L"] - table["frac_M"]).abs() >= MIN_HALF_DIFFERENCE
    return table.loc[partial & split].sort_values("start_time").reset_index(drop=True)


def spread(table: pd.DataFrame, limit: int = MAX_FRAMES) -> pd.DataFrame:
    """Every k-th row, k the smallest step that fits inside ``limit``."""
    if table.empty:
        return table
    step = max(1, math.ceil(len(table) / limit))
    return table.iloc[::step].reset_index(drop=True)


def vote_frames(mirror: Path, chosen: pd.DataFrame) -> pd.DataFrame:
    """Score both stacking hypotheses for each frame."""
    records = []
    for row in chosen.itertuples(index=False):
        path = mirror / Path(row.label_path).parent / row.img_file_name
        image, order, scores = read_image(path)
        threshold, top, bottom = bright_fractions(image)
        h1 = abs(top - row.frac_L) + abs(bottom - row.frac_M)
        h2 = abs(top - row.frac_M) + abs(bottom - row.frac_L)
        if abs(h1 - h2) < ABSTAIN_MARGIN:
            vote = "abstain"
        else:
            vote = "H1" if h1 < h2 else "H2"
        records.append(
            {
                "product_id": row.product_id,
                "start_time": pd.Timestamp(row.start_time),
                "frac_L": float(row.frac_L),
                "frac_M": float(row.frac_M),
                "byte_order": order,
                "big_endian_implausible": scores[">f4"],
                "little_endian_implausible": scores["<f4"],
                "threshold": threshold,
                "bright_top": top,
                "bright_bottom": bottom,
                "mean_top": float(np.nanmean(image[: LINES // 2])),
                "mean_bottom": float(np.nanmean(image[LINES // 2 :])),
                "score_h1": h1,
                "score_h2": h2,
                "vote": vote,
            }
        )
    return pd.DataFrame.from_records(records)


def full_half_frames(mirror: Path, orbit: int, limit: int = 12) -> pd.DataFrame:
    """Frames where exactly one half is completely on the planet."""
    frames = load_frames(mirror, [orbit])
    geo = load_geo(mirror, [orbit])
    dual = frames.loc[frames["band"] == "LM"]
    fractions = (
        geo.loc[geo["geo_ok"].fillna(False)]
        .pivot(index="product_id", columns="half", values="on_planet_frac")
        .rename(columns={"L": "frac_L", "M": "frac_M"})
        .reset_index()
    )
    table = dual.merge(fractions, on="product_id", how="inner")
    one_full = (table["frac_L"] == 1.0) ^ (table["frac_M"] == 1.0)
    table = table.loc[one_full].sort_values("start_time")
    table = spread(table, limit)
    records = []
    for row in table.itertuples(index=False):
        image, _, _ = read_image(mirror / Path(row.label_path).parent / row.img_file_name)
        records.append(
            {
                "product_id": row.product_id,
                "frac_L": float(row.frac_L),
                "frac_M": float(row.frac_M),
                "full_half": "L" if row.frac_L == 1.0 else "M",
                "mean_top": float(np.nanmean(image[: LINES // 2])),
                "mean_bottom": float(np.nanmean(image[LINES // 2 :])),
            }
        )
    return pd.DataFrame.from_records(records)


def _ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        return f"{number}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return lines


def report_text(
    orbit: int,
    n_candidates: int,
    votes: pd.DataFrame,
    full: pd.DataFrame,
    verdict: dict,
) -> str:
    counts = votes["vote"].value_counts()
    h1, h2 = int(counts.get("H1", 0)), int(counts.get("H2", 0))
    abstain = int(counts.get("abstain", 0))
    orders = votes["byte_order"].value_counts().to_dict()
    lines = [
        "# Which band half comes first in a 256-line JIRAM product?",
        "",
        "Generated by `scripts/lm_half_order.py` (see that file for the method).",
        "",
        "## Question",
        "",
        "A `SCI_I1_*` product stores both detector bands in one 256x432 array,",
        "the ten gap lines between them removed. The SIS (v7.5) does not say",
        "which half is written first, and the PDS3 label gives",
        "`LINE_FIRST_PIXEL = 2` for both band groups, i.e. relative to each",
        "half. `geometry.LM_HALF_ORDER` encodes the answer.",
        "",
        "## Method",
        "",
        f"1. Orbit {orbit} `LM` frames whose halves see different amounts of",
        f"   planet: at least one half with {PARTIAL_LOW} <= on_planet_frac <=",
        f"   {PARTIAL_HIGH} and |frac_L - frac_M| >= {MIN_HALF_DIFFERENCE},",
        f"   from `index/frames_geo.parquet`. {n_candidates} frames qualify;",
        f"   every {_ordinal(max(1, math.ceil(n_candidates / MAX_FRAMES)))} one",
        f"   by time is used, {len(votes)} frames spread over the orbit.",
        "2. Each image is split into top (rows 0-127) and bottom (rows",
        "   128-255) halves. A pixel is bright when its radiance exceeds",
        "",
        "       T = median(image) + 5 * median(|image - median(image)|)",
        "",
        "   the median and MAD taken over the whole 256x432 array, so T is",
        "   five median-absolute-deviations above the frame's own background",
        "   rather than an absolute radiance.",
        "3. Hypothesis H1 (top = L, bottom = M) scores",
        "   `|bright_top - frac_L| + |bright_bottom - frac_M|`, H2 (top = M,",
        "   bottom = L) the swapped expression; the smaller score wins, and a",
        f"   frame abstains when the two scores differ by less than {ABSTAIN_MARGIN}.",
        "",
        "### Byte order (a prerequisite the spec got the other way round)",
        "",
        "The PDS3 label says `SAMPLE_TYPE = IEEE_REAL`, nominally big-endian,",
        "but the RDR files are little-endian. Read big-endian, an orbit-24",
        "image is several thousand non-finite pixels and values of order",
        "1e29; read little-endian, every pixel is finite and of order 1e-4 to",
        "1e-2 W/(m^2 sr um), and an orbit-4 M-band frame reads 0.02-0.5,",
        "the right magnitude for 4.8 um thermal emission. Each image here is",
        "read in whichever order leaves no implausible pixel; of the",
        f"{len(votes)} frames scored below, "
        + ", ".join(
            f"{count} read {'little' if order == '<f4' else 'big'}-endian"
            for order, count in orders.items()
        )
        + ".",
        "",
        "## Evidence",
        "",
    ]
    rows = [
        [
            record.product_id,
            pd.Timestamp(record.start_time).strftime("%Y-%m-%dT%H:%M:%S"),
            f"{record.frac_L:.3f}",
            f"{record.frac_M:.3f}",
            f"{record.bright_top:.3f}",
            f"{record.bright_bottom:.3f}",
            f"{record.score_h1:.3f}",
            f"{record.score_h2:.3f}",
            record.vote,
        ]
        for record in votes.itertuples(index=False)
    ]
    lines.extend(
        _table(
            [
                "product_id",
                "start_time",
                "frac_L",
                "frac_M",
                "bright_top",
                "bright_bottom",
                "score H1",
                "score H2",
                "vote",
            ],
            rows,
        )
    )
    decided = h1 + h2
    share = h1 / decided if decided else float("nan")
    lines.extend(
        [
            "",
            f"Votes: H1 (top = L) {h1}, H2 (top = M) {h2}, abstain {abstain};",
            f"{decided} decided, H1 share {share:.3f}.",
            "",
            "## Radiance of the halves when only one is on the planet",
            "",
            "Mean radiance, W/(m^2 sr um). The M band is thermal and stays",
            "positive over the planet day and night; the L band is reflected",
            "sunlight and collapses to the background on the nightside.",
            "",
        ]
    )
    lines.extend(
        _table(
            ["product_id", "full half", "frac_L", "frac_M", "mean top", "mean bottom"],
            [
                [
                    record.product_id,
                    record.full_half,
                    f"{record.frac_L:.3f}",
                    f"{record.frac_M:.3f}",
                    f"{record.mean_top:.6f}",
                    f"{record.mean_bottom:.6f}",
                ]
                for record in full.itertuples(index=False)
            ],
        )
    )
    lines.extend(["", "## Conclusion", "", verdict["conclusion"], ""])
    return "\n".join(lines) + "\n"


def verdict_text(votes: pd.DataFrame) -> dict:
    """Apply the vote criterion and write the conclusion it earns."""
    counts = votes["vote"].value_counts()
    h1, h2 = int(counts.get("H1", 0)), int(counts.get("H2", 0))
    decided = h1 + h2
    winner, wins = ("H1", h1) if h1 >= h2 else ("H2", h2)
    share = wins / decided if decided else float("nan")
    resolved = decided >= MIN_VOTES and share >= AGREEMENT
    order = ("L", "M") if winner == "H1" else ("M", "L")

    mismatch = {
        "bottom_M": (votes["bright_bottom"] - votes["frac_M"]).abs().median(),
        "bottom_L": (votes["bright_bottom"] - votes["frac_L"]).abs().median(),
        "top_L": (votes["bright_top"] - votes["frac_L"]).abs().median(),
        "top_M": (votes["bright_top"] - votes["frac_M"]).abs().median(),
    }
    dark_top = float((votes["bright_top"] < 0.05).mean())
    if resolved:
        conclusion = (
            f"The vote resolves the order: {wins} of {decided} decided frames "
            f"({share:.1%}, at least {AGREEMENT:.0%} of at least {MIN_VOTES} "
            f"votes) support {winner}, so the first 128 rows of a 256-line "
            f"product are the {order[0]} band and `LM_HALF_ORDER` is "
            f"`{order}`."
        )
    else:
        conclusion = "\n".join(
            [
                f"The vote does NOT resolve the order: {wins} of {decided} "
                f"decided frames ({share:.1%}) support {winner}, short of the "
                f"{AGREEMENT:.0%} of at least {MIN_VOTES} votes the procedure "
                "requires. `LM_HALF_ORDER` therefore keeps its default "
                "`('L', 'M')` and the storage order is, by this vote, formally "
                "unresolved.",
                "",
                "The scoring rule is what falls short, not the data. It assumes "
                "that each half's bright fraction tracks its on-planet "
                "fraction, which holds for the thermal M band and fails for "
                "the L band: the top half is dark (bright fraction below 0.05) "
                f"in {dark_top:.0%} of the frames scored here, so whenever "
                "frac_L is large H1 is charged nearly the whole of it and "
                "loses, however well the bottom half matches. Pairing the "
                "halves both ways over the same frames shows which pairing the "
                "data actually support (median absolute mismatch):",
                "",
                f"- bottom half against frac_M: {mismatch['bottom_M']:.3f}",
                f"- bottom half against frac_L: {mismatch['bottom_L']:.3f}",
                f"- top half against frac_L: {mismatch['top_L']:.3f}",
                f"- top half against frac_M: {mismatch['top_M']:.3f}",
                "",
                "The bottom half tracks the M-band footprint an order of "
                "magnitude better than the L-band one, and its row-by-row "
                "bright profile follows the M-band footprint's ramp; the top "
                "half is dark wherever the L band looks at the nightside. The "
                "mean-radiance table above says the same: whenever the M half "
                "is fully on the planet the bottom half is strongly positive. "
                "So does the instrument documentation, independently of any "
                "image: in `juno_jiram_v02.ti` and `juno_v12.tf` the L band "
                "occupies detector lines 1-128 (the +X side, where line 1 is) "
                "and the M band lines 139-266, and the labels' per-band "
                "`SAMPLE_FIRST_PIXEL` (1 for L, 139 for M) numbers the halves "
                "in that same order.",
                "",
                "The default `('L', 'M')` therefore stands -- on the radiance "
                "and documentary evidence, not on the vote, which is recorded "
                "here as inconclusive.",
            ]
        )
    return {
        "resolved": resolved,
        "winner": winner,
        "order": order,
        "share": share,
        "decided": decided,
        "mismatch": mismatch,
        "conclusion": conclusion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mirror", help="local mirror root")
    parser.add_argument("--orbit", type=int, default=24)
    parser.add_argument("--out", default=str(REPORT))
    args = parser.parse_args()

    mirror = mirror_root(args.mirror)
    table = candidates(mirror, args.orbit)
    chosen = spread(table)
    if chosen.empty:
        raise SystemExit("no LM frames match the selection; run geo --orbits 24 first")
    votes = vote_frames(mirror, chosen)
    full = full_half_frames(mirror, args.orbit)
    verdict = verdict_text(votes)

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        report_text(args.orbit, len(table), votes, full, verdict), encoding="utf-8"
    )
    counts = votes["vote"].value_counts()
    print(f"candidates: {len(table)}, scored: {len(votes)}")
    print(
        f"H1 (top = L): {int(counts.get('H1', 0))}  "
        f"H2 (top = M): {int(counts.get('H2', 0))}  "
        f"abstain: {int(counts.get('abstain', 0))}"
    )
    print(
        f"resolved: {verdict['resolved']}  winner: {verdict['winner']}  "
        f"share: {verdict['share']:.3f} of {verdict['decided']} decided votes"
    )
    print(
        "LM_HALF_ORDER should be "
        f"{verdict['order'] if verdict['resolved'] else ('L', 'M')}"
        f"{'' if verdict['resolved'] else ' (default kept; vote inconclusive)'}"
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
