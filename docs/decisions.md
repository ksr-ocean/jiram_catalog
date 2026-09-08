# Decisions

## Bounded JunoCam expansion and additive strip builds (2026-09-08)

The owner requested actual multi-pass expansion. The lead selected 194 exact
preferred RGB products from PJ5, 6, 8, 12, 18, 24, 30 and 34 after independent
archive, instrument and pipeline audits. Selection uses supported unsummed
dimensions, complete framelets, inspected corrections and finite archive timing;
zero is a determined no-shift result, while NULL and absence are unassessed.
The published offsets are already incorporated in label times and must not be
added again. IDs, versions, URLs and checksums are frozen in the
[acquisition oracle](reports/junocam_expansion_selection_2026-09-08.csv).

NASA's affirmative normal-operation evidence through PJ34 supports individual
screening. It does not whitelist that epoch. Newly cleared IDs also require
verified bytes, clean finite overall and per-colour metrics and successful
geometry. Existing failure exclusions and PJ4 evidence are retained. The
instrument team's mission-long optical-response evolution is a separate
quantitative limitation: native RDR already contains archive exposure/distance
scaling, remains DN, and receives no automatic throughput correction. The
existing 47–60 numeric factor domain is retained for compatibility; it is not
a physical onset boundary or evidence of unit response earlier. See the
[evidence audit](reports/junocam_expansion_evidence_2026-09-08.md).

Incremental JunoCam strip builds upsert successful product IDs and preserve
omitted rows and files, including another band selection and failed rebuilds.
The ID-based filename can hold one band set; incompatible replacements fail
explicitly. Metadata is validated before atomic pixel-file replacement.
Pruning and multiple named band variants require separate work. Shared index
writers remain serialized operationally. Enforced by
`junocam/strips.py` and `test_gate_junocam_incremental.py`.

Cross-instrument candidate searches fall back to valid corner coordinates when
an optional footprint outline is empty. For paired JIRAM products, the returned
bands identify only detector halves that pass the time and spatial filters;
the product-level `LM` label is not evidence that both halves qualify. The
real-catalog audit and fixed `test_gate_junocam_matches.py` cases enforce these
repairs. Bounding-box candidates still require physical-image/mask validation
before scientific pairing; see the
[measured match results](reports/junocam_expansion_matches_2026-09-08.csv).

## Scientific workspace and JunoCam failure exclusion (2026-09-07)

The owner accepted D01–D12 in the usability review and requested their
implementation. `failure-exclusion-v1` uses documented image/interval failures
and conservative pixel checks; unassessed instrument states remain
metadata-only. Annealing and legacy A/B/C grades do not establish per-image
recovery. Latest archive-known RDR versions are the default observation
identity; duplicate versions and corrected millisecond timestamps do not add
independent exposures. Old derived products are filtered at access without
rewriting archived files. Enforced by `junocam/policy.py`, `api/data.py`,
builders, and movie/export integration.

Native-band analysis/export requires explicit channel selection, valid
common support and regular positive cadence. Population comparisons separate
instrument, band, units, normalization and resolution class; uncertainty is
across independent pass means. Image registration is not wind truth. Unknown
navigation uncertainty, vector association and grid equivalence remain
explicitly unknown. Enforced by `science.py`, `api/science.py` and the
[current scientific workspace](research_workflow.md).

The PDS `junocam_atm-ml-calib` bundle is a derived reference, not an automatic
replacement for native radiance or motion observations. Actual sample labels
have no tile times; GeoTIFFs have 62.5 km pixels and generated HST-equivalent
channels. Scaling/mask examples need reconciliation before quantitative
import. Evidence: [bounded collection audit](reports/junocam_calibrated_assessment_2026-09-07.md).

Arrow's service pool is bounded independently of BLAS/OpenMP (default four,
overridable with `JIRAM_ARROW_THREADS`). On this node, inheriting
`OMP_NUM_THREADS=1` made cold catalog construction take about 29 seconds;
explicit Arrow four restored about 0.8 seconds with identical rows. The
existing physical models and geometry conventions are unchanged.

