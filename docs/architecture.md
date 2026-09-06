# Architecture

This describes the system as built, not as planned; where the plan
changed (e.g. the paper's map turning out to be orthographic rather
than stereographic), the built version is what is documented, with a
pointer to `docs/decisions.md` for why. Every module referenced below
is under `src/jiram_catalog/`; every fact here traces to a spec, a
report, or the module's own docstring.

## The instrument, in one paragraph

JIRAM is a two-channel infrared imager on Juno: an L-band channel
(centred 3.455 um, 290 nm wide -- H3+ auroral emission at the poles, not
reflected sunlight) and an M-band channel (centred 4.780 um, 480 nm wide
-- thermal emission from ~5-6 bar depth through gaps in the ammonia
cloud deck, day and night; Adriani et al. 2017, quoted in
`docs/reports/juno_mission_facts.md`), each a 128 (lines) x 432 (samples)
detector, sharing one optical path and one 12-deg/s despun scan as Juno
spins. Some products deliver one
band alone (128 lines); others (`SCI_I1_*` mode) deliver both bands
stacked into one 256-line array with the 10 gap lines between them
removed -- see "L top, M bottom" in `docs/decisions.md`. The archive
calls a 128-line product's band `L` or `M` and a 256-line product `LM`.

## Layers, in build order

### 1. Archive mirror (`pds.py`, `mirror.py`)

`pds.py` parses the PDS4 archive's Apache directory listings
(`data_calibrated/orbitNN/`) into `manifest.parquet` -- every file the
archive has, one row each, before anything is downloaded. `mirror.py`
downloads labels and image data with `wget` (one process per orbit, a
small thread pool across orbits), verifying completeness by declared
size (`FILE_RECORDS * RECORD_BYTES` from the sibling label) and,
optionally, MD5. Labels are cheap (about 700 MB archive-wide) and are
mirrored for every orbit; image data (~21-25 GB) is mirrored
orbit-by-orbit as later milestones need it -- see the caveat on
`img_present` in `docs/data_products.md`.

### 2. Label index (`labels.py`, `index.py`)

`labels.py` parses one PDS3-syntax `.LBL` file (via `pvl`, with a
fallback grammar) into a flat dict; a label that fails to parse still
gets a row, with `parse_ok=False` rather than aborting the run. `index.py`
runs this over every mirrored label (a multiprocessing pool) into
`frames.parquet`, then segments frames into **sequences**: a spin
sequence is Juno's spacecraft-frame stare pattern, one frame every few
seconds. The segmentation rule (applied per `(orbit_dir, band)` group,
sorted by time) starts a new sequence at the first row and whenever the
gap since the previous row exceeds 45 s, or `sequence_number` fails to
increase. This is necessary because the archive's own bookkeeping is not
reliable across a gap: **`sequence_number` is 1-based and the archive
never delivers frame 1 of a sequence** (`seq_n` always equals
`sequence_samples - 1`) -- see `docs/decisions.md`.

### 3. SPICE geometry engine (`geometry.py`, `kernels.py`)

The label carries geometry for only a minority of frames (see below), so
a vectorised SPICE engine computes it for every frame from scratch:
per-pixel planetocentric latitude, longitude, range, emission,
incidence and phase, at the label's `START_TIME` exactly (Juno spins at
12 deg/s, so a 0.1 s epoch error moves the boresight by more than a
degree -- there is no per-pixel exposure offset). The engine is
**mirror-blind**: it does not model JIRAM's de-spinning mirror at all,
using only the spacecraft bus attitude (CK) and the static instrument
geometry (IK/FK); this was validated against 48 archive-labelled frames
before anything else was built (`docs/decisions.md`).

