# Spec: trackability report (which passes and latitude bands support velocity retrieval)

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog
(`uv` project; package `jiram_catalog`). Read first: `src/jiram_catalog/geo.py`
(`frames_with_geo(mirror, orbits=None)` merges the frame index with the
SPICE geometry table; one row per frame and band half), `docs/reports/geo_report.md`,
and `tests/fixtures/pj4_ingersoll2022_map_labels.csv`.

## Goal
For every pass (orbit) and latitude band, quantify whether the archive
provides repeated views of the same area within minutes, and whether the
expected cloud displacement over those baselines is resolvable at the
frames' pixel size. Output: a report, two Parquet tables, one figure.

## Files in scope
- `src/jiram_catalog/trackability.py`   (create; pure numpy/pandas)
- `scripts/trackability_report.py`       (create)
- `docs/reports/trackability.md`, `docs/reports/figures/trackability_heatmap.png` (created by the script)
- `tests/test_trackability_offline.py`   (create)
READ-ONLY: everything else. Do not edit `cli.py`, `pyproject.toml`,
`uv.lock`. Under the mirror root write only `index/trackability_pairs.parquet`
and `index/trackability_frames.parquet`. Never write under
`/expanse/lustre/projects/cla119/kaushiks/JIRAM` or `~/scratch`.

## Definitions (normative)
- Mirror root `/expanse/lustre/projects/cla119/kaushiks/jiram_mirror`
  (env `JIRAM_MIRROR` overrides).
- Unit of analysis: rows of `frames_with_geo` with `geo_ok`,
  `on_planet_frac >= 0.3`, `bore_emission <= 70`. Columns used:
  `product_id`, `orbit_dir`, `half`, `seq_id`, `start_time`, `bore_lat`,
  `bore_lon_east`, `median_pixel_km`, `on_planet_frac`, `bore_emission`,
  `dayside_frac`.
- Latitude bands by `bore_lat`: S polar [-90,-60), S mid [-60,-30),
  S low [-30,-10), equator [-10,10), N low [10,30), N mid [30,60),
  N polar [60,90].
- Candidate pair: two rows with the same `orbit_dir` and `half`,
  different `seq_id`, `90 s <= dt <= 6 h` (`dt` = |difference of
  `start_time`|), and boresight great-circle separation (on a sphere of
  radius 69,911 km) `<= 0.5 * 432 * min(median_pixel_km of the two)`.
  This is an overlap proxy, not an exact footprint intersection; say so
  in the report.
- Expected displacement at reference speed `U` (m/s):
  `d_px = U * dt / (1000 * max(median_pixel_km of the two))`. A pair is
  trackable at `U` when `0.5 <= d_px <= 40`. Report `U` = 10, 30, 100.
- Per frame: `has_partner` (any candidate pair), `best_dt_s` (REVISED
  2026-09-05: the smallest `dt` among partners trackable at U=30,
  i.e. with `0.5 <= d_px_30 <= 40`; NaN when none), `n_partners`,
  `trackable_10/30/100` (bool).
- Sequence pairs: for each ordered pair of sequences (A earlier than B,
  same orbit and half) with at least 3 candidate frame pairs, record
  `dt_start_s` (difference of first frames), `n_pairs`, `frac_A`
  (fraction of A's frames with a partner in B), and the median `d_px`
  at U=30.

## Outputs
- `index/trackability_pairs.parquet`: one row per candidate pair with
  both product_ids, orbit, half, both seq_ids, `dt_s`, `sep_km`,
  `pixel_km`, `d_px_10`, `d_px_30`, `d_px_100`.
- `index/trackability_frames.parquet`: one row per unit-of-analysis
  frame with the per-frame columns above plus `lat_band`.
- `docs/reports/trackability.md`: (1) method and the proxy caveat; (2)
  table per latitude band: frames, with partner, trackable at 10/30/100
  m/s, median best dt, median pixel km; (3) table per orbit: frames,
  trackable-30 count by band (7 columns); (4) top 10 passes per band by
  trackable-30 count; (5) the sequence-pair table restricted to pairs
  with `frac_A >= 0.5`, sorted by orbit and dt (this is the list of
  deliberate repeat sequences), with a count per orbit; (6) totals.
  Keep tables as Markdown; no prose beyond captions and the method.
- `docs/reports/figures/trackability_heatmap.png`: orbit (x) by
  latitude band (y) heatmap of trackable-30 frame counts, log colour
  scale, matplotlib, dpi 150.

## Offline tests (`tests/test_trackability_offline.py`)
Synthetic `frames_with_geo`-like DataFrame (10 frames, 3 sequences, two
orbits): pairing rules (dt window, separation threshold, same
orbit/half, different seq), `d_px` arithmetic, `best_dt_s` choice,
sequence-pair aggregation, latitude binning edges.

## Validation that defines done
```
JIRAM_SKIP_GATES=1 uv run pytest -q tests/test_trackability_offline.py
uv run python scripts/trackability_report.py
uv run pytest -q tests/test_trackability_offline.py tests/test_gate_trackability.py
```
READ-ONLY gate `tests/test_gate_trackability.py`: the 48 paper frames
(orbit 4, half M) each have a partner; their `best_dt_s` values are
within 60 s of 487 or 974 s; the pairs table contains pairs between the
paper's first and third sequences with dt within 30 s of 974 s; counts
are consistent (trackable <= with partner <= frames per band); the
heatmap exists.

## Report (at most 35 lines)
Commands; the per-band table; the number of deliberate repeat-sequence
pairs found and the three orbits with most; judgment calls; ambiguities
with the choice made.

If any part of this spec is ambiguous or underdetermined, do NOT choose an
interpretation silently: implement everything else, and list the
ambiguity with the options and your choice at the end of your report.
Also list every judgment call you made, however minor.
