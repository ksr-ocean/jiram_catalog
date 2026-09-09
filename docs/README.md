# Documentation index

Start with the top-level `README.md`, then this repository's own
`AGENTS.md` if you are going to change anything. Everything below is
one line each; follow the links for the rest.

## Read first

- [Ingersoll 2022 methods slide deck](pedagogy/ingersoll_2022_tracking_slides.pdf)
  ([editable TeX](pedagogy/ingersoll_2022_tracking_slides.tex)) -- visual lessons
  on cloud tracking, velocity uncertainty, vorticity/divergence and proposed
  workflows. [Companion notes](pedagogy/ingersoll_2022_tracking.pdf)
  ([TeX](pedagogy/ingersoll_2022_tracking.tex)) provide the full derivations.
- [`../PEDAGOGICAL_REVIEW.md`](../PEDAGOGICAL_REVIEW.md) -- teaching tour of
  JIRAM/JunoCam implementation, scientific workflows and validation, with
  [companion slides](pedagogy/slides.pdf).
- [`research_workflow.md`](research_workflow.md) -- current five-view workspace,
  JunoCam exclusion policy, capability matrix and scientific interpretation.
- [`../README.md`](../README.md) -- what the tool is, quickstart,
  where to read next.
- [`architecture.md`](architecture.md) -- the layers as built, the
  module map, how a frame flows from archive to product, the two
  regimes (region stacks vs. the strip library).
- [`data_products.md`](data_products.md) -- the schema of every table
  and file the tool writes, with units and NaN/mask conventions.
- [`usage.md`](usage.md) -- every subcommand's `--help`, plus worked
  examples and the environment variables.
- [`configuration.md`](configuration.md) -- how the mirror and paper-data
  paths are resolved (`JIRAM_MIRROR`, `JIRAM_PAPER_DATA`, the optional
  TOML file, `jiram-catalog config`).

## Decisions and gaps

- [`reports/junocam_expansion_2026-09-08.md`](reports/junocam_expansion_2026-09-08.md)
  -- measured multi-pass acquisition, eligibility, mapped coverage and limits.
- [`reports/junocam_expansion_evidence_2026-09-08.md`](reports/junocam_expansion_evidence_2026-09-08.md),
  [`inventory`](reports/junocam_expansion_inventory_2026-09-08.md) and
  [`pipeline audit`](reports/junocam_expansion_pipeline_2026-09-08.md)
  -- primary instrument evidence, exact archive costs and safe incremental processing.
- [`reports/junocam_calibrated_assessment_2026-09-07.md`](reports/junocam_calibrated_assessment_2026-09-07.md)
  -- bounded PDS sample inspection, generated-channel provenance and suitability.
- [`reports/usability_scientific_review_2026-09-07.md`](reports/usability_scientific_review_2026-09-07.md)
  -- usability and aesthetics assessment, live JunoCam readiness findings,
  archive comparison, screenshots, and prioritized design backlog D01–D12.
- [`decisions.md`](decisions.md) -- one entry per settled choice, with
  evidence and where it is enforced; do not reopen without new evidence.
- [`open_items.md`](open_items.md) -- known gaps: kernel coverage,
  unresolved residuals, deferred GUI features, undocumented VICAR
  keywords.

## The GUI

- [`gui_usage.md`](gui_usage.md) -- serving it, tunnelling to it from a
  laptop, what each view does, what it writes.
- [`gui_guide.md`](gui_guide.md) -- illustrated walkthrough of all five views,
  with current controls, JunoCam eligibility and scientific export workflows.
- [`gui_v2_notes.md`](gui_v2_notes.md) -- the React + deck.gl front
  end's architecture, state model, and design intent, for anyone
  extending it.

## Process

- [`build_log_2026-09-08.md`](build_log_2026-09-08.md) -- JunoCam expansion,
  strip preservation repair, measured validation and documentation refresh.
- [`build_log_2026-09-07.md`](build_log_2026-09-07.md) -- accepted D01–D12
  implementation, validation and remaining data limitations.
- [`agent_harness.md`](agent_harness.md) -- the spec -> gate -> execute
  -> verify loop this repository is built with.
- [`../AGENTS.md`](../AGENTS.md) -- entry point for any agent working in
  this repository; read this before changing code.
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) -- the same loop, phrased
  as a human contributor workflow (branches, pull requests).
- [`../CLAUDE.md`](../CLAUDE.md) -- project rules and layout conventions.
- [`build_log_2026-09-04.md`](build_log_2026-09-04.md) -- the
  milestone-by-milestone build record: who built what, which gate it
  passed, and the numbers each step produced.
- [`brainstorm_2026-09-03.md`](brainstorm_2026-09-03.md) -- the original
  design record and the decisions it left open (see `decisions.md` for
  how most of them were settled).