The method: pixel rays are built with a pinhole model in the band
frame (`pixel_directions`), rotated into `IAU_JUPITER`, and intersected
with the triaxial ellipsoid analytically (no per-pixel Python loop,
so a 128x432 frame takes tens of milliseconds). The one subtlety that
took two implementation passes to get right is stellar aberration:
`sincpt`'s `srfvec` and every pixel ray are *apparent* directions under
light-time-plus-stellar-aberration correction (`abcorr="LT+S"`), so both
must be converted to *geometric* directions with the same inverse
correction before the ellipsoid intersection -- correcting only one
leaves a systematic error of order `|v|/c` (about 7 arcsec, ~4 km on
the surface at perijove range). `reproject.py`'s inverse camera model
mirrors this chain exactly, re-applying the *forward* correction, so
that projecting a (lat, lon) back through the camera lands within
1.2e-5 px of the pixel the forward engine started from.

`kernels.py` resolves which SPICE kernels a set of frames needs (a
static set -- leap seconds, planetary constants, frame and instrument
kernels -- plus, per orbit, the reconstructed CK and SPK the archive
label names) and downloads missing ones from NAIF, retrying the PDS
SPICE archive mirror when the operational server's naming differs
(`spk_rec_*` vs `juno_rec_*`).

### 4. Geometry table (`geo.py`)

`jiram-catalog geo` runs the engine over every mirrored frame and writes
`frames_geo.parquet`: one row per (frame, band half), because a 256-line
`LM` product's two halves look at different parts of the planet and are
geometrically independent. This is the table nearly everything else in
the catalog reads, via `frames_with_geo()`, which joins it back to
`frames.parquet` on `product_id`. It exists because **label geometry is
absent for the majority of the archive**: orbits 39 and later
(`TARGET_PIXELS=0`, all geometry fields `"N/A"`) and most 256-line
dual-band products carry no centre latitude at all -- 64,139 of 85,108
frames, at the time this was measured. `docs/reports/lm_half_order.md`
and the geo consistency report (`<mirror>/index/geo_report.md`) are
where the engine was checked against the labels that do have geometry;
see `docs/decisions.md` for the numbers.

### 5. Reprojection (`reproject.py`, `vicar.py`)

Two independent things live here. `vicar.py` reads JPL VICAR files
(the format of the published perijove-4 maps and TRACKER4 vector
tables): a fixed-length ASCII label followed by raw image records, with
`FORMAT`/`INTFMT`/`REALFMT` giving dtype and byte order. `reproject.py`
is the map machinery: `PolarStereo` describes a plane tangent to a pole
(despite the name, it also implements the orthographic law that turned
out to be the right one for the published maps -- see
`docs/decisions.md`); `project_to_pixels` is the *inverse* camera model,
taking a (lat, lon) to the fractional detector pixel that saw it, by
running the geometry engine's forward chain backwards; `reproject_frame`
combines the two into exact inverse-mapping resampling (for every output
pixel, ask which input pixel it came from, so no output pixel is left
unpainted and no input pixel is scattered twice). `PAPER_GRID` is the
`PolarStereo` instance fitted, once, to the 48 published perijove-4
maps (`docs/reports/paper_projection_fit.md`); it is the geometric
definition of one entry in the region registry below, not a special
case in the code.

### 6. Regions and stacks (`regions.py`, `stacks.py`, `movie.py`, `export_goflow.py`)

This is **regime 1**, for places JIRAM revisits: the polar caps.
`regions.py` defines named rectangular grids over Jupiter -- `polar_ortho`
(the paper's own rule, generalised to either pole and any central
meridian) and `local_ortho` (a tangent-plane orthographic patch centred
anywhere) -- in `configs/regions.yaml`. `stacks.py` selects every frame
that overlaps a region (a cheap bounding-box prefilter against
`frames_geo.parquet`, then an exact per-pixel test), reprojects each
onto the region's grid, and lays the results along a time axis as a
NetCDF stack with per-pixel validity and emission angle. `movie.py`
renders a stack frame-by-frame with one whole-stack stretch (so a
navigation problem shows up as scene wobble, not as a per-frame
brightness jump); `export_goflow.py` cuts a stack into constant-cadence
runs and writes them in the layout the downstream optical-flow model
expects (`docs/reports/goflow_summary.md` -- 3 consecutive frames,
`(frame, y, x)`, invalid pixels zero-encoded, a `valid` mask, a
`loggrad` channel, no ground truth).

