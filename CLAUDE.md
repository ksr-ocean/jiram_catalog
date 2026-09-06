# jiram_catalog — project instructions

Catalog and region-extraction tool for the full Juno JIRAM archive
(PDS4 bundle `juno_jiram_bundle`, Atmospheres node). Design record:
`docs/brainstorm_2026-09-03.md`; research reports in `docs/reports/`.

## Layout and conventions
- `uv` project, Python 3.12, src layout (`src/jiram_catalog/`). Torch-free.
  Run tests with `uv run pytest`.
- Data mirror (labels, inventory, SPICE kernels, later images):
  `/expanse/lustre/projects/cla119/kaushiks/jiram_mirror/` (= `~/projects/jiram_mirror`).
- Ground truth for validation, READ-ONLY, never modified by any agent:
  `/expanse/lustre/projects/cla119/kaushiks/JIRAM/` (Ingersoll 2022 PJ4
  north-pole mosaics `n01..n04_15km/*.map`, TRACKER4 vectors `*.tp4`).
- `~/scratch/goflow_rnd/` is the downstream consumer; READ-ONLY here. Its
  input contract: 3 consecutive frames, constant `dt_img_s`, x:(3,H,W),
  y:(2,H,W), mask:(1,H,W), float32, regular square grid.
- Network: atmos.nmsu.edu works with the system CA bundle;
  naif.jpl.nasa.gov requires the `certifi` bundle (system bundle lacks
  Sectigo Root R46). Use `requests`/`httpx` (certifi by default) or
  `curl --cacert $(python -c 'import certifi;print(certifi.where())')`.
- `vendor/` holds cloned third-party repos for evaluation; not ours.

## Orchestration overlay (see docs/agent_harness.md)
- The lead plans, writes each spec and its read-only gate before
  dispatch, verifies every delivery itself (scope check against the
  spec's file list, gates run by the lead, self-reports treated as
  claims), and keeps every judgment call.
- Mechanical code from an airtight spec goes to the cheapest executor
  that has passed a gate on this repo; bounded numerical or geometric
  work and fresh-context review go to a stronger executor; literature
  review and reader-facing prose always go to a Sonnet-class writer.
- Never delegate: choice of latitude convention and map projection per
  region; acceptance of the geometry engine against the PJ4 ground
  truth; the decision to mirror image data at scale; final
  interpretation of velocity fields or statistics.
- Executors never edit `cli.py`, `pyproject.toml`, `uv.lock`, gates,
  fixtures, or `docs/specs`; new subcommands are exposed through
  `add_subparser(subparsers)` / `run(args)` in their own module and
  wired by the lead. Every spec ends with the verbatim
  stop-on-ambiguity clause and a judgment-call listing requirement.
