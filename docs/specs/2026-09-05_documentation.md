# Spec: reader-facing documentation pass

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog.
Audience: collaborators (planetary and ocean scientists) and agents
who will extend the repository. Register: clear, pedagogical, concrete;
every claim checked against the code, the specs, or the reports. No
executor product names anywhere (the repository is shared).

## Files in scope (create or rewrite)
- `README.md` (rewrite: what the tool is, what exists, quickstart,
  configuration, where to read next)
- `docs/README.md` (index of all documentation with one line each)
- `docs/architecture.md` (layers as built: archive mirror, label index,
  SPICE geometry engine, geometry table, reprojection, regions and
  stacks, strip library, statistics, exports, GUI; module map; how a
  frame flows from archive to product; the two regimes)
- `docs/data_products.md` (schema of every table and file the tool
  writes: `manifest.parquet`, `frames.parquet`, `frames_geo.parquet`,
  `trackability_*.parquet`, region stack NetCDF, sequence composite,
  goflow realization layout, strip NetCDF and `strips.parquet`,
  strip statistics NetCDF, GUI cache; units, conventions, NaN and mask
  semantics; read them from the code and, where the mirror has them,
  from the actual files)
- `docs/usage.md` (command reference generated from `--help` of every
  subcommand, plus worked examples: index a new orbit, build a polar
  stack and movie, build strips for a latitude band, compute
  statistics, export for the velocity model, run the GUI; environment
  variables `JIRAM_MIRROR`, `JIRAM_PAPER_DATA`, `JIRAM_SKIP_GATES`)
- `docs/decisions.md` (decision log: one entry per settled choice with
  date, decision, evidence, and where it is enforced in code; sources:
  `docs/build_log_2026-09-04.md`, the specs, the reports, and
  `CLAUDE.md`. Must include: cluster-only v1; camera frames only;
  planetocentric east-positive longitudes; geometry epoch = label
  START_TIME; mirror-blind SPICE geometry; inverse aberration on rays
  and observer vector; little-endian image data; L top / M bottom in
  dual-band products; sequence definition and the missing frame 1;
  resolution classes; the paper grid being orthographic; strip
  chunking rule; statistics conventions and the departure on shell
  factors; executor-neutral shared docs)
- `docs/open_items.md` (kernel gaps for orbits 38/55/70 and the
  gap-filler kernels; the L-band 0.024 deg residual; eight failed
  label downloads; JPL VICAR documentation offline; mask leakage in
  spectra; GUI features deferred: in-app builds, population
  statistics; anything else the build log marks open)
READ-ONLY: everything else. Do not change code. Do not touch
`docs/specs/`, `docs/reports/`, `docs/build_log_*.md`,
`docs/gui_design.md`, `docs/agent_harness.md`, `AGENTS.md`,
`CONTRIBUTING.md`, `CLAUDE.md`.

## Method
Read `CLAUDE.md`, `AGENTS.md`, `docs/agent_harness.md`, the build log,
every spec and report, and the source. Run `uv run jiram-catalog
<subcommand> --help` for each subcommand and paste the usage lines.
Open real product files with xarray/pandas to confirm schemas
(`JIRAM_MIRROR` default in `src/jiram_catalog/config.py` if it exists,
else `pds.DEFAULT_MIRROR`). Where a fact cannot be verified, say so
in the text rather than guess. Keep each document under about 300
lines; use tables for schemas; link between documents with relative
paths.

## Validation that defines done
- `grep -rci codex README.md docs/*.md` prints only zeros.
- Every subcommand in `uv run jiram-catalog --help` appears in
  `docs/usage.md`.
- Every column name in `docs/data_products.md` for `frames_geo.parquet`,
  `strips.parquet`, and `trackability_frames.parquet` exists in the
  actual file (check with pandas and list any mismatch in your report).
- `JIRAM_SKIP_GATES=1 uv run pytest -q` still passes (you changed no code).

## Report (at most 30 lines)
Files written with line counts; the schema checks; anything you could
not verify; judgment calls.
