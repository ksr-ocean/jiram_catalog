# Spec: JunoCam acquisition, index, and quality table (JunoCam milestone 1)

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog.
Read first: `docs/reports/junocam_archive_recon.md` (measured facts:
archive location, volume layout, naming, framelet stacking, label
keywords with quoted values, INDEX.TAB columns, ERRATA content),
`docs/reports/junocam_navigation_quality.md` (damage timeline,
throughput loss, companding), then the JIRAM acquisition modules to
mirror their conventions: `src/jiram_catalog/pds.py`, `mirror.py`,
`labels.py`, `index.py`, `config.py`, and `cli.py` (subcommand
registration pattern in `docs/agent_harness.md`).

## Goal
A `junocam` subcommand group that builds a manifest of every JunoCam
product on the PDS Imaging node from the volumes' `INDEX.TAB` files,
mirrors labels for all products and images for selected orbits and
levels, parses labels into `junocam_images.parquet`, and computes a
per-image quality record (documented epoch flags plus measured image
metrics) so that every downstream step can filter by a quality tier.

## Files in scope (create)
- `src/jiram_catalog/junocam/__init__.py`, `pds.py`, `mirror.py`,
  `labels.py`, `index.py`, `quality.py`, `cli.py` (exposes
  `add_subparser(subparsers)` registering a `junocam` parser with
  sub-subcommands `manifest`, `mirror`, `index`, `quality`, and
  `run(args)`; plus a `__main__` for stand-alone use)
- `tests/test_junocam_offline.py`
READ-ONLY: everything else, including the top-level `cli.py`
(the lead wires `junocam.cli.add_subparser`), `pyproject.toml`,
`uv.lock` (no new dependencies; `pvl`, `pandas`, `pyarrow`, `numpy`,
`requests`/`certifi` are present), gates. Under the mirror root write
only under `<mirror>/junocam/`. Never write under the paper-data
directory or `~/scratch`.

## Facts (from the reconnaissance; verify against the samples in `<mirror>/junocam_samples/`)
- Root `https://planetarydata.jpl.nasa.gov/img/data/juno/` with
  volumes `JNOJNC_0001` … `JNOJNC_0035`; each volume has
  `INDEX/INDEX.TAB` (+ `INDEX.LBL` describing 16 columns including
  `PRODUCT_ID`, `START_TIME`, `STOP_TIME`, `PROCESSING_LEVEL_ID`, and a
  file specification column; read `INDEX.LBL` to get column names and
  positions, do not hard-code), `ERRATA.TXT`, `AAREADME.TXT`, and data
  under `DATA/` organised by orbit and day-of-year. TLS: the host fails
  the system CA bundle and works with `certifi` (use `requests` with
  default verification, or `wget --ca-certificate=<certifi.where()>`).
- Product naming `JNC<T>_<YYYYDDD>_<OO><F><NNNNN>_V<XX>.<IMG|LBL>` where
  `T` = `E` (EDR, 8-bit square-root companded) or `R` (RDR, 16-bit
  linear, decompanded, scaled so a white target is 10,000 DN, no flat
  field), `OO` = orbit, `F` = a flag letter, `NNNNN` = sequence.
- Image files: `RECORD_BYTES = 1648`, `LINES = 128 * n_framelets`,
  framelets stacked in time order, bands cycling within each frame in
  `FILTER_NAME` order; label keywords (quoted values for the 2017
  sample `JNCE_2017033_04C00105_V01`): `START_TIME =
  2017-02-02T13:05:34.387`, `IMAGE_TIME` (same),
  `SPACECRAFT_CLOCK_START_COUNT = "539312928:137"`, `ORBIT_NUMBER = 4`,
  `INTERFRAME_DELAY = 0.375 <s>`, `EXPOSURE_DURATION = 9.600000 <ms>`,
  `JNO:TDI_STAGES_COUNT = 3`, `FILTER_NAME = ('BLUE','GREEN','RED')`,
  `LINES = 15360`, `LINE_SAMPLES = 1648`, `SAMPLE_BITS = 8`,
  `SAMPLE_BIT_MODE_ID = "SQROOT"`; the 2024 sample
  `JNCE_2024034_58C00012_V01` has `EXPOSURE_DURATION = 6.400000 <ms>`,
  `JNO:TDI_STAGES_COUNT = 2`, `LINES = 3840`. Read every keyword the
  recon report lists; store all of them.
- ERRATA: radiation damage to a voltage regulator from perijove 47,
  to the CCD by perijove 56, annealing campaigns, imaging recovered by
  perijove 57; throughput loss through orbit 60 of red 23%, green 47%,
  blue 64%, uncorrected in RDR; blooming into the methane band;
  per-image timing offsets. Read the ERRATA of the latest volumes and
  encode what they state as data (a small YAML `configs/junocam_quality.yaml`
  you create: epoch boundaries by perijove, throughput factors by band
  with the orbit range they apply to, and a free-text note per entry
  with the source volume).