NetCDF-backed API routes and background jobs share a process-local lock.
Concurrent first-open requests caused a reproducible native-library crash;
serializing these operations follows the
[netCDF C thread-safety restriction](https://docs.unidata.ucar.edu/netcdf-c/current/faq.html).
Config, health, jobs, selections and catalog requests stay outside that lock.
Bounded stretch and display-contour sampling slices lazy arrays before loading
them; full-resolution science runs only on explicit request. This trades
parallel image processing for reliability; process-isolated workers are a
future scaling option, not a prerequisite for the present local workspace.

One entry per settled choice: what was decided, the evidence, and where
it is enforced. Do not reopen these without new evidence -- see
`AGENTS.md`. Sources are named per entry; the general process that
produces entries here is `docs/agent_harness.md`.

## Scope

**2026-09-04. Camera frames only for v1** (`JIR_IMG_RDR_*`, not the
spectrometer's `JIR_SPE_RDR_*`). Decision recorded as "imager only" in
`docs/brainstorm_2026-09-03.md` (#6.1) and accepted by the user the same
day (`docs/build_log_2026-09-04.md`, "Decisions (defaults accepted by
user 2026-09-04)"). Enforced in `mirror.py` (`--family img` default) and
`index.py` (parses `JIR_IMG_RDR_*.LBL` only); the manifest still lists
`SPE`/`LOG_*` families for a future extension.

**2026-09-04/05. v1 runs on the cluster only**, not packaged for a
laptop. `docs/brainstorm_2026-09-03.md` (#6.5) posed this as an open
question ("whether v1 must run on the cluster only, or also on a
laptop"); it was settled implicitly rather than debated: the mirror
lives on Lustre (`CLAUDE.md`), the GUI is served from a cluster node and
reached over an SSH tunnel rather than run locally
(`docs/gui_design.md`, "Deployment"; `docs/gui_usage.md`), and every
default path in `config.py` is a cluster path. `docs/specs/
2026-09-05_generalize_paths.md` made those paths overridable
(`JIRAM_MIRROR`, `JIRAM_PAPER_DATA`, a TOML file) but did not change
this: the defaults, and the assumption that SPICE kernels and ~25 GB of
image data live on a shared filesystem, still target the cluster.

## Geometry conventions

**2026-09-04. Planetocentric latitude, east-positive longitude,
`[0, 360)`**, everywhere in the catalog. Matches the archive labels'
own convention (`docs/specs/2026-09-04_crawl_index.md`, "Longitudes are
stored exactly as labelled ... do not convert") and is restated as a
project-wide rule in `AGENTS.md`. Enforced by construction in
`geometry.py` (`planetocentric()`: `lon_east = atan2(y, x) mod 360`) and
followed by every downstream module (`geo.py`, `reproject.py`,
`regions.py`, `strips.py`).

**2026-09-04. Geometry epoch is the label `START_TIME` exactly; no
exposure offset.** Measured before the geometry engine was built:
Juno spins at 12 deg/s (2 rpm), so a boresight computed at mid-exposure
(+1.25 s for a typical M-band frame) differs by tens of degrees from
one computed at `START_TIME` (`docs/build_log_2026-09-04.md`, step 1b
"48-frame result"). Enforced in `geometry.py` (`frame_geometry(epoch,
...)` takes the label's `start_time` column directly; `geo.py` never
adds an offset before calling it) and stated as a project-wide rule in
`AGENTS.md` and `docs/agent_harness.md`.

**2026-09-04. Mirror-blind SPICE geometry.** The engine does not model
JIRAM's de-spinning mirror at all; it uses only the spacecraft bus
attitude (CK) and the static IK/FK instrument geometry. Justified before
any code was written: mirror-blind SPICE at `IMAGE_TIME`, using only the
bus CK and a static M-band frame, matched all 48 orbit-4 label centres
to a lat/lon median of 0.006 deg (max 0.31 deg at 89 deg latitude),
emission to 0.017 deg max, altitude to 28 km max
(`docs/build_log_2026-09-04.md`, step 1b). `docs/reports/
jiram_pointing_overlap.md` researched the mirror mechanism itself and
found the CK-only approach adequate for this accuracy. Enforced by
`geometry.py`'s algorithm never referencing a mirror model or a
mirror-angle telemetry field.

**2026-09-04 (spec), revised 2026-09-04 (implementation). Inverse
stellar aberration applied to both the pixel rays and the observer
vector.** `sincpt`'s `srfvec` and every pixel ray are *apparent*
directions under `abcorr="LT+S"`; both must be converted to *geometric*
directions with the same inverse correction before the ellipsoid
intersection. The geometry-engine spec's original step 3 used `srfvec`
directly; the executor found this left a per-pixel error of 4e-3 deg
against the oracle, and the corrected version (converting `srfvec` the
same way as the pixel rays) reduced it to 8e-5 deg
(`docs/specs/2026-09-04_geometry_engine.md`, "REVISED 2026-09-04";
`docs/build_log_2026-09-04.md`, step 5'). `reproject.py`'s inverse
camera model mirrors the same correction in the forward direction
(module docstring, `_camera_transforms`); skipping it there leaves a
~7 arcsec (~1/3 pixel) systematic error.

## Archive format

**2026-09-05. Archive `.IMG` files are little-endian float32, despite
every label (PDS3 and PDS4) declaring `IEEE754MSBSingle`/big-endian.**
Discovered while fitting the paper's projection: frame
`JIR_IMG_RDR_2017033T114006_V02` read big-endian spans -2.6e38 to
3.1e38 with 143 NaNs; read little-endian it spans -0.00017 to 0.5547
W/m^2/sr/um, matching the published map's non-zero range (0.0060 to
0.5545) (`docs/reports/paper_projection_fit.md`, "Byte order of the
calibrated RDR frames"; corroborated independently in
`docs/reports/lm_half_order.md`, "Byte order (a prerequisite the spec
got the other way round)", where all 32 orbit-24 frames scored read
correctly only little-endian). Enforced everywhere image data is read:
`stacks.read_frame_image`, `strips.py`, the fit and tracking scripts
all use `'<f4'`. This is a project-wide rule now (`AGENTS.md`), not
just a one-off fix -- the original crawl-index spec (before this was
discovered) still says "big-endian" and is superseded on this point.

**2026-09-05. In a 256-line dual-band (`SCI_I1_*`, `band == "LM"`)
product, the top 128 lines are the L band and the bottom 128 are the M
band** (`geometry.LM_HALF_ORDER = ("L", "M")`). The SIS does not
document the storage order. The spec's own vote procedure
(`docs/specs/2026-09-04_geo_augment.md`, Part C) came in short of its
own 80%-of-decided-votes threshold (15/19 = 78.9% for "top = L"), but
the lead's review of the same evidence found the procedure, not the
data, at fault: it charges a hypothesis for the *entire* on-planet
fraction of a band whenever that band is dark, which is exactly what
happens for L on the nightside; scoring each half against each band's
footprint directly instead gives the bottom half matching the M-band
footprint at a median mismatch of 0.020 versus 0.27-0.39 for every
other pairing, and the instrument kernels (`juno_jiram_v02.ti`,
`juno_v12.tf`) independently place L on detector lines 1-128 and M on
139-266, matching the labels' own `SAMPLE_FIRST_PIXEL` (`docs/reports/
lm_half_order.md`, "Conclusion" and "Lead decision"). Enforced by the
`LM_HALF_ORDER` constant in `geometry.py`, consumed by every module that
splits a 256-line array (`stacks.py`, `strips.py`).

## Sequence definition

**2026-09-04. A sequence is a run of same-`(orbit_dir, band)` frames in
time order, broken whenever the gap exceeds 45 s or `sequence_number`
fails to strictly increase; the archive is 1-based and never delivers
frame 1 of a sequence** (`seq_n` always equals `sequence_samples - 1`).
The missing-frame-1 fact was measured during archive reconnaissance
(`docs/build_log_2026-09-04.md`, "Facts" after step 3d) and is exact,
not an artifact of mirroring: every sequence in the index is one frame
short of what its own label claims. Enforced in `index.assign_sequences`
(the segmentation rule and the `seq_id`/`seq_index`/`seq_n`/`seq_gap_s`
columns, spec in `docs/specs/2026-09-04_crawl_index.md`) and tested in
`tests/test_index.py`.

## The paper's map grid

**2026-09-05. The published perijove-4 polar maps are an orthographic
projection of the ellipsoid onto the body-fixed equatorial plane, not
the polar stereographic the JIRAM literature (Dinelli et al. 2017,
Adriani et al. 2020) and the label's own `MPROJ=4` were both assumed to
mean.** Fitting all seven candidate radial laws that are true-to-scale
at the pole to the 48 frames' footprint centroids, the orthographic law
on the ellipsoid wins decisively: centroid RMS 0.339 px versus 0.828 px
for stereographic, and it is the only candidate that simultaneously
drives the fitted axis anisotropy to ~1e-4, lands the pole and central
meridian on (near-)integer pixel/degree values, and recovers the
label's own `MPS=15` km/px exactly. Registering six frames against the
best stereographic grid instead gives shifts up to 1.04 px and NCC down
to 0.957, against 0.85 px and 0.985 (worst frame) for orthographic over
all 48 (`docs/reports/paper_projection_fit.md`, "Result" and "How the
radial law was identified"). In effect the map *is* the body-fixed
equatorial plane: `line = 1800 + x/15`, `sample = 1800 + y/15` (km),
with no projection mathematics in it at all. `MPROJ=4`'s true meaning in
the MIPL projection-code table was never found (`docs/reports/
vicar_map_projection_notes.md` -- the MIPL documentation site is
offline); "4 = orthographic" is the working conclusion from the fit,
not a confirmed table lookup. Enforced by `reproject.PAPER_GRID` (a
`PolarStereo` instance with `projection="orthographic"`) and by
`regions.py`'s `polar_ortho` rule, which generalises the same law to
any pole and any central meridian.

## Strip library

**2026-09-05. Resolution classes: `(2, 3, 5, 7, 10, 15, 20, 30, 50, 70,
100, 150, 200, 300)` km/px, log-nearest snap (ties to the smaller).**
Chosen so that two strips of the same class can be compared spectrum-
to-spectrum or structure-function-to-structure-function without
resampling either -- the point of quantising resolution at all
(`docs/specs/2026-09-05_strips.md`, "Grid per chunk"; module docstring
of `strips.py`). Enforced by `strips.RESOLUTION_CLASSES` and
`resolution_class()`.

**2026-09-05. Strip chunking rule: a chunk breaks whenever the
boresight has moved more than 12 deg from the chunk's first frame, or
the cadence gap exceeds 120 s; chunks under `--min-frames` (default 2)
are dropped.** Chosen because one nominal spin sequence can itself slew
tens of degrees, which would otherwise force geometrically unrelated
looks onto one grid (`docs/specs/2026-09-05_strips.md`, "Definitions").
Enforced by `strips.CHUNK_SEPARATION_DEG`, `CHUNK_GAP_S`, and
`chunk_table()`.

## Statistics conventions

**2026-09-05. `stats2d.py` adopts the downstream velocity-retrieval
model's numerical conventions (`conventions-v1`,
`~/scratch/goflow_rnd/docs/conventions.md`) verbatim, with two stated
departures.** (1) The conventions document defines the isotropic
spectrum with the kinetic-energy factor `0.5*|u_hat|^2`; JIRAM strips
carry radiance, not velocity, so the factor is dropped and `E`
integrates to variance instead. (2) The conventions document restricts
the isotropic spectrum to shells inside the Nyquist disc and drops the
partial corner shells; `stats2d.py` instead returns every shell out to
the lattice corner (flagged `inside_disc`) because the module's own
spec calls for a population counting that "covers every lattice point"
and dropping the corner shells would break the exact Parseval identity
the offline tests check (`src/jiram_catalog/stats2d.py`, module
docstring, "Two departures from the conventions document are
deliberate"). A third, separately logged caveat is not a departure but
a limitation: the periodogram's variance correction (`valid_frac *
window_power`) is exact only when the mask is uncorrelated with the
taper and with the field, and masking a strip convolves the true
spectrum with the mask's own spectrum -- smooth, few, large holes
distort a power-law slope only mildly (measured: -2.97 unmasked vs.
-2.92 with 15% masked coverage in the offline tests,
`docs/build_log_2026-09-04.md` step 9a), but no scalar correction
repairs speckle-like masking. Enforced throughout `stats2d.py`
(`power_spectrum_2d`, `isotropic_spectrum`); see `docs/data_products.md`
for the units this implies.

## GUI

**2026-09-06. GUI rebuilt as a FastAPI backend and a React/deck.gl
front end** after the owner's test of the Panel version (colour maps
not applying, no repaint, movie not shown, no hover on rasterised
points, aspect drift, confusing send-to); the Panel version was removed
the same day. Enforced in `gui_cmd.py`, `src/jiram_catalog/api/`,
`frontend/`.

## Documentation and process

**2026-09-05. Shared documentation names no AI product or vendor.**
Stated in this spec (`docs/specs/2026-09-05_documentation.md`, "No
executor product names anywhere") and consistent with the orchestration
overlay's rule that shared docs stay tool-neutral. Enforced by this
document set itself: a case-insensitive scan of `README.md` and every
`docs/*.md` file for any executor's product name is part of this pass's
own validation, and must return no matches.
