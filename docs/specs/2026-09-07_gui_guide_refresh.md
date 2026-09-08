# Accepted illustrated GUI guide refresh

Status: accepted by the owner's request to update the outdated `gui_guide`
in docs. This is documentation of the delivered five-view interface at
`daccd3a`, not a new interface or scientific change. The earlier request to
push completed work to GitHub remains applicable. This spec supersedes the
2026-09-06 guide's three-tab scope, tall viewport workaround, image-size
constraints and no-commit restriction.

## Facts and scope

The current guide and its screenshot script describe Catalog / Poles /
Strips. The application now presents Explore / Time series / Image library /
Compare / Coverage. Current real screenshots already exist under
`docs/reports/figures/implementation_2026-09-07/`; those are read-only and
may be referenced directly instead of duplicating image files.

- Screenshot executor owns only `docs/gui_guide/take_screenshots.py` and new
  `docs/gui_guide/current_*.png` files. Read this spec and actual source for
  selectors. Use the existing Python Playwright helper as the entry point;
  ephemeral tool dependencies are allowed, repository dependencies unchanged.
- Lead owns `docs/gui_guide.md`, brief link descriptions in `README.md`, `docs/gui_usage.md`,
  `docs/research_workflow.md`, `docs/README.md`, this spec and the build log.
  Lead verifies images, controls, links and scope, commits and pushes normally.
- All application source, build artifacts, dependencies, tests, fixtures,
  other specs/reports, native data and ground truth are read-only. Do not
  create stacks, exports or saved selections for screenshots. Normal GUI
  read caches are allowed in the configured mirror. Runtime files and server
  logs must use configured Lustre TMPDIR, never system `/tmp`.

## Screenshot deliverable

Replace the stale script with reproducible real-browser capture against the
committed production app on an automatically chosen loopback port. Ensure
server/browser cleanup on failure, drain server logs to a scratch file, and
use loaded-image/application-state waits rather than a fixed sleep as proof
of readiness. Capture at 1440 x 900, preserving readable control text. Use
lossless PNG optimization; no forced palette quantization of scientific
imagery. Existing historical PNGs may remain unreferenced.

Required new images:

1. `current_explore.png`: Explore, loaded density map and current controls.
2. `current_selection.png`: eligible JunoCam filter, nonempty browser-only
   selection and visible selection tray; no saved-selection writes.
3. `current_time_series.png`: JIRAM stack with loaded image, controls visible.
4. `current_image_library.png`: library filters and loaded JIRAM image.
5. `current_compare.png`: source controls and loaded same-grid comparison;
   label any identical-source example as a control in the guide.
6. `current_coverage.png`: actual stage counts/policy, no invented records.

An optional `current_junocam_readiness.png` may capture the three-observation
physical-band readiness state if achievable without large new calculations;
otherwise the existing JunoCam time-series evidence can be referenced.
Do not generate expensive statistics merely to duplicate existing verified
statistics screenshots. Report actual source ids and any omitted captures.

## Guide content

Write an illustrated task guide with a quick first session, serve/tunnel
commands, five-view navigation map, current visible controls and concrete
workflows. Cover selection/save/load/build destinations, footprint versus
boresight search, instrument-specific bands, modes, normalization/stretches,
lazy statistics, population weighting and fit diagnostics, comparison and
uncertainty limits, archive stages and external reference collections.

Explain that exclusions and unassessed instrument states are metadata-only
with no pixel override; a legacy A/B/C tier does not establish health.
Distinguish RGB display from physical-band analysis, native DN from calibrated
I/F, and three unique irregularly spaced local polar observations from a
regular exportable triplet. The current local counts are a dated snapshot.
Use existing research records for scientific assessment, with links; no new
web research is needed to document established application behavior.

Accurately describe the tray's **Browse images from these orbits** action as
an orbit filter, not an exact selected-source filter. The detail card offers
selection and cross-instrument context, not a library handoff. Document downloads
versus server files and native statistics costs. Replace old portrait-browser,
always-visible selection, automatic-statistics and unrestricted-export claims.
Do not claim every path was exercised solely from screenshot capture.

## Lead acceptance gate, fixed before dispatch

This reversible documentation update uses a manual artifact gate rather than
new application tests: check all five view names and controls against source,
all local links/images exist, inspect every new PNG, run the screenshot helper
successfully, and confirm no source/build/dependency/test/data changes. Verify
no stale historical-guide pointers remain in updated entry points. Reusing
existing statistics images must be explicit about their capture date.
Run Python compilation for the helper and `git diff --check`, record results
and judgment calls, then commit/push and verify local and remote main match.
The existing full software validation remains valid because software is
unchanged. A capture timeout may be repaired within the helper; it does not
authorize application changes or weakened image readiness checks.

Judgment calls: retain old captures as historical artifacts but link the
current guide only to current captures; prefer normal desktop dimensions and
unmodified scientific colours over the old 300 KB limit; delegate screenshot
automation while the lead writes and checks the scientific user instructions.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