## Products
- `<mirror>/junocam/manifest/manifest.parquet`: one row per product
  file from all volumes' `INDEX.TAB`: `volume`, `product_id`, `level`
  (`EDR`/`RDR`), `orbit` (parsed from the product id; also from the
  path; store both, flag disagreement), `doy_dir`, `start_time`,
  `stop_time`, `url_img`, `url_lbl`, `file_spec`, `index_row_json`
  (the raw row for anything else). Raw `INDEX.TAB`/`INDEX.LBL`/`ERRATA`
  cached under `<mirror>/junocam/manifest/<volume>/`.
- `<mirror>/junocam/<volume>/<path as in archive>` for mirrored files.
- `<mirror>/junocam/index/junocam_images.parquet`: one row per label
  mirrored: identity (product_id, level, volume, orbit, path), all
  timing keywords (`start_time` datetime64[ns], `sclk_start`,
  `interframe_delay_s`, `exposure_ms`, `tdi_stages`), `filters`
  (`;`-joined in stacking order), `n_bands`, `n_framelets` (=
  `LINES/128/n_bands`, as integers with a flag if not divisible),
  `lines`, `samples`, `sample_bits`, `companded` (bool), `record_bytes`,
  `file_records`, `img_size_bytes`, `img_present`, `md5` if present,
  `target`, plus `parse_ok`/`parse_error` and every other keyword under
  a `label_json` string column.
- `<mirror>/junocam/index/junocam_quality.parquet`: one row per RDR
  image mirrored: `quality_epoch` (`nominal` before the first damage
  perijove, `regulator_damage`, `ccd_damage`, `post_anneal`, from the
  YAML), `throughput_factor_red/green/blue` (1.0 outside documented
  ranges), and measured metrics on the image: per framelet, using the
  first 16 and last 16 samples of each line as a "dark" reference when
  they are off the planet is NOT reliable, so instead: (a)
  `streak_index` = variance of framelet line means divided by total
  variance (the JIRAM L-band diagnostic), (b) `noise_mad` = median
  absolute deviation of the high-pass residual (image minus a 5x5
  median filter, computed with numpy strides, no scipy) over pixels
  below the 20th percentile of brightness, (c) `saturation_frac` =
  fraction of pixels at the maximum code, (d) `zero_frac` = fraction at
  0, (e) `bloom_flag` = True when a methane framelet's line means
  exceed 3x their median in more than 5% of lines; aggregated per band
  as medians over framelets; and `quality_tier`: `A` if
  `quality_epoch in (nominal, post_anneal)` and `streak_index < 0.3`
  and `saturation_frac < 0.02`; `B` if epoch nominal/post_anneal but a
  metric fails; `C` if epoch is a damage epoch. Document the rule in the
  module docstring; thresholds as module constants.

## CLI (`jiram-catalog junocam <sub>`)
- `manifest [--volumes 1-35] [--refresh]`
- `mirror --orbits SPEC [--level RDR|EDR|both] [--kinds labels,data] [--doy DDD,...] [--jobs N]`
  (labels for every product of the selected orbits; data only for
  `--level`; `--doy` restricts data to given days; completeness by
  size `RECORD_BYTES*FILE_RECORDS` for `.IMG`, `END` line for `.LBL`)
- `index --orbits SPEC [--jobs N]`
- `quality --orbits SPEC [--jobs N]`
Use `wget` as the JIRAM mirror does, with `--ca-certificate` set to
the certifi bundle; at most 4 concurrent; idempotent.

## Offline tests (`tests/test_junocam_offline.py`)
Product-id parsing; INDEX.LBL column parsing on a synthetic label;
label parsing on the two sample labels in `<mirror>/junocam_samples/`
(exact values above); framelet count arithmetic; quality metrics on
synthetic framelet stacks (a striped stack has `streak_index > 0.9`,
a flat noisy one `< 0.1`; saturation and zero fractions; bloom flag);
tier rule on synthetic rows; YAML epoch lookup.

## Validation that defines done
```
JIRAM_SKIP_GATES=1 uv run pytest -q tests/test_junocam_offline.py
uv run python -m jiram_catalog.junocam.cli manifest --volumes 1-35
uv run python -m jiram_catalog.junocam.cli mirror --orbits 4 --level RDR --kinds labels,data --doy 033 --jobs 3
uv run python -m jiram_catalog.junocam.cli mirror --orbits 58 --level RDR --kinds labels --jobs 3
uv run python -m jiram_catalog.junocam.cli index --orbits 4,58
uv run python -m jiram_catalog.junocam.cli quality --orbits 4
uv run pytest -q tests/test_junocam_offline.py tests/test_gate_junocam_index.py
```
READ-ONLY gate `tests/test_gate_junocam_index.py`: manifest covers
volumes 1-35 with orbit 4 EDR count 1021; the index has the two sample
products with the quoted keyword values; every RDR image of orbit 4
day 033 is mirrored and complete; the quality table has a row per
mirrored orbit-4 RDR with finite metrics and `quality_epoch ==
"nominal"`; the 2024 product's epoch is `post_anneal`.

## Report (at most 35 lines)
Counts (manifest rows by level, per-orbit totals for 4 and 58, files
and bytes mirrored, wall times); the quality metrics' distribution for
orbit 4; judgment calls; ambiguities with the choice made.