### 7. Strip library (`strips.py`)

This is **regime 2**, for everywhere else. Outside the poles JIRAM does
not revisit anything: a mid-latitude or equatorial target is seen once,
as a narrow swath from one spin sequence lasting a couple of minutes,
and never again at useful resolution. A time stack is the wrong shape
for that -- there is no time axis worth having -- so `strips.py` instead
builds a **library**: each spin sequence is split into *chunks*
wherever the boresight has walked more than 12 deg from where the chunk
started, or the cadence has broken for more than 120 s (a fast slew
inside one nominal sequence would otherwise be forced onto one grid and
blurred); each chunk gets its own `local_ortho` grid centred on its
boresight centroid, sized to its actual footprint, at a resolution
snapped to one of fourteen fixed classes (`docs/decisions.md`) so that
strips of the same class can be compared, spectrum to spectrum, without
resampling. The result is `strips.parquet` (queryable by latitude band,
epoch, resolution, illumination via `load_strips`) plus one NetCDF per
strip.

### 8. Statistics (`stats2d.py`)

Masked two-dimensional statistics for one strip or a population of
strips of the same resolution class: the 2-D periodogram and its
isotropic (shell) spectrum, 1-D spectra along each axis, structure
functions (orders 2 and 3), and a normalised bicoherence estimator. Every
diagnostic follows the same recipe -- mean over valid pixels only, fill
holes with that mean, taper the whole canvas, correct the periodogram
for the lost variance -- because a strip is a field with holes and the
diagnostics have to survive that. This module deliberately follows the
downstream model's own numerical conventions (`conventions-v1`: angular
wavenumbers, `norm="forward"` FFT, `C`-ordered arrays with y increasing
row index) verbatim except for two departures logged in
`docs/decisions.md`.

### 9. Trackability (`trackability.py`)

A pure numpy/pandas analysis, no imagery: for every pair of frames of
the same orbit and band half taken 90 s to 6 h apart with boresights
close enough to plausibly overlap, is the expected cloud displacement at
a reference wind speed resolvable at that frame's pixel scale? This
answers, per latitude band and per orbit, where the archive actually
supports velocity retrieval before any pixel is reprojected -- see
`docs/reports/trackability.md` and the totals in `docs/decisions.md`.

### 10. GUI (`src/jiram_catalog/api/`, `frontend/`, `gui_cmd.py`)

A three-tab browser application (Catalog, Poles, Strips) split across a
FastAPI backend (`api/`, JSON/Arrow/PNG under `/api`) and a React +
deck.gl front end (`frontend/`, built to `webapp/dist/` and served by
the backend at `/`) that holds all state and does all drawing in the
browser, one process serving both from the same origin. It reads the
products above and writes nothing except its own cache
(`<mirror>/gui_cache/`) and files a user explicitly exports; it computes
no science of its own, every number it shows is something a command-line
subcommand already wrote or could write. The split exists because the
first version kept state and rendering on the server and pushed rasters
to the browser, which produced synchronisation failures a client that
holds its own state and does its own drawing cannot have by
construction -- see `docs/gui_v2_notes.md` for the architecture and
design intent, `docs/specs/2026-09-06_api_contract.md` for the contract
the two halves share, and `docs/gui_usage.md` for running it.

## Cross-cutting: classical tracking and the validation gate (`tracking.py`)

`tracking.py` is not a pipeline stage -- it is how every stage above was
checked. A textbook template-matching tracker (normalised cross-
correlation, sub-pixel parabolic refinement, six masked window sums
computed as cost volumes so ~20,000 templates take seconds rather than
minutes) is run first on the *published* perijove-4 maps against the
*published* TRACKER4 vectors (does the tracker itself work?), then on
*our* reprojections of the raw archive against the same published
vectors (does the whole pipeline, camera model included, reproduce a
published result?). See `docs/reports/tracking_pj4.md` for the numbers;
this two-stage comparison is the project's standing acceptance test for
geometry and reprojection together.

