# Open items

Known gaps, unresolved questions, and deferred work. Each item names
what would close it. See `docs/decisions.md` for the choices that *are*
settled and `docs/agent_harness.md` for how a new milestone gets opened
against one of these.

## JunoCam formats and calibration beyond the September 8 RGB expansion

The [expansion audits](reports/junocam_expansion_evidence_2026-09-08.md) found
three concrete limits that remain outside the supported full-resolution RGB
sample. Summed 816-column products need a sampling-aware detector model;
methane labels can override detector distortion-Y (405.48 instead of the
fixed 315.48 used by the current model); and PJ3's JIRAM orbit bucket does not
identify the December JunoCam pass's navigation kernels. Each requires its own
source/geometry gate before acquisition and clearance. NULL or absent archive
timing is not zero, and some latest PJ3 products still use the old companding
pipeline. Broad epoch eligibility would hide these distinctions.

Native cross-pass brightness and spectral amplitude also need defensible
response/illumination/compression controls. The published throughput fit lacks
per-image uncertainties and is not automatically applied. Current maps support
screened morphology and qualified relative-signal analysis; they do not
establish invariant radiometry or a validated wind product.

Cross-instrument candidate matching now uses the actual JIRAM corners and
physical detector halves. PJ24 already has JIRAM strips in the selected
encounter window; PJ5, 6, 8, 12, 18, 30 and 34 have local JIRAM native images
and navigation metadata but need JIRAM strip products for the same library
workflow. A returned time/bounding-box candidate does not certify common
valid pixels, equal grids, illumination or cloud altitude.

Expose limb-fit outcomes alongside available footprint geometry in future
navigation/readiness controls. The new mapped sample has 103 successful fits
and 23 refusals below the existing 200-point minimum, with nominal navigation
retained for the latter. Their fitted offsets/residuals remain unknown. The
[per-source navigation table](reports/junocam_expansion_navigation_2026-09-08.csv)
and stack/run tables preserve those distinctions now; successful fitting still
needs independent positional and motion validation.

Same-ID geometry-fit and GUI statistics caches need source-aware invalidation
before general reprocessing of existing products. This expansion uses new IDs
and preserves previous products. CLI index writers must still be serialized;
the new additive strip upsert is not a cross-process lock. Full survey geometry
also allocates native arrays before subsampling, so worker counts must follow
measured memory rather than the survey docstring's sampling description.

Dense JunoCam construction repeats inverse projection for signal, emission
and incidence in each band. The September 8 PJ5 pilot took 31.17 minutes for
five strips with one worker. Sharing that calculation is a possible bounded
performance improvement, but requires an independent equivalence gate for
images, angle fields, masks and coordinates before changing the numerical
path. This expansion keeps the existing mapper and measures its worker costs.

## SPICE geometry fails entirely for orbits 38 and 70, partially for 55

