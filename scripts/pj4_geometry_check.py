#!/usr/bin/env python3
"""
Mirror-blind SPICE geometry check for JIRAM M-band frames vs. PJ4 map-label
ground truth (Ingersoll 2022 north-pole mosaic VICAR labels).

For each row of the fixture CSV, at ET(IMAGE_TIME), computes:
  1. sub-spacecraft NEAR POINT/ELLIPSOID altitude
  2. JUNO_JIRAM_I_MBAND boresight sincpt geometry: planetocentric latitude,
     east longitude, emission, incidence, phase, range
  3. the four M-band FOV corner intercepts (planetocentric lat, east lon;
     NaN if a corner ray misses the ellipsoid)
  4. the same boresight intercept re-evaluated at
     IMAGE_TIME + EXPOSURE_DURATION/2 (mid-exposure), to test whether the
     label's IMAGE_TIME is start-of-exposure or mid-exposure

This deliberately does NOT apply the JIRAM de-spinning mirror's per-frame
NADIR_OFFSET angle -- juno_v12.tf (FK) states its JIRAM frames "do not
attempt to account for the de-spinning mirror motion." The point of this
script is to measure how far that mirror-blind static-boresight assumption
departs from the officially archived geometry, frame by frame.

Usage:
    uv run --no-project --with spiceypy,numpy,certifi python pj4_geometry_check.py \\
        --fixture <csv> --metakernel <tm> --out <csv>
"""
import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np
import spiceypy as spice

REPO = Path(__file__).resolve().parents[1]
# ``--no-project`` runs this outside the uv environment, so jiram_catalog is
# not installed; reach its (dependency-free) config module straight from the
# checkout instead of hardcoding the mirror path here.
sys.path.insert(0, str(REPO / "src"))
from jiram_catalog.config import mirror_root  # noqa: E402

DEFAULT_FIXTURE = str(REPO / "tests" / "fixtures" / "pj4_ingersoll2022_map_labels.csv")
DEFAULT_METAKERNEL = str(mirror_root() / "spice" / "pj4_smoketest.tm")
DEFAULT_OUT = str(REPO / "docs" / "samples" / "pj4_geometry_check.csv")

MBAND_FRAME = "JUNO_JIRAM_I_MBAND"
TARGET = "JUPITER"
TFRAME = "IAU_JUPITER"
OBSERVER = "JUNO"
ABCORR = "LT+S"

NAN6 = (math.nan,) * 6


def east_lon_deg(lon_rad):
    """spice.reclat longitude (radians, east-positive) -> degrees in [0, 360)."""
    return math.degrees(lon_rad) % 360.0


def sub_sc_altitude(et):
    _, _, srfvec = spice.subpnt("NEAR POINT/ELLIPSOID", TARGET, et, TFRAME, ABCORR, OBSERVER)
    return float(np.linalg.norm(srfvec))


def boresight_intercept(et, boresight, frame):
    """(lat_pc_deg, east_lon_deg, emission_deg, incidence_deg, phase_deg, range_km),
    or all-NaN if the ray misses the ellipsoid."""
    try:
        spoint, _, srfvec = spice.sincpt(
            "ELLIPSOID", TARGET, et, TFRAME, ABCORR, OBSERVER, frame, boresight
        )
    except spice.stypes.SpiceyError:
        return NAN6
    _, lon, lat = spice.reclat(spoint)
    try:
        _, _, phase, incdnc, emissn, _, _ = spice.illumf(
            "ELLIPSOID", TARGET, "SUN", et, TFRAME, ABCORR, OBSERVER, spoint
        )
    except spice.stypes.SpiceyError:
        return NAN6
    rng = float(np.linalg.norm(srfvec))
    return (
        math.degrees(lat),
        east_lon_deg(lon),
        math.degrees(emissn),
        math.degrees(incdnc),
        math.degrees(phase),
        rng,
    )


def corner_intercepts(et, corners, frame):
    out = []
    for c in corners:
        try:
            spoint, _, _ = spice.sincpt(
                "ELLIPSOID", TARGET, et, TFRAME, ABCORR, OBSERVER, frame, c
            )
            _, lon, lat = spice.reclat(spoint)
            out.append((math.degrees(lat), east_lon_deg(lon)))
        except spice.stypes.SpiceyError:
            out.append((math.nan, math.nan))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fixture", default=DEFAULT_FIXTURE)
    ap.add_argument("--metakernel", default=DEFAULT_METAKERNEL)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()

    spice.furnsh(args.metakernel)

    mband_id = spice.bodn2c(MBAND_FRAME)
    shape, frame, boresight, n, bounds = spice.getfov(mband_id, 4)
    assert frame == MBAND_FRAME, f"unexpected frame {frame}"

    rows_out = []
    with open(args.fixture, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            et = spice.utc2et(row["IMAGE_TIME"])

            alt_spice = sub_sc_altitude(et)
            lat, lon, em, inc, ph, rng = boresight_intercept(et, boresight, frame)
            corners = corner_intercepts(et, bounds, frame)

            exp = float(row["EXPOSURE_DURATION"])
            et_mid = et + exp / 2.0
            lat_mid, lon_mid, em_mid, _inc_mid, _ph_mid, _rng_mid = boresight_intercept(
                et_mid, boresight, frame
            )

            rows_out.append(
                {
                    "file": row["file"],
                    "PRODUCT_ID": row["PRODUCT_ID"],
                    "alt_spice": alt_spice,
                    "alt_label": float(row["SPACECRAFT_ALTITUDE"]),
                    "cenlat_spice": lat,
                    "cenlat_label": float(row["CENLAT"]),
                    "cenlon_spice": lon,
                    "cenlon_label": float(row["CENLON"]),
                    "em_spice": em,
                    "em_label": float(row["EM_ANGLE"]),
                    "in_spice": inc,
                    "in_label": float(row["IN_ANGLE"]),
                    "range_km": rng,
                    "c1_lat": corners[0][0],
                    "c1_lon": corners[0][1],
                    "c2_lat": corners[1][0],
                    "c2_lon": corners[1][1],
                    "c3_lat": corners[2][0],
                    "c3_lon": corners[2][1],
                    "c4_lat": corners[3][0],
                    "c4_lon": corners[3][1],
                    "cenlat_mid": lat_mid,
                    "cenlon_mid": lon_mid,
                    "em_mid": em_mid,
                }
            )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows_out[0].keys())
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    print(f"Wrote {len(rows_out)} rows to {out_path}")

    spice.kclear()


if __name__ == "__main__":
    main()
