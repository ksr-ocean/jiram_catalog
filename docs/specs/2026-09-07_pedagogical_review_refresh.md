# Accepted pedagogical review refresh and GitHub delivery

Status: accepted. The owner asked whether the pedagogical code review had
been updated, then explicitly requested that it be updated and all completed
work pushed to GitHub. This scope updates its existing Markdown and companion
slide deck to implementation commit `1e2723d`, including the intervening
JunoCam acquisition, geometry, photometry and products commits. The original
2026-09-06 pedagogy spec supplies the audience and teaching register; this
spec supersedes its old output-length, no-commit and file-scope restrictions
for this refresh. No new scientific interpretation or numerical behavior.

## Ownership and read-only boundaries

- Review executor: `PEDAGOGICAL_REVIEW.md` only.
- Deck executor: `docs/pedagogy/slides.tex`, rebuilt `slides.pdf` and its
  tracked generated `slides.vrb` only. Existing screenshots/figures may be
  referenced; no new data generation is needed.
- Lead: this spec, documentation links in `README.md` and `docs/README.md`
  if useful, `docs/build_log_2026-09-07.md`, final independent verification,
  local commit, fetch/integration if necessary, and normal push to the existing
  GitHub remote/branch. No force push.
- Source, frontend source/build, dependencies, tests/gates, fixtures, all
  other specs/reports, mirrored native data and ground truth are read-only.
  Lead-only evidence-based exception, specified before editing: the recipe
  text in `science.provenance` says incidence <89 degrees while both native
  builders and `normalise_frame` apply `NIGHT_INCIDENCE_DEG = 88.0`. Derive
  this text from that existing constant. No estimator, mask or cutoff changes.
  Verify a small field crossing the 88-degree boundary and run the existing
  offline suite. Executors still treat all source as read-only.
- All runtime/LaTeX temporary files stay in the configured Lustre TMPDIR or
  the existing documentation build directory. No system `/tmp`.

## Required content

Preserve the review's eight-part teaching structure and established JIRAM
deep dives, distinguishing historical measurements from present behavior.
Keep section numbers 4.1 through 4.17 stable. Extend with **4.18, JunoCam**
and **4.19, scientific workflow**, so deck and review share stable references.
Update introduction, module/data-flow/path maps, browser section 4.16,
export discussion, testing/implementation lessons and glossary where needed.
Do not leave blanket JIRAM-only claims applying to JunoCam (byte order,
frame epochs, units, sequence identity, geometry or observing cadence).

Teach the current code, with short accurate excerpts/formulas and links:
1. JunoCam archive/version identity, dynamic discovery, image/label indexes,
   unsigned integer EDR/RDR and decompanding, per-band framelets, camera
   distortion, per-frame timing, limb refinement, reprojection and masks.
2. Documented failure exclusions, measured signal checks, unassessed states,
   latest-known-version defaults and direct pixel/derived-product access.
   A/B/C grades and annealing alone do not establish instrument health.
3. Physical band selection, native DN versus JIRAM radiance, Lambert/Minnaert
   limits, thermal nightside treatment, normalization/stretches, and the
   different pixel scales of display versus native `flat:sigma` processing.
4. Five task views, selection/density/details, lazy statistics, full request
   identity against stale responses, NetCDF serialization, bounded metadata
   reads and cache invalidation. Native full-resolution statistics still cost
   minutes; responsiveness fixes do not constitute new science estimators.
5. Cadence/grid/mask export preflight and provenance, Compare registration
   conventions/sampling/uncertainty, independent-pass population summaries,
   masks and slope fits, approximate cross-instrument matching, strict vector
   association and honest unavailable states.
6. PDS calibrated collection as a generated/mosaicked reference: use the
   already completed bounded audit and native-versus-derived distinction;
   do not imply quantitative native import or instrument repair certification.
7. Evidence: current snapshot 72 eligible JunoCam observations, three unique
   polar observations with irregular ~577/243-second gaps; 339 offline and
   135 frontend tests, eight focused API/science gates, successful full
   production browser gate, and the preserved old >=100 count-gate conflict.

Read implementation bodies and corresponding reports; code controls current
behavior, existing records support numerical results. The review is an
explanation for an ocean modeler modifying the code, not a release-note dump.
Avoid executor product names in the shared teaching material. Each new slide
has one main idea, legible content and the matching review-section footer.
Retain the existing six scientific figures and validated JIRAM history.

## Lead acceptance gate, defined before dispatch

- Verify every required topic above against actual code/report evidence;
  search globally for stale three-tab, all-data-float32, single-epoch,
  unrestricted-export, no-science-in-GUI and unconditional-repeat claims.
- Check Markdown local links/section references and all LaTeX figure paths.
- Compile twice through `latexmk -pdf -interaction=nonstopmode slides.tex`;
  require a nonempty current PDF, successful build, no new clipped content,
  and visually inspect rendered representative updated/new slides.
- Confirm source unchanged from `1e2723d` except the narrow recipe-text
  correction above; build, dependencies, tests and fixtures remain unchanged.
  Existing offline tests plus the boundary/recipe check verify that correction;
  no new test file or repeat browser run is required.
- Record scope, page/slide counts, verification and judgment calls, commit
  completed documents locally, push all accepted commits normally, and verify
  remote main equals local main.

Judgment calls: the request includes the review's companion deck and compiled
PDF; preserve original scientific figures and section references while
extending the teaching coverage. Existing user authorization includes push.
The historical recommendation of unavailable model classes does not prevent
using the available executors for bounded prose/deck updates.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
