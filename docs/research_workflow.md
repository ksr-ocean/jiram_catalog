# Scientific workspace: current workflow and capability matrix

Updated 2026-09-07. Use the [illustrated GUI guide](gui_guide.md) for current
screenshots and step-by-step controls, or [gui_usage.md](gui_usage.md) for
serving and tunnelling. The data mirror is shared; the Coverage view
reports its actual processing stages and source timestamps.

## Choose a task

| View | Main task | What to check before interpreting a result |
|---|---|---|
| Explore | Find mapped observations with task presets, instrument controls and footprint latitude coverage; inspect and select products. | Dates, product identity, native band, coverage and quality reasons. A footprint intersecting a region differs from its boresight lying there. |
| Time series | Step through a region, inspect image geometry, render a physical-band movie, preflight/export a sequence. | Unique observations, positive cadence, common mask and chosen normalization. Frame/sequence/cumulative identify different temporal products. |
| Image library | Inspect per-pass strips and individual or grouped intensity statistics. | Native resolution, wavelength, masks, normalization and independent pass count. |
| Compare | View two frames/strips with linked navigation, split/blink, shared stretch and common-mask overlay. | Equal physical grids, time difference, registration sampling, scene differences and user-supplied navigation uncertainty. |
| Coverage | Search archive metadata, see processing-stage counts and source age, inspect exclusions and reference maps. | Archive-known does not mean pixels are local, navigated, quality assessed or analysis ready. |

The selection tray and metadata areas collapse to leave more room for imagery.
Image and statistics panels have minimum usable heights; controls wrap at
smaller desktop sizes. The map starts with coverage density to avoid stacking
thousands of opaque footprint polygons. Outlines, coordinate conventions,
selected observations and legends remain available for inspection.

## JunoCam policy and identity

`failure-exclusion-v1` is enforced in the source loaders, not just the filter
controls. Documented bad images/intervals and measured signal failures are
excluded. Missing evidence is unassessed, including post-anneal observations
without specific clearance. Both states are metadata-only. There is no
quality toggle that reveals their pixels. The policy records instrument,
signal and navigation reasons separately from a legacy A/B/C tier.

The current mirror has 72 preferred eligible PJ4 observations. Compared with
the earlier 93 mapped observations, 21 additional methane observations are
withheld by their existing bloom flags. This is a conservative image metric:
line means exceeding three times the framelet median on more than 5% of
lines. It is not proof that every flagged image has a documented hardware
incident. No RGB observation was removed by this additional cut. Ten other
observations fail geometry and were already absent from the mapped catalog.

Defaults use the latest known processing version, then require its
eligibility. A version suffix is provenance, not a new exposure; corrected
label times differing by milliseconds do not create independent observations.
An eligible older version can be inspected explicitly. Existing derived
stacks retain their stored source version provenance and are filtered without
rewriting the source file.

The local JunoCam polar stack now displays three observations. Their gaps
are approximately 577 and 243 seconds, so there is no regular three-frame
sequence under the 5% tolerance. RGB is a display composite; choose RED,
GREEN, BLUE or another actual available channel for analysis, movies and
exports. Exposure units and normalization follow the selected band; an
illumination correction does not turn DN into calibrated I/F.

## Comparing images

Choose two sources and physical bands. Linked pan/zoom lets the same map
coordinates occupy the same screen position; split and blink reveal changes
and navigation differences. A common-mask overlay shows the shared support.
Numerical registration runs only when projection and coordinates establish
equivalent grids. Different grids report incompatibility and need a separate,
documented reprojection.

The registration result is a bounded, sampled image displacement. It is not
a retrieved wind field. Predicted displacement uses the chosen speed,
time separation and map pixel size. If you enter a per-image navigation
uncertainty, the display propagates independent errors from both images;
otherwise the uncertainty remains unknown. Cross-band morphology may move
the correlation peak even when the atmosphere has not moved.

The vector overlay reads existing matching NetCDF products only when source
stack, time, projection, `map_xy` basis, kilometre coordinates and m/s units
are explicit. This mirror has no such generic product. Published TP4 vectors
remain accessible through the established validation script; arbitrary
association with a displayed stack is not inferred.

## Statistics and reproducibility

Population analysis accepts up to 100 selected strips. It groups instrument,
physical band, native resolution class, units and normalization. Duplicate
versions or shared source observations are counted once. Each pass contributes
one mean; the displayed standard error comes from the means of independent
passes. One pass has unknown population standard error, not zero uncertainty.

Inspect validity fraction, connected mask components, boundary and seam
diagnostics before fitting slopes. Fit positive bins within an explicit
wavenumber range; the default fit stays inside the Nyquist disc. These are
intensity-variance spectra and structure functions, not kinetic-energy
spectra. A scalar mask correction cannot repair spectral leakage.

Statistics run when requested; a 6000-square JunoCam strip can take several
minutes at native resolution. Display contours use bounded sampling for
responsiveness, while the scientific estimator retains native pixels.

Normalization sensitivity compares processing choices as different groups.
JIRAM thermal data keeps nightside emission valid; reflected-light Lambert
and Minnaert corrections are rejected for it. Export SVG/PNG figures with
numeric values and a recipe recording source versions, settings, pass
weighting, software revision, units and available kernel provenance.

For `flat:sigma`, sigma is measured in the array's pixels: served preview
pixels in the image display, native pixels in science calculations and
exports. Consequently the same numerical sigma can smooth different physical
scales. Treat display flattening as a viewing aid; use the recorded native
analysis recipe when comparing spatial statistics.

## Capability and validation scope

| Capability | Implementation | Scientific validation scope |
|---|---|---|
| JIRAM navigation / paper-grid reprojection | Available | Published PJ4 comparison; known kernel gaps remain for other passes. |
| JunoCam navigation | Native framelet geometry and PJ4 refinement | Local PJ4 geometry assessment; unresolved limb-height/timing effects remain. |
| Instrument-failure exclusion | Default catalog, image and derived-product access | Versioned conservative rules and measured local metrics; no archive-wide radiometric certification. |
| Velocity-model export | Readiness, physical-band selection, masks, native units and provenance | Synthetic contract tests and existing JIRAM layout checks; not inferred wind accuracy. |
| Comparison / population analysis | Interactive views and bounded APIs | Known synthetic shifts, power laws, masks, cadence, grouping and uncertainty tests. |
| Cross-instrument matching | Time proximity plus seam-aware spherical footprint boxes | Approximate candidates only; no exact pixel overlap or common cloud-level claim. |
| Reference maps / ML calibrated collection | Provenance links in Coverage | Context resources; quantitative automatic import is not enabled. See the [calibrated-data assessment](reports/junocam_calibrated_assessment_2026-09-07.md). |

New research caches and movies live in `<mirror>/gui_cache/research/`;
velocity-model exports live in `<mirror>/gui_cache/exports/`. GUI export paths
stay inside that directory, and explicitly named destinations must be empty.
Default export identities include the filtered sources and settings, so a
new quality assessment cannot mix new runs with stale realization files.
PNG/movie responses for JunoCam are not retained in the HTTP cache.

Judgment calls: preferred task-oriented names while retaining existing API
identifiers; used per-pass uncertainty rather than counting overlapping
strips as independent; withheld unknown instrument states; preserved native
data and separate derived reference products. Refused unsupported numerical
comparisons instead of guessing a projection, time or vector association.