## Specs (`specs/`)

One per milestone: goal, files in scope, measured facts, the exact
design, and the validation that defined done. `decisions.md` and
`architecture.md` summarise what these settled; read a spec itself when
you need the exact column list, formula, or CLI signature it specifies.

- [`specs/2026-09-04_crawl_index.md`](specs/2026-09-04_crawl_index.md) --
  archive crawler, label parser, `frames.parquet`, the four original
  subcommands (`manifest`, `mirror`, `index`, `stats`).
- [`specs/2026-09-04_geometry_engine.md`](specs/2026-09-04_geometry_engine.md)
  -- the vectorised SPICE engine, `KernelSet`, the `kernels` subcommand.
- [`specs/2026-09-04_geo_augment.md`](specs/2026-09-04_geo_augment.md) --
  `frames_geo.parquet`, the `geo` subcommand, the L/M half-order
  investigation.
- [`specs/2026-09-04_reproject_paper_grid.md`](specs/2026-09-04_reproject_paper_grid.md)
  -- the VICAR reader, the inverse-camera reprojection engine, fitting
  the paper's map grid.
- [`specs/2026-09-05_regions_stacks.md`](specs/2026-09-05_regions_stacks.md)
  -- the region registry, time stacks, movies, the goflow export
  layout.
- [`specs/2026-09-05_tracking.md`](specs/2026-09-05_tracking.md) -- the
  classical tracker and its validation against TRACKER4.
- [`specs/2026-09-05_stats2d.md`](specs/2026-09-05_stats2d.md) -- masked
  spectra, structure functions, bicoherence.
- [`specs/2026-09-05_strips.md`](specs/2026-09-05_strips.md) -- the strip
  library: chunking, grids, the index.
- [`specs/2026-09-05_trackability.md`](specs/2026-09-05_trackability.md)
  -- the repeat-view and displacement-resolvability analysis.
- [`specs/2026-09-06_api_contract.md`](specs/2026-09-06_api_contract.md)
  -- the GUI v2 API contract shared by the backend and the front end.
- [`specs/2026-09-06_frontend_v2.md`](specs/2026-09-06_frontend_v2.md)
  -- the React + deck.gl front end that replaced the first version's
  server-rendered GUI.
- [`specs/2026-09-05_generalize_paths.md`](specs/2026-09-05_generalize_paths.md)
  -- removing hardcoded paths, `config.py`, `jiram-catalog config`.
- [`specs/2026-09-05_documentation.md`](specs/2026-09-05_documentation.md)
  -- the spec this documentation pass itself followed.

## Reports (`reports/`)

Reconnaissance and validation write-ups, referenced from `decisions.md`
and `open_items.md` for their numbers.

- [`reports/jiram_pds_research.md`](reports/jiram_pds_research.md) --
  archive layout, formats, sources, before the crawler was written.
- [`reports/juno_mission_facts.md`](reports/juno_mission_facts.md) --
  mission timeline, instrument facts (wavelengths, IFOV, spin rate),
  fact-checked against Adriani et al. (2017).
- [`reports/jiram_pointing_overlap.md`](reports/jiram_pointing_overlap.md)
  -- the de-spinning mirror, focal-plane orientation, why a mirror-blind
  geometry engine was judged adequate.
- [`reports/local_jiram_inventory.md`](reports/local_jiram_inventory.md)
  -- inventory of the published ground-truth data directory.
- [`reports/spice_kernel_coverage.md`](reports/spice_kernel_coverage.md)
  -- kernel counts, date ranges, and the CK/SPK gaps that motivate part
  of `open_items.md`.
- [`reports/lm_half_order.md`](reports/lm_half_order.md) -- the evidence
  and the lead's decision on which detector half is L and which is M in
  a 256-line product.
- [`reports/vicar_map_projection_notes.md`](reports/vicar_map_projection_notes.md)
  -- interpreting the paper maps' VICAR label keywords without access to
  MIPL documentation.
- [`reports/paper_projection_fit.md`](reports/paper_projection_fit.md)
  -- fitting the published perijove-4 maps' actual projection
  (orthographic, not the assumed stereographic).
- [`reports/tracking_pj4.md`](reports/tracking_pj4.md) -- the classical
  tracker validated against the paper's own maps and against this
  pipeline's reprojections.
- [`reports/trackability.md`](reports/trackability.md) -- per-band,
  per-orbit repeat-view and displacement-resolvability tables (long;
  see `decisions.md`/`open_items.md` for the headline numbers).
- [`reports/goflow_summary.md`](reports/goflow_summary.md) -- the
  downstream optical-flow model's input contract, which
  `export_goflow.py` was built to match.
