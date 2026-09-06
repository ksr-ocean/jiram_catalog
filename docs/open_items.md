# Open items

Known gaps, unresolved questions, and deferred work. Each item names
what would close it. See `docs/decisions.md` for the choices that *are*
settled and `docs/agent_harness.md` for how a new milestone gets opened
against one of these.

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
holes), and no diagnostic currently flags which strips have which kind
of mask geometry before their spectra are trusted or pooled into a
population statistic.

## GUI features deferred past the first version

Per `docs/gui_design.md`, "First version": the Catalog tab is complete,
Poles is a viewer only (opens stacks that already exist; cannot build
one from the GUI), and Strips has the table, map, viewer and per-strip
statistics panel, but not the population-level view. Explicitly
deferred to a later version:

- **In-app stack/strip builds.** `region-stack` and `strips` still have
  to be run from the command line; the GUI can only open what already
  exists under `<mirror>/regions/` or `<mirror>/strips/`.
- **Population statistics in the GUI.** `stats2d.population_statistics`
  exists and is reachable from `strip-stats --population` on the
  command line, but the Strips tab's "population mean spectrum with
  standard error, and the bicoherence map, computed on demand and
  cached" (`docs/gui_design.md`, "3. Strips") was not built into the
  first version.
- **Tracking-vector overlay** on the Poles viewer ("overlay tracking
  vectors when a vector file exists, published or ours",
  `docs/gui_design.md`, "2. Poles") was designed but not implemented;
  `tracking.py`'s output is not currently wired into any GUI view.

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
The Panel server binds to the cluster network when tunnelled through
the login node and has no login. Acceptable for an interactive session
that is stopped afterwards; add basic authentication (Panel supports
`--basic-auth`) before anyone leaves it running unattended.