## Module map

| module | role |
| --- | --- |
| `pds.py` | archive listing parser, manifest |
| `mirror.py` | wget-based download and verification |
| `labels.py` | PDS3 label parser |
| `index.py` | frame table, sequence segmentation |
| `geometry.py` | vectorised SPICE geometry engine, `KernelSet` |
| `kernels.py` | kernel manifest and download |
| `geo.py` | geometry augmentation table (`frames_geo.parquet`) |
| `vicar.py` | VICAR file reader (paper maps, `.tp4` vectors) |
| `reproject.py` | map grids, inverse camera model, resampling |
| `regions.py` | named region registry and grid math |
| `stacks.py` | region time stacks, frame selection, composites |
| `movie.py` | stack -> MP4/GIF |
| `export_goflow.py` | stack -> velocity-model input layout |
| `strips.py` | strip library: chunking, grids, NetCDF, index |
| `stats2d.py` | masked spectra, structure functions, bicoherence |
| `trackability.py` | repeat-view and displacement-resolvability analysis |
| `tracking.py` | classical template-matching tracker (validation tool) |
| `config.py` | mirror/paper-data path resolution |
| `config_cmd.py` | `config` subcommand |
| `api/` | FastAPI backend for GUI v2 (`app.py`, `catalog.py`, `stacks.py`, `strips.py`, `selections.py`, `jobs.py`, `images.py`, `arrow.py`) |
| `frontend/` | React + deck.gl front end (TypeScript, built to `src/jiram_catalog/webapp/dist/`) |
| `gui_cmd.py` | `gui` subcommand |
| `cli.py` | argparse entry point, wires every subcommand together |

Files not owned by any milestone but read by several:
`docs/reports/goflow_summary.md` (the downstream model's input
contract), `configs/regions.yaml` (the region registry `regions.py`
loads), `tests/fixtures/pj4_ingersoll2022_map_labels.csv` (the 48-frame
ground truth every gate checks against).

## How one frame flows from archive to product

1. **Archive** -> `manifest.parquet` (listed) -> mirrored `.LBL`/`.IMG`
   under `<mirror>/pds4/...` (downloaded).
2. **Label** -> one row of `frames.parquet` (parsed; sequence assigned).
3. **SPICE** -> one or two rows of `frames_geo.parquet` (per-pixel
   geometry computed at `start_time`, reduced to boresight/corner/
   footprint summaries).
4. From here the frame's path depends on the regime:
   - *Polar / repeat-view*: selected into a region (`select_frames`),
     reprojected onto the region's fixed grid (`reproject_frame`),
     written into a time stack (`build_stack`) or a sequence composite
     (`composite_sequences`), optionally rendered as a movie or cut into
     goflow realizations.
   - *Everywhere else*: grouped into its spin-sequence chunk
     (`chunk_table`), reprojected onto a `local_ortho` grid centred on
     that chunk (`build_strip`), written as one strip and one row of
     `strips.parquet`; later read back by `stats2d.strip_statistics` for
     spectra and structure functions.
5. **GUI**: every one of the files above is what the Catalog, Poles and
   Strips tabs display; nothing is recomputed for the GUI that the
   command line does not already produce.

## The two regimes, side by side

| | polar / repeat-view (regime 1) | everywhere else (regime 2) |
| --- | --- | --- |
| product | region time stack | strip library |
| grid | fixed per region, shared across frames | one per chunk, centred on that chunk |
| time axis | real (multiple visits) | none (one look) |
| revisited by | `region-stack`, `movie`, `export-goflow` | `strips`, `strip-stats` |
| designed for | velocity retrieval at a cadence | distribution-level statistics (spectra, structure functions) across many independent looks |
| ground truth | Ingersoll et al. (2022) PJ4 maps and TRACKER4 vectors | none published; internal consistency only |