`<mirror>/index/geo_report.md` and `frames_geo.parquet` show `geo_ok`
false for 1,795/1,795 rows of orbit 38, 850/850 of orbit 70, and 82 of
4,000 rows of orbit 55 (build log: "kernel gap-fill for orbits
38/55/70", `docs/build_log_2026-09-04.md`). Reading the actual
`geo_error` text (not previously recorded anywhere) distinguishes two
different failures:

- **Orbit 38** (2021-11-29) and **orbit 55** (2023-10-14, the 82
  failing rows) raise `SpiceNOFRAMECONNECT`: "insufficient information
  available to transform from reference frame ... `JUNO_JIRAM_I_MBAND`
  (or `_LBAND`) to reference frame ... `J2000`" -- the spacecraft-bus CK
  loaded for that orbit does not cover the frame's exact epoch. This is
  an attitude/pointing gap.
- **Orbit 70** (2025-03-02/03) raises `SpiceSPKINSUFFDATA`:
  "insufficient ephemeris data ... to compute the state of -61 (JUNO)
  relative to 0 (SOLAR SYSTEM BARYCENTER)" -- the trajectory SPK loaded
  does not cover the epoch. This is a different kind of gap (position,
  not attitude) from orbits 38/55.

`docs/reports/spice_kernel_coverage.md` found six CK gaps longer than 7
days in the PDS archive's reconstructed-CK series, each covered instead
by a lower-accuracy `juno_sc_raw_*.bc` file for that span -- but none of
those six gaps falls near 2021-11-29, 2023-10-14, or 2025-03-02, so the
short gaps causing these three orbits' failures are not the ones that
report measured. **Not yet done**: identify the specific missing or raw
kernel for each date and confirm `kernels.py`/`KernelSet.for_orbits`
picks it up (currently it only resolves reconstructed CK/SPK names from
the archive label, never falls back to a `juno_sc_raw_*`/`juno_sc_pre_*`
equivalent when the reconstructed one is short). Until that fallback
exists, these three orbits (and any other orbit with a similarly narrow
gap) will have holes in `frames_geo.parquet` that no rerun of `geo` will
fix.

## L-band geometry is roughly twice as noisy as M-band against the label

The build log flags "the L-band 0.024 deg residual" as an open question
without giving the comparison number
(`docs/build_log_2026-09-04.md`, PAUSE line). Reproducing the gate's own
well-conditioned filter (`geo_ok`, fully on-planet, `geom_band` matching
`half`) for orbit 4 and computing the boresight-vs-label-centre
great-circle residual separately by band gives: **L median 0.0239 deg
(p95 0.059, n=277) versus M median 0.0104 deg (p95 0.056, n=427)** --
roughly double, though both are comfortably inside the 0.02 deg gate
tolerance that applies to M only (`tests/test_gate_geo_pj4.py` checks
M-band boresights to 0.02 deg but has no L-band assertion). Whether this
is a real geometric effect (e.g. the L-band frame's boresight or IFOV in
`juno_jiram_v02.ti` being less well characterised than M's, since L is
the auroral/H3+ channel and less used for navigation) or an artifact of
having fewer, more polar L-band label comparisons is not established.
**Not yet done**: a gate assertion for L-band geometry, and an
investigation of whether the discrepancy is IK-related or a property of
the specific frames compared.

## Kernel and archive completeness

- **Eight label downloads failed on the first pass** of the full-archive
  label mirror and were retried (`docs/build_log_2026-09-04.md`, step
  3d: "85,105/85,108 IMG labels; 8 files failed then retried"). The
  subsequent index build reports all 85,108 rows with `parse_ok=True`
  and the mirrored index today still shows 85,108/85,108, so the retry
  evidently succeeded -- but the build log never names which eight
  files, so there is no record to audit against if a silent corruption
  is ever suspected. **Not yet done**: nothing functionally, but a
  reproducible listing of "which products failed and why" was never
  captured.
- **Image data mirroring for orbits beyond 4 and 24 is a background job,
  not a completed, verified pass.** As of this writing the mirror holds
  30,400 of 85,108 `.IMG` files on disk, spread across every orbit
  directory that exists at all -- consistent with the "full image
  mirror all orbits" background job noted running in
  `docs/build_log_2026-09-04.md` (step 9d) -- but this project has not
  run `mirror --verify` archive-wide, so completeness and MD5 integrity
  beyond orbits 4 and 24 (the two verified for the gates) are unknown.
  `frames.parquet`'s `img_present` column is stale relative to whatever
  the background job has done since the index was last rebuilt for a
  given orbit (see `docs/data_products.md`).

## JPL VICAR documentation was never located

`MPOX`, `MPOY`, `DU`, `MPROJ` in the paper maps' VICAR labels have no
confirmed source: `www-mipl.jpl.nasa.gov` no longer resolves and
`mipl.jpl.nasa.gov` refused connections during the research session
(`docs/reports/vicar_map_projection_notes.md`). Their meanings used in
this codebase (`docs/decisions.md`, "the paper's map grid") are
empirical inferences from fitting the 48 frames, not confirmed against
MIPL's own projection-code table. In particular:

- `MPOX = MPOY = 3800` is 2000 px (30,000 km) away from the fitted pole
  pixel (1800, 1800) in both axes, equal and integral, but nothing else
  in the label explains the 2000 px offset (`XOFF = YOFF = 0` rules out
  the obvious explanation). Left unresolved in
  `docs/reports/paper_projection_fit.md`.
- `NORTHANG` varies frame to frame (66.3 to 289.1 deg over the 48) on
  one fixed grid, so it is some per-frame pointing quantity, not a grid
  parameter; its exact definition was not identified and nothing in the
  reprojection pipeline needs it.
- `MPROJ = 4`'s meaning in MIPL's internal table is inferred as
  "orthographic" purely from the fit (see `docs/decisions.md`); this
  contradicts the JIRAM literature's "polar stereographic" language for
  the same product family, which the fit shows numerically to be wrong
  for this projection (or the literature is imprecise; the two were not
  reconciled).

**Would close this**: access to `NASA-AMMOS/VICAR` source
(`vos/p2/sub/mp*`, `vos/p2/apimap*` on GitHub) or a live MIPL system to
read the actual projection-code table, per
`docs/reports/vicar_map_projection_notes.md`'s own suggestion.

## Mask leakage in spectra has no correction

`stats2d.py`'s periodogram correction (`valid_frac * window_power`) is
unbiased only when the mask is uncorrelated with the field and the
taper; masking a strip convolves the true spectrum with the mask's own
spectrum, moving variance across `k` even when the total variance is
right. The offline test suite measured this directly: a `k^-3` field's
fitted slope is -2.97 unmasked and -2.92 with 15% of pixels masked as a
few smooth blobs -- a mild, tolerable distortion for smooth, sparse
masks (`docs/build_log_2026-09-04.md`, step 9a; `src/jiram_catalog/
stats2d.py` module docstring). The same docstring states plainly that
"no scalar correction can repair" a speckle-like mask. **Not yet done**:
no correction exists for masks that are not smooth and sparse (e.g. a
strip whose invalid pixels are salt-and-pepper rather than a few large
holes). The new population panel reports connected components, boundary fraction
and seam diagnostics so these limitations are visible before pooling; those
diagnostics do not themselves correct spectral leakage.

## GUI and scientific follow-up after the 2026-09-07 implementation

The accepted D01–D12 review improvements are implemented; see
[research_workflow.md](research_workflow.md) and the
[build record](build_log_2026-09-07.md). Comparison, matched population
statistics, mask diagnostics, selected fit ranges, figure/recipe export,
coverage discovery, JunoCam failure exclusion and vector-overlay support
are available. Remaining data/validation limits:

- Full-resolution statistics on 6000-square JunoCam strips can take minutes.
  They run only when requested. Native arrays and the numerical estimator are
  preserved; persistent preview products or process-isolated analysis workers
  would be separate performance work.

- In-app strip construction remains a CLI workflow. Region builds are
  available from a selection; strip builds were not part of D01–D12.
- The vector overlay requires a product explicitly associated with the
  displayed stack, time and map basis. This mirror has none. Published TP4
  vectors have a dedicated validation reader; a reusable association/product
  writer would enable those overlays without guessing.
- The September 8 expansion adds short JunoCam sequences with measured
  cadence and common coverage, including PJ34 north. Their pointing and
  cloud-motion accuracy still require validation; format readiness is not a
  wind result. The historical PJ4 example retains its irregular gaps. See the
  [measured expansion record](reports/junocam_expansion_2026-09-08.md).
- The new analysis checks have synthetic validation, not archive-wide
  navigation/radiometric or wind validation. Population bicoherence and
  calibrated effective-resolution transfer functions remain future work.
- The PDS ML-derived collection is linked as a reference. Its missing tile
  times, generated channels, mask/scaling ambiguities and different grids
  prevent automatic quantitative import; see the
  [sample assessment](reports/junocam_calibrated_assessment_2026-09-07.md).

## Regions beyond the initial registry

The initial `configs/regions.yaml` has four entries (`north_pole_paper`,
`north_pole`, `south_pole`, `neb_15n`); `docs/build_log_2026-09-04.md`'s
PAUSE note flagged "user decisions on regions and viewer" as open at the
time. The viewer question is resolved (the GUI exists); which
additional named regions are worth adding (the Great Red Spot,
additional mid-latitude jets, specific vortices) has not been decided
and is a one-line addition to `configs/regions.yaml` plus a
`RegionGrid` construction once someone names a target.

## GUI: no authentication (added 2026-09-06)
The backend binds to the cluster network when tunnelled through the
login node and has no login. Acceptable for an interactive session that
is stopped afterwards; add basic authentication (e.g. FastAPI
middleware in front of the routers in `src/jiram_catalog/api/app.py`)
before anyone leaves it running unattended.

## Cumulative stack build cost (added 2026-09-06)
`region-stack --level cumulative` on the paper region takes about 7
minutes and 29 GB from an existing frame stack (the frame stack is
read whole; the zlib write of a 3.6 GB file dominates). Streaming the
frame file step by step and writing with larger chunks would cut both;
a job in the GUI shows progress meanwhile.

## JunoCam timing and limb physics (added 2026-09-07)
Limb-fit refinement of the start time gives offsets of 1-26 ms on
perijove 4 (median 4.6 ms) and residuals of about 0.5 px. Two findings
worth follow-up: (1) the limb where the planet enters a strip sits 1-8
px outside the 1-bar ellipsoid, more in blue and at high latitude,
i.e. haze above 1 bar, while the trailing limb sits on the ellipsoid;
a limb-height model or a trailing-edge-only fit would tighten the
residual. (2) Band registration across the strips prefers an
inter-frame delta near 2 ms rather than the kernel's 1 ms; the kernel
value is kept; a per-image rate term could be fitted alongside the
offset. See `docs/reports/junocam_pj4_geometry.md`.

## Accepted usability and JunoCam review

The [review](reports/usability_scientific_review_2026-09-07.md) preserves the
original JC-01–JC-07 findings and D01–D12 recommendations. The owner accepted
implementation on 2026-09-07; these are no longer pending proposals.
[Implementation spec](specs/2026-09-07_review_implementation.md),
[current usage](research_workflow.md) and
[verification record](build_log_2026-09-07.md) describe the resulting behavior.

Judgment calls: kept the unresolved physical/data questions above distinct
from implemented usability features. Export readiness is not wind validation;
reference availability is not permission to treat generated reflectance as
an original observation.
