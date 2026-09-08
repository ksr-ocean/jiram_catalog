# A pedagogical review of `jiram_catalog`

This document is for one reader: an ocean modeler, fluent in numerical
methods, spectral analysis, and machine learning, who did not write
this code, has not used SPICE before, and wants to understand the
design and the implementation well enough to modify anything in the
repository. It explains terms once, on first use, and afterward
assumes you remember them (the glossary in section 8 is there for
lookups, not for first exposure). It is unhurried: where a design
choice cost two implementation passes, or a "fact" from the mission
literature turned out to be wrong, that is written up as its own
paragraph, not folded into a footnote, because the lesson usually
lives in the mistake, not in the final formula.

Every number in this document comes from a report, the build log, a
gate, or a measurement made while writing it, and the source file is
named inline next to the number. Where the code and a document
disagree, the code is treated as ground truth and the disagreement is
called out explicitly — this happens at least twice below (`orbit`
vs. `orbit_dir` in the frame index; the exact wording of one archive
fact that a later measurement revised). No claim here should need you
to trust prose over `git blame`.

The implementation described here is **commit `1e2723d`**, through
2026-09-07. It includes the intervening JunoCam acquisition, geometry and
photometry work and the scientific-workspace implementation. Earlier JIRAM
measurements remain validation history, not a fresh census. The new deep
dives are [4.18, JunoCam](#418-junocam--archive-camera-and-quality-evidence)
and [4.19, scientific workflow](#419-scientific-workflow--what-the-data-can-support).
Current counts and verification are recorded in the
[delivery log](docs/build_log_2026-09-07.md).

## Table of contents

1. [What the tool is for](#1-what-the-tool-is-for-and-the-two-regimes)
2. [Three archive facts that shaped everything](#2-three-archive-facts-that-shaped-everything)
3. [Architecture](#3-architecture)
4. [Deep dives, module by module](#4-deep-dives-module-by-module)
5. [Testing philosophy](#5-testing-philosophy)
6. [How it was built](#6-how-it-was-built)
7. [How to extend it](#7-how-to-extend-it-a-worked-example)
8. [Glossary](#8-glossary)

---

## 1. What the tool is for, and the two regimes

The project began with Juno's JIRAM instrument, a small two-channel
infrared camera on a spacecraft that spins twice a minute. Its eccentric
polar orbit gives a short close encounter with Jupiter followed by a long
departure. During a pass, JIRAM produces hundreds to thousands of
128×432-pixel band images: narrow looks at cloud-top thermal emission or
aurora. The original archive census contains 85,108 camera-frame labels
(`docs/build_log_2026-09-04.md`). For much of that archive the label does not
supply usable surface coordinates. Reconstructing where a pixel looked,
from spacecraft trajectory, attitude and camera geometry, is the first
problem this tool solves.

The current system also reads **JunoCam**, a visible and near-infrared
pushframe camera. One native JunoCam product contains multiple timed camera
readouts, each carrying one or more filter strips. Its archive layout,
detector arithmetic, timing and health evidence have their own modules.
The two instruments meet at the mapped-product and browser interfaces;
they do not become physically interchangeable there. Sections 2–4.17 retain
the original JIRAM foundations, with instrument-specific qualifications;
section 4.18 explains the second camera.

The second problem is organizing the observations into forms useful for
fluid dynamics. A single image can support spatial diagnostics, but cannot
supply a motion pair or a time series. The original design therefore splits
into two regimes. Think of the distinction as one between a model time
series at fixed grid cells and a collection of individual survey transects.

**Regime 1 — a region observed repeatedly.** JIRAM's polar sequences can
revisit the same atmospheric region. The project's reference is perijove 4
(2017-02-02), whose 48 archived frames underlie four published mosaics and
TRACKER4 wind vectors from Ingersoll et al. (2022). A **region time stack**
puts contributing frames or composites onto one fixed map grid, with
observation times, validity and viewing geometry. The repeated views are a
scientific opportunity; regular cadence, adequate overlap and navigation
accuracy must still be demonstrated for the particular selection.

That qualification matters immediately for JunoCam. The preserved local
polar file originally had six entries, but two processing versions of each
of three observations do not give six independent times. After version
selection it has three observations with approximately 577- and 243-second
gaps. It is viewable, but cannot supply the default constant-cadence triple.
These are current measured results, recorded in
[the delivery log](docs/build_log_2026-09-07.md), not a claim about all
possible JunoCam passes.

**Regime 2 — individual swaths.** Much of the useful nonpolar JIRAM sampling
is a sweep across cloud over one to a few minutes, followed by departure.
A library of locally projected swaths therefore makes more sense than
inventing a regular time axis. This does not establish that every location
is seen only once for the whole mission: `trackability.py` tests repeat-view
geometry, and JunoCam has its own schedule. An absent repeat assessment
means *unassessed*, not an observed absence of a partner.

A **strip library** stores these looks on local map grids with their source
identities and times. It supports spectra and structure functions of single
swaths, followed by comparisons across observations or passes. A large
library does not by itself supply a large number of independent realizations;
overlapping strips and reprocessed versions must be accounted for.

| | repeated-region product | individual-swath product |
| --- | --- | --- |
| stored form | region time stack | strip library |
| grid | shared region grid | local grid for each swath/chunk |
| temporal support | explicit times; cadence and overlap tested | source times retained, no stacked image time axis |
| builders | instrument-specific `region-stack` | instrument-specific `strips` |
| analysis | preflighted motion-model inputs and comparisons | spatial statistics and independent-pass summaries |
| reference evidence | validated JIRAM PJ4 maps and vectors | synthetic statistical checks; no general published swath truth |

The browser exposes five task views: **Explore**, **Time series**,
**Image library**, **Compare**, and **Coverage**. Those names describe what
you do; the two product regimes describe what the files mean. Keep both in
mind when modifying a module: first identify the physical observation, then
the map product, then the question its temporal and spatial support can answer.

---

## 2. Three archive facts that shaped everything

Three facts about the **JIRAM archive** itself — none of them documented
anywhere the project could find *before* someone measured them
directly — forced most of the architecture below. Each one was found
the hard way, each one is enforced today by a specific line of code
(not just a comment), and getting any of them wrong silently corrupts
downstream geometry rather than raising an error. If you are about to
touch `labels.py`, `index.py`, `geometry.py`, or anything that reads a
raw `.IMG` file, read this section before you read the module itself.

### 2.1. JIRAM archive labels lose all geometry after orbit 38

The PDS3 label attached to every camera frame carries an
`M_BAND_PARAMETERS` and an `L_BAND_PARAMETERS` group, and *when they
are populated* they give you exactly the kind of geometry you would
want: `CENTER_LATITUDE`, `CENTER_LONGITUDE`, `EMISSION_ANGLE`, the
four corner extents, a pixel scale in metres. The label's own
`SPICE_FILE_NAME` list even tells you which kernels the archive's own
pipeline used to compute those numbers, which is what made it possible
to validate an independent geometry engine against them before trusting
it on anything the label *doesn't* cover.

The trouble is coverage. The full-archive label mirror and index,
built as one of the earliest milestones, turned up this finding
verbatim in the build log:

> KEY FINDING (2026-09-04 23:00): label geometry is ABSENT for orbits
> >= 39 (2021+, PDS_RDR_v10: TARGET_PIXELS=0, all geometry fields
> "N/A") and for most 256-line dual-band (I1 mode, band=LM, 28,457
> frames) products. 64,139/85,108 frames have no label centre.
> (`docs/build_log_2026-09-04.md`, step 3d/3e)

That is 75% of the archive with no usable label geometry at all —
and note that this is not confined to the recent orbits: even within
the earlier orbits that do carry it, a 256-line dual-band product
(`SCI_I1_*` mode, both bands stacked with the ten gap lines removed)
almost never has it either, because the archive's per-band groups are
only populated for single-band products. `docs/data_products.md`
states the resulting fraction plainly: label-carried geometry "is
present for only about 25% of frames" and is "kept only as a
cross-check against `frames_geo.parquet`," never as the primary
source.

**What it forced.** The label geometry could not be the tool's
foundation; it could only ever be a spot-check on a small, biased
subset. This single measurement is why the SPICE geometry engine
(`geometry.py`) and the augmentation table it feeds
(`geo.py` → `frames_geo.parquet`) exist as first-class, mandatory
infrastructure rather than an optional convenience: every downstream
module — `stacks.py`, `strips.py`, `trackability.py`, the GUI — reads
`frames_with_geo()`, the SPICE-derived table joined back onto the
label index, and none of them can fall back to label geometry when
SPICE fails, because for three out of every four frames there is
nothing to fall back to. The 25% that does carry label geometry stays
useful for exactly one purpose from this point on: a per-orbit
consistency report (`geo.py`'s `geo_report_text`, written to
`<mirror>/index/geo_report.md`) that answers "does our independent
computation of geometry agree with the archive's own pipeline, where
both exist?" — see section 4.6 for the numbers, and section 5 for how
that consistency check itself had to be fixed once it was built on
the wrong subset of frames.

### 2.2. JIRAM image files are little-endian, despite every label saying the opposite

The JIRAM PDS3 and PDS4 labels examined declare their calibrated image
data `SAMPLE_TYPE = IEEE_REAL` (PDS3) or `IEEE754MSBSingle` (PDS4) —
both mean big-endian IEEE 754 float32, unambiguously, in the archive's
own documented terminology. Believing the label is the natural first
move, and it is wrong.

This surfaced while fitting the published map's exact projection
(section 4.8), which needed to read a raw calibrated frame
(`JIR_IMG_RDR_2017033T114006_V02`) and compare it pixel for pixel
against the published mosaic:

> Discovered while fitting the paper's projection: frame
> `JIR_IMG_RDR_2017033T114006_V02` read big-endian spans -2.6e38 to
> 3.1e38 with 143 NaNs; read little-endian it spans -0.00017 to
> 0.5547 W/m^2/sr/um, matching the published map's non-zero range
> (0.0060 to 0.5545) (`docs/decisions.md`, "Archive format"; full
> account in `docs/reports/paper_projection_fit.md`, "Byte order of
> the calibrated RDR frames"). The very first four bytes of that file
> are `e5 17 ab 3d`, which decode to 0.0836 read little-endian and
> roughly -1.0e23 read big-endian — not a subtle difference.

A second, fully independent investigation happening in parallel (the
question of which detector half is which band inside a 256-line
product, section 2.3's cousin fact, discussed in section 4.6) hit the
identical wall from a different angle and reported it separately:
reading the label's declared big-endian order made every one of the
32 orbit-24 frames it sampled come out with a background of huge,
implausible values and scattered NaNs, while little-endian gave
sensible thermal radiances everywhere (`docs/reports/
lm_half_order.md`, "Byte order (a prerequisite the spec got the other
way round)"). Two people, two unrelated questions, the same
mis-declared byte order, found the same way both times: by checking
whether the numbers that come out are physically plausible, not by
trusting the label's field name.

**What it forced.** Native JIRAM radiance readers hard-code `'<f4'`,
rather than that label declaration. This is a JIRAM rule, not a universal
`.IMG` rule: `junocam/images.py` reads RDR as big-endian unsigned 16-bit
integers and EDR as unsigned 8-bit counts. The JIRAM convention is recorded
in `docs/decisions.md`; `stacks.read_frame_image`,
`strips.py`'s frame reads, the projection-fit and tracking scripts all
do this identically. The original crawl-index spec, written *before*
this was discovered, still says "big-endian" in its own text and is
explicitly superseded on this one point (`docs/decisions.md`) — a
useful reminder that in this repository, when a spec and a later
report disagree, the report (and the code) wins, and the spec is left
as a historical record rather than silently edited.

### 2.3. JIRAM sequence numbering is one-based, and frame 1 is absent

The JIRAM camera-frame labels carry `SEQUENCE_NUMBER` (this
frame's 1-based position within its spin sequence) and
`SEQUENCE_SAMPLES` (how many frames the sequence is supposed to
contain in total). The natural assumption — that a sequence's frames
carry `SEQUENCE_NUMBER` values `1, 2, ..., SEQUENCE_SAMPLES` — is
false in a specific, exact, and unexplained way:

> archive sequence_number is 1-based and frame 1 is never delivered
> (`seq_n = sequence_samples-1` always). (`docs/build_log_2026-09-04.md`,
> "Facts" after step 3d)

That is: every single spin sequence in the archive is missing its
first frame, and the gap is not occasional data loss — it is total and
exact, `seq_n` (the count actually observed) equals
`sequence_samples - 1` (the count the label *claims*) with no
exceptions found. Nothing in the SIS (the instrument's 219-page
archive interface specification, checked directly — `docs/reports/
jiram_pds_research.md`) explains why; the working hypothesis is that
frame 1 of each commanded sequence is used by the spacecraft for
something that never gets archived (auto-exposure, a settling frame),
but this is inference, not a documented fact, and the codebase does
not depend on knowing the reason — only on not being surprised by the
consequence.

Combined with the fact that `SEQUENCE_NUMBER` is not reliable across a
real gap in the data (a dropped frame, an instrument reconfiguration,
or simply the boundary between two genuinely separate commanded
sequences can each look identical in the label's own bookkeeping), the
project could not simply trust `SEQUENCE_NUMBER` to define a sequence
boundary. `index.assign_sequences` instead defines a sequence purely
from observed behaviour, sorted in time order within each
`(orbit_dir, band)` group: a new sequence starts at the first row of
the group and again whenever the gap since the previous frame exceeds
45 seconds (the observed spin spacing is about 30.5 s, so one
missing intervening frame creates a roughly 61 s gap that crosses it), or whenever
`sequence_number` fails to strictly increase (`docs/decisions.md`,
"Sequence definition"; enforced in `src/jiram_catalog/index.py`,
`assign_sequences`). The resulting `seq_id`, `seq_index`, `seq_n`, and
`seq_gap_s` columns are the ones every later module — chunking in
`strips.py`, pairing in `trackability.py`, compositing in `stacks.py`
— actually relies on; `sequence_number` and `sequence_samples`
themselves survive in the index purely as label-carried bookkeeping,
consulted by nothing downstream.

**What it forced.** Two things, one obvious and one easy to miss.
Obviously: any code that assumes a sequence has `sequence_samples`
frames, or that frame 1 exists, is wrong by construction — a spin
sequence commanded for 13 frames delivers 12, always
(`tests/test_gate_pj4.py`'s own gate fixture checks this explicitly:
the n01a frame's label says `SEQUENCE_NUMBER = 2`,
`SEQUENCE_SAMPLES = 13`, and the sequence it belongs to has exactly 12
members in the index). Less obviously: because sequence identity has
to be *reconstructed* from timing and the (unreliable) sequence
number rather than read directly, every module that groups JIRAM frames by
sequence is implicitly trusting `index.assign_sequences`'s 45-second
threshold to have drawn the right boundaries — get that threshold
wrong for some future orbit with an unusual cadence, and every
downstream sequence-level product (composites, chunk grids, revisit
pairing) inherits the mistake silently, with no NaN or error to flag
it.

---

## 3. Architecture

This section describes the system as it was actually built, layer by
layer, in the order the milestones were delivered (`docs/
build_log_2026-09-04.md`); where the original plan changed along the
way — most dramatically, the published map's projection turning out
to be orthographic rather than the stereographic every published paper
about JIRAM calls it — the built version is what is documented here,
with a pointer to section 2 or 4 for the story of why.

### 3.1. Module map

Every module lives under `src/jiram_catalog/` unless noted. One
paragraph each, following the data journey. This is not a strict import
graph: shared normalization helpers, for example, live in `api/images.py`
and are also called by scientific code:

- **`config.py`** — resolves the two paths every other module needs
  (the local mirror, and the read-only published ground truth) through
  one precedence chain: explicit argument, then environment variable,
  then an optional TOML file, then a built-in default. Ordinary product
  workflows therefore need no hard-coded mirror path; see section 4.17.
- **`pds.py`** — parses the archive's own Apache directory listings
  (plain HTML, no API) into `manifest.parquet`, one row per file the
  archive has, before anything is downloaded.
- **`mirror.py`** — downloads labels and image data with `wget`,
  verifying completeness against the label's own declared size and,
  optionally, its MD5 checksum.
- **`labels.py`** — parses one PDS3-syntax `.LBL` file into a flat
  Python dict, using the `pvl` library with a fallback grammar, and
  never lets one malformed label abort a run.
- **`index.py`** — runs the label parser over an entire mirrored
  archive (a multiprocessing pool) into `frames.parquet`, then derives
  the sequence-identity columns described in section 2.3.
- **`geometry.py`** — the vectorised SPICE engine: per-pixel
  planetocentric latitude, longitude, range, emission, incidence, and
  phase for a JIRAM camera frame, computed from the pinhole camera model
  and an analytic ellipsoid intercept, with careful handling of light
  time and stellar aberration. Its ellipsoid and aberration helpers are
  shared with JunoCam; the second camera has a separate detector and
  timing model.
- **`kernels.py`** — resolves which SPICE kernel files a set of
  orbits needs (a small static set, plus per-orbit attitude and
  trajectory kernels named in the labels) and downloads whatever is
  missing from NAIF.
- **`geo.py`** — runs the geometry engine over every mirrored frame
  and band half, writing the one table (`frames_geo.parquet`) that
  almost everything downstream actually queries.
- **`vicar.py`** — a from-scratch reader for JPL's VICAR image format,
  needed only because the published ground-truth maps and wind-vector
  tables are shipped in it, not in anything PDS-native.
- **`reproject.py`** — the map machinery: a parametrised polar
  azimuthal grid (`PolarStereo`, despite the name it also implements
  the orthographic law the published maps actually use), the *inverse*
  camera model that runs the geometry engine's chain backwards, and
  exact inverse-mapping resampling.
- **`regions.py`** — the named-region registry: two projection rules
  (`polar_ortho`, `local_ortho`) built on top of `reproject.py`'s
  primitives, loaded from `configs/regions.yaml`.
- **`stacks.py`** — regime 1's core: selects every frame overlapping
  a region, reprojects each one, and assembles a time-stacked NetCDF
  with validity and emission-angle channels.
- **`movie.py`** — renders physical-band frames with the requested
  normalization and one whole-stack display stretch; streams MP4 frames
  and publishes finished output atomically.
- **`export_goflow.py`** — preflights band, grid, observation identity,
  cadence and common masks; writes image/mask/gradient realizations with
  native units, normalization and source provenance.
- **`strips.py`** — regime 2's core: chunks each spin sequence into
  geometrically coherent pieces, builds one tangent-plane grid per
  chunk, and maintains the resulting library's index.
- **`stats2d.py`** — masked two-dimensional statistics for one strip
  or a population of strips: periodograms, isotropic spectra,
  one-dimensional spectra, structure functions, bicoherence.
- **`trackability.py`** — a pure numpy/pandas analysis (no SPICE, no
  imagery) of which orbits and latitude bands have repeat-view
  geometry that could support velocity retrieval at all, before any
  pixel is ever reprojected.
- **`tracking.py`** — an independent classical cloud tracker used to
  test the JIRAM geometry-and-reprojection chain against published PJ4
  vectors. That reference does not validate arbitrary JunoCam navigation.
- **`junocam/`** — the second acquisition and geometry branch, from
  volume tables through integer images, distortion, frame timing and
  reprojection to banded stacks/strips. `quality.py` measures and describes
  image quality; `policy.py` decides whether evidence permits its use.
- **`science.py`** — physical-band selection, grid/cadence readiness,
  masks, spectral fits and provenance. **`api/science.py`** adds comparison,
  independent-pass populations, cross-instrument candidates and strict
  association of existing vectors (section 4.19).
- **`api/`**, **`frontend/`** and **`gui_cmd.py`** — FastAPI paired with
  React/deck.gl. Explore, Time series, Image library, Compare and Coverage
  expose the shared Python analysis kernels as well as product browsing.
  `api/coverage.py` distinguishes archive metadata from local eligible data;
  `api/io_guard.py` protects calls into the native NetCDF library.
- **`cli.py`** — the argparse entry point; wires every subcommand
  together, and is the one file only the project lead edits (see
  section 4.17).

`scripts/` holds one-off reconnaissance and validation scripts, each
tied to a specific report or gate (`fit_paper_projection.py`,
`classical_tracking_pj4.py`, `lm_half_order.py`,
`trackability_report.py`) — these are not part of the installed
package and are not imported by anything under `src/`.

### 3.2. How one frame flows from archive to product

```
JIRAM PDS4 archive (Apache HTML listings)
      |  pds.py: fetch_listing / parse_listing / build_manifest
      v
manifest.parquet                          (one row per archive file)
      |  mirror.py: wget per orbit, size + optional MD5 verification
      v
<mirror>/pds4/.../orbitNN/*.LBL *.IMG *.xml     (mirrored bytes)
      |  labels.py: parse_label (pvl, PDS3 grammar, never aborts)
      |  index.py: build_index -> assign_sequences
      v
frames.parquet                (one row per label; seq_id/seq_n/seq_gap_s)
      |  kernels.py: ensure_kernels (per-orbit CK/SPK from the label)
      |  geometry.py: frame_geometry (vectorised SPICE, per pixel)
      |  geo.py: build_geo  (1 row for L or M; 2 rows for a 256-line LM frame)
      v
frames_geo.parquet    (boresight, corners, footprint, on-planet fraction, ...)
      |
      +-------------------------------+---------------------------------+
      |   regime 1: polar/repeat-view |   regime 2: everywhere else     |
      v                               v                                 
select_frames (stacks.py)        unit_rows -> chunk_table (strips.py)
      |  reproject_frame               |  reproject_frame per chunk
      v                               v
region stack .nc (time,y,x)      strip .nc (y,x) + strips.parquet row
      |                               |
      +-- composite_sequences         +-- stats2d.strip_statistics
      +-- movie.py -> .mp4/.gif       +-- stats2d.population_statistics
      +-- export_goflow.py -> goflow realizations (constant-cadence triples)
      |                               |
      +---------------+---------------+
                       v
     Policy-aware API: Explore / Time series / Image library / Compare / Coverage
     Shared Python science; gui_cache/ for caches, selections and exports
     Requested stack builds write under regions/
```

JunoCam meets that product layer through its own branch:

```
volume discovery -> INDEX.LBL / INDEX.TAB / ERRATA.TXT
  -> manifest_files.parquet (all versions) + manifest.parquet (preferred)
  -> mirrored labels + unsigned integer EDR/RDR pixels
  -> junocam_images + junocam_quality + junocam_geo tables
  -> eligible/preferred observation selection
  -> per-frame camera geometry + optional limb timing refinement
  -> stack (time,band,y,x) or strip (band,y,x)
  -> selected physical band -> shared readiness / comparison / statistics
```

Metadata may remain available when pixel access is withheld. The policy must
therefore be applied again at direct product access; hiding a table row
alone is insufficient. The audited calibrated JunoCam bundle is a separate
reference source, not a replacement input in this diagram.

### 3.3. Where every product lives on disk

All paths are relative to the mirror root (`jiram-catalog config`
prints the one in effect):

| product | path | written by |
| --- | --- | --- |
| archive manifest | `manifest/manifest.parquet` | `manifest` |
| mirrored labels/images | `pds4/juno_jiram_bundle/data_calibrated/orbitNN/` | `mirror` |
| label index | `index/frames.parquet` | `index` |
| SPICE geometry | `index/frames_geo.parquet` | `geo` |
| geometry consistency report | `index/geo_report.md` | `geo` |
| SPICE kernels | `spice/{lsk,pck,fk,ik,sclk,spk,ck}/` | `kernels` |
| trackability tables | `index/trackability_pairs.parquet`, `index/trackability_frames.parquet` | `scripts/trackability_report.py` |
| region time stacks | `regions/<region>/<band>_orbits<spec>_<level>.nc` | `region-stack` |
| movies | wherever `--out` names | `movie` |
| goflow realizations | `regions/<region>/goflow_<band>_orbits<spec>/rNNNNN/` | `export-goflow` |
| strip NetCDFs | `strips/orbitNN/<strip_id>.nc` | `strips` |
| strip library index | `strips/strips.parquet` | `strips` |
| strip statistics | `strips/stats/<strip_id>_stats.nc` (CLI) or `gui_cache/stats_<strip_id>.nc` (GUI) | `strip-stats` / GUI |
| JunoCam volume records and manifests | `junocam/manifest/` | `junocam manifest` |
| JunoCam native bytes | `junocam/<volume>/<FILE_SPECIFICATION_NAME>` | `junocam mirror` |
| JunoCam image/quality/geometry tables | `junocam/index/junocam_{images,quality,geo}.parquet` | `junocam index`, `quality`, `geo` |
| JunoCam per-image geometry cache | `junocam/geometry_cache/<product_id>.nc` | `junocam.geometry` |
| JunoCam stacks | `regions/<region>/junocam_<bands>_orbits<spec>_frame.nc` | `junocam region-stack` / GUI |
| JunoCam strips | `strips/junocam/orbitNN/<product_id>.nc` | `junocam strips` |
| Calibrated-collection audit samples | `junocam/calibration_review/` | bounded audit, not native indexing |
| GUI research cache | `gui_cache/research/` | movies and population recipes/results |
| GUI cache and exports | `gui_cache/`, `gui_cache/exports/` | GUI selections, statistics, jobs and explicit exports |

(schema details for every one of these files are in
`docs/data_products.md`, cross-checked there against the real files on
the mirror; this table only says where they live.)


---

## 4. Deep dives, module by module

Each subsection follows the same shape: what the module is for; the
data structures that matter; the algorithm, with equations where the
module has them; two or three things a modifier absolutely needs to
know before changing the module; the validation gate that watches it
and what specifically it protects against; and the scars — the dead
ends, the wrong first attempts, the things that took two tries.

### 4.1. `pds.py` and `mirror.py` — archive mirror

**Purpose.** Turn the archive's own Apache directory listings into a
machine-readable manifest (`pds.py`), then download exactly the files
that manifest says are needed, verified against the label's own
declared size and (optionally) MD5 (`mirror.py`). Nothing downstream
ever talks to the network again once these two have run.

**Key data structures.** `manifest.parquet`: one row per archive file,
`orbit`/`basename`/`ext`/`family`/`kind`/`product_id`/`url`/
`listed_size_bytes`/`listed_mtime` (9 columns, `docs/data_products.md`).
`family_for_basename` classifies a file by filename prefix alone
(`JIR_IMG_RDR_` → `IMG`, `JIR_SPE_RDR_` → `SPE`, and the two `LOG_`
variants) — this project indexes only the `IMG` family for v1
(`docs/decisions.md`, "Camera frames only for v1"), but the manifest
still lists every family so a future extension to the spectrometer
products is a one-line filter change, not a re-crawl.

**Algorithm.** `pds._ListingParser` is a small hand-written
`html.parser.HTMLParser` subclass that walks Apache's
`mod_autoindex`-generated `<table>` markup row by row, pulling the
first `<a href>` of each `<tr>` and its trailing columns (date, size);
it deliberately ignores sort links (`href` starting with `?`),
parent-directory links (`/`), and directory entries (`href` ending in
`/`). Sizes are Apache's own K/M-rounded text, parsed by
`_listed_size` with a regex and treated everywhere as approximate.
`mirror._mirror_orbit` downloads labels before data for one orbit
(`is_complete` checks the exact declared byte count from the sibling
label, or the last line being `END` for a `.LBL`, or the trailing
`</Product_Observational>` for a PDS4 `.xml`), running one `wget`
subprocess per orbit via `ThreadPoolExecutor`, capped at 4 concurrent
jobs regardless of what `--jobs` asks for.

**What a modifier must know.**
- Every download goes through `wget`, never a Python HTTP client, and
  robots.txt under the archive's own `/PDS/` path only permits the
  `Wget` user agent — `pds.fetch_listing` spoofs the same user agent
  for the (Python-fetched) listings themselves for consistency, even
  though listings are not gated by the same robots rule.
- `mirror_files` never re-downloads a file that already passes
  `is_complete`; if you need to force a re-fetch, delete the file
  first (or use `mirror --verify`, which re-checks MD5 and
  re-downloads mismatches once).
- Completeness for a `.IMG`/`.DAT`/`.TAB` file requires its sibling
  `.LBL` to already exist and parse (`declared_data_size` reads
  `FILE_RECORDS * RECORD_BYTES` from it) — labels are always fetched
  first, precisely so this check can run.

**Validation gate.** `tests/test_gate_pj4.py` (read-only) checks the
downstream consequence of a correct mirror + index: the 48 published
perijove-4 frames are present, correctly typed, and their sequence
segmentation matches the paper's own four 12-frame groups. There is no
gate that inspects `mirror.py` in isolation — mirroring is validated
by whether everything built on top of it works.

**Scars.** Orbit listings are not uniformly small: orbit 50's listing
alone is roughly 36 MB of HTML, large enough to exceed a short socket
timeout on a slow day, which is why `pds.fetch_listing` retries with
exponential backoff (`min(60.0, 5.0 * attempt)` seconds) rather than
failing on the first transient error (`src/jiram_catalog/pds.py`,
`fetch_listing` docstring). Separately, the full-archive label mirror
run had eight files fail on the first pass and succeed on retry; the
retry evidently worked (today's mirror shows 85,108/85,108 labels with
`parse_ok=True`), but the build log never recorded *which* eight files
failed, so there is no artifact to audit against if a silent
corruption were ever suspected later (`docs/open_items.md`, "Kernel
and archive completeness"). This is listed as an open item rather than
fixed, because there is nothing to fix retroactively — only a lesson
for the next full-archive run to log more.

### 4.2. `labels.py` — PDS3 label parsing

**Purpose.** Turn one `.LBL` file into a flat Python dict of typed
values, tolerantly enough that a single malformed label never aborts
an archive-wide index build.

**Key data structures.** `FRAME_COLUMNS`, the ordered union of six
column groups (identity, time, archive bookkeeping, image shape,
spacecraft geometry, band-group geometry) that together define the
exact schema of `frames.parquet` — this list, not any per-function
return type, is the single source of truth for what a row looks like.
`_SCALAR_MAP` and `_GEOMETRY_MAP` are the label-keyword-to-column-name
dictionaries that do the actual translation (e.g.
`SUB_SPACECRAFT_LATITUDE` → `sub_sc_lat`).

**Algorithm.** `_load_pvl` calls `pvl.load` (a general-purpose PDS3
grammar library); if that raises, it retries once with `pvl.grammar.
OmniGrammar()`, a more permissive grammar, before giving up — either
way, it then independently checks that the last non-blank line of the
file is literally `END`, which is the one structural guarantee a valid
PDS3 label makes and catches truncated downloads that happen to still
parse. `parse_label` reads the two band-parameter groups
(`M_BAND_PARAMETERS`, `L_BAND_PARAMETERS`) and picks whichever one has
a numeric `CENTER_LATITUDE` as `geom_band` — this is a per-label
choice, independent of which band the *image data* actually is, and
the two can differ (a dual-band `LM` product's `geom_band` names only
one band, the one whose group happened to be populated). `band` itself
is derived separately, from `INSTRUMENT_MODE_DESC` text matching
(`"M-Band"` / `"L-Band"`) or from `lines == 256` implying `"LM"`.

**What a modifier must know.**
- A parse failure never raises past `parse_label`: the returned row
  still has `product_id` (from the filename, since the label itself
  may not have parsed far enough to give one), `parse_ok = False`, and
  `parse_error` set to the exception text. Any code that consumes
  `frames.parquet` must filter on `parse_ok` before trusting the rest
  of a row's geometry columns, which today are simply `NaN`/`None` for
  a failed row.
- `_number` maps the literal string `"N/A"` (case-insensitively) to
  `NaN`, and this is the *only* sentinel the archive uses for a
  missing numeric value; there is no separate "missing" vs. "zero"
  distinction to worry about.
- `img_present` is computed once, at parse time, by checking the
  sibling `.IMG` file's size against the label's own declared size —
  it is a snapshot, not a live check, and `docs/data_products.md`
  documents explicitly that a background mirroring job can make this
  column stale relative to what is actually on disk by the time you
  read it.

**Validation gate.** No dedicated gate; exercised through
`tests/test_labels.py` (offline, exact field-by-field checks against
the sample label in `docs/samples/`) and transitively through
`test_gate_pj4.py`.

**Scars.** None distinct from the archive facts of section 2, which
this module is where all three are actually enforced in code:
`_load_pvl` (byte order is irrelevant here — labels are ASCII text,
only the pixel data has the endianness problem), the `geom_band`
selection logic (a direct consequence of fact 2.1), and `spice_kernels`
parsing (feeds fact 2.1's replacement, the geometry engine, its
per-orbit kernel list).

### 4.3. `index.py` — the frame table and sequence segmentation

**Purpose.** Run the label parser over every mirrored label
(multiprocessing, one process per label file, default up to 8 workers)
into one combined `frames.parquet`, then compute the sequence-identity
columns over the *entire* combined table (this second step is
single-process and runs after the pool has finished, because sequence
boundaries depend on sorting across an entire `(orbit_dir, band)`
group, not on any one label in isolation).

**Key data structures.** `ARROW_TYPES`/`FRAME_SCHEMA`: the exact
PyArrow schema `frames.parquet` is written with, built once from
`labels.py`'s column-group constants so the two modules cannot drift
apart silently. `_orbit_dirs`: the directory-name-derived orbit list
used when `--orbits all` is requested, which unions "every orbit
directory that exists on disk" with "every orbit already in an
existing index" (so re-running `index` after a background mirror job
adds new orbits without you having to enumerate them by hand).

**Algorithm.** `assign_sequences` is section 2.3's segmentation rule,
implemented directly: sort each `(orbit_dir, band)` group by
`start_time`, and inside that group walk rows in order, starting a new
sequence whenever the gap to the previous frame exceeds 45.0 seconds
or `sequence_number` fails to strictly increase (including the case
where it is null). `seq_id` embeds the first frame's timestamp
(`%Y%jT%H%M%S`) so that two sequences from different orbits or bands
can never collide.

**What a modifier must know.**
- `orbit` (the label's own `ORBIT_NUMBER`) is stored as `float64`, not
  the `int16` the original spec called for, because 4,697 of 85,108
  rows have a null label value there and a nullable integer column
  round-trips through Parquet as float
  (`docs/data_products.md`, "Schema-check findings"). Use `orbit_dir`
  (the directory name the file was mirrored under; never null) for any
  orbit-based selection — every module in this codebase does, and
  `orbit` actively disagrees with `orbit_dir` on 3,900 rows, every one
  of them filed under `orbit03` with a label claiming
  `ORBIT_NUMBER = 1` (an archive property, confirmed by lead
  inspection, not a parsing bug — `docs/data_products.md`).
- `assign_sequences` is re-run over the *whole* table every time
  `index` is invoked for any subset of orbits, because a sequence
  boundary is a property of a sorted group, not of one row; this is
  why `build_index` reads the existing `frames.parquet`, replaces only
  the selected orbits' rows, and then re-derives sequence columns for
  the combined result rather than patching them incrementally.
- The multiprocessing pool here uses the default `fork` start method
  (there is no SPICE state to protect at this stage, unlike `geo.py`
  and `stacks.py` downstream), so `_parse_task` can be a plain
  module-level function without the `spawn`-and-cache dance those
  later modules need.

**Validation gate.** `tests/test_gate_pj4.py`, `test_
sequence_segmentation_matches_paper_groups`: the 48 paper frames'
`seq_id` values collapse to exactly 4 sequences (one per published
mosaic group), each frame's within-sequence gap is between 29.0 and
32.0 seconds (one spin period, never more than the 45 s threshold),
and the n01a frame's own label values (`SEQUENCE_NUMBER = 2`,
`SEQUENCE_SAMPLES = 13`) survive unchanged in the index even though
the *actual* sequence it belongs to has only 12 members — this is the
gate that directly encodes the "frame 1 never arrives" fact from
section 2.3.

**Scars.** None beyond section 2.3 itself, which lives here.


### 4.4. `geometry.py` — the SPICE geometry engine

This is the foundation of the JIRAM geometry pipeline. Getting its
physics slightly wrong can remain invisible until comparison against an
independent oracle, because downstream maps inherit its predicted
coordinates. JunoCam reuses its ellipsoid and aberration helpers, but
supplies its own camera model and frame epochs (section 4.18). Shared
geometry mathematics does not imply interchangeable instrument models.

**Purpose.** For any JIRAM camera frame — a 128×432 single-band image,
or a 256×432 dual-band one — compute, for every pixel, the
planetocentric latitude, east longitude, range, emission angle,
incidence angle, and phase angle of the point on Jupiter's ellipsoid
that pixel is looking at, at the frame's exact label epoch, using only
SPICE kernels (no image data, no per-pixel Python loop). This is a
*forward camera model*: pixel index in, predicted surface point out. Section 4.8
(`reproject.py`) is this module's inverse.

**Key data structures.** `KernelSet`: a small RAII-style wrapper
around `spiceypy.furnsh`/`spiceypy.unload` — SPICE's kernel pool is
global C-library state, not a Python object, so this class exists
purely to track which paths *this* code loaded, so it can unload
exactly those and nothing another caller loaded (`KernelSet.__init__`
furnsh's in order; `unload` unloads in *reverse* order; never calls
`spiceypy.kclear`, which would blow away kernels someone else in the
same process is depending on). `FrameGeometry`: a dataclass holding
every per-pixel array (`lat`, `lon_east`, `range_km`, `emission`,
`incidence`, `phase`, `on_planet`) plus scalar summaries (`boresight`,
`corners`, `sub_sc_lat`, `obspos_km`). `LM_HALF_ORDER`: a module-level
constant, `("L", "M")`, that says which detector half comes first in a
256-line dual-band product — this is section 4.6's "L-band residual"
story's cousin fact, discussed there.

**Algorithm.** Four steps, each worth walking through because each one
is where a naive implementation goes wrong in a different way.

*1. Pixel rays (the pinhole model).* `pixel_directions(lines, samples,
ifov_rad)` builds one unit vector per pixel in the *band frame*
(`JUNO_JIRAM_I_MBAND` or `_LBAND`, a SPICE-defined coordinate frame
fixed to the instrument), using the instrument kernel's per-pixel
instantaneous field of view (`INS-6141x_IFOV`, 237.767 microradians —
`docs/specs/2026-09-04_geometry_engine.md`, "Kernel facts"):

```
x = ifov * (lines/2 + 0.5 - line)      # 1-based line index
y = ifov * (samples/2 + 0.5 - sample)  # 1-based sample index
d = (x, y, 1) / |(x, y, 1)|
```

This is a plain pinhole projection — `x` and `y` are angles from
boresight along the two detector axes, and dividing by the norm turns
`(x, y, 1)` into a unit ray. It runs once, as one vectorised numpy
array operation over the whole `(lines, samples, 3)` grid, never per
pixel.

*2. Rotate into the planet's body-fixed frame.* SPICE gives you
`pxform(from_frame, to_frame, et)`, a 3×3 rotation matrix at a given
ephemeris time; the chain here is band frame → `J2000` (inertial) →
`IAU_JUPITER` (body-fixed, rotating with the planet). This sounds like
two matrix multiplies and would be, except for step 3.

*3. Light time and stellar aberration — the subtlety that took two
implementation passes.* The relevant light time is between Juno and
the observed point on Jupiter, not between Earth and Jupiter. Together
with the observer's motion, it means that what the instrument actually
points at ("apparent" direction) differs
measurably from where the target geometrically *is* right now
("geometric" direction) — light-time correction and stellar aberration,
the same effects Earth-based telescopes correct for when pointing at a
moving target. SPICE's `sincpt` (surface intercept) call, given
`abcorr="LT+S"`, returns everything already in *apparent* terms:
`srfvec` (the observer-to-surface-point vector) is apparent, and by
construction so is every pixel ray computed against the geometric
target directly. The engine needs the *geometric* ellipsoid intercept,
not the apparent one, so both the observer's effective position and
every pixel ray have to be converted from apparent to geometric with
the same inverse stellar-aberration correction before the ellipsoid
math runs:

```
obspos = base_point - aberrate(apparent_vec, v, inverse=True)
ray_geometric = aberrate(ray_apparent, v, inverse=True)
```

where `v` is the observer's velocity relative to the solar-system
barycentre (`spkezr(..., "NONE", "SOLAR SYSTEM BARYCENTER")`) and
`aberrate` is a vectorised re-implementation of NAIF's `stlabx`
(apparent → geometric) / `stelab` (geometric → apparent) pair, built
because those functions only take one ray at a time and this engine
needs to correct 55,296 rays per frame without a Python loop
(`geometry.aberrate`, tested against `spiceypy.stelab`/`stlabx` to
`1e-12` per component, `tests/test_geometry_offline.py`).

The original spec's algorithm applied this correction to the pixel
rays but used `srfvec` directly for the observer position — and the
executor implementing it found, empirically, that this left a
systematic per-pixel disagreement with the SPICE oracle:

> The geometry-engine spec's original step 3 used `srfvec` directly;
> the executor found this left a per-pixel error of 4e-3 deg against
> the oracle, and the corrected version (converting `srfvec` the same
> way as the pixel rays) reduced it to 8e-5 deg
> (`docs/decisions.md`, "Geometry conventions"; full account in
> `docs/specs/2026-09-04_geometry_engine.md`, "REVISED 2026-09-04").

That is a 50× reduction in error from correcting *both* halves of the
same physical quantity consistently instead of one. The size of the
mistake is not an accident: `|v|/c` at Juno's typical velocities is of
order 7 arcseconds, which projects to roughly 4 km on Jupiter's
surface at perijove range (`src/jiram_catalog/geometry.py`, module
docstring) — small enough to be easy to miss in a spot check, large
enough to fail a gate built on sub-pixel tolerances. `reproject.py`'s
inverse camera model has to re-apply the same correction in the
*forward* direction for exactly the same reason; skipping it there
leaves the identical systematic offset (section 4.8).

*4. The ellipsoid intercept, vectorised.* `ellipsoid_intercept(obspos,
dirs, radii)` solves, for a whole array of rays at once, the
textbook quadratic for where a ray from `obspos` in direction `dirs`
crosses a triaxial ellipsoid with semi-axes `radii`: scale everything
by `1/radii` (turning the ellipsoid into a unit sphere), solve
`|p + t·q|² = 1` for the smallest positive `t`, and take
`spoint = obspos + t·dirs` in the unscaled frame. A negative
discriminant or a non-positive root means the ray misses the planet or
the intercept is behind the observer, and comes back as `on_planet =
False`, `NaN` in every per-pixel field for that pixel — off-planet is
represented by absence, never by zero (`docs/data_products.md`,
"Conventions"). This runs as pure numpy broadcasting over the whole
frame, which is why a 128×432 frame's full geometry computes in tens
of milliseconds rather than the many seconds a per-pixel `sincpt` call
would cost.

**What a modifier must know.**
- **For JIRAM, the epoch is the label's `START_TIME`, exactly, with no
  exposure offset.** Juno spins at 2 rpm, 12 degrees per second
  (`docs/reports/juno_mission_facts.md`, primary-source quote from
  Adriani et al. 2017); a boresight computed even 0.1 seconds off
  moves by more than a degree, and computing it at mid-exposure
  (roughly +1.25 s for a typical M-band frame) rather than at
  `START_TIME` was measured, before any code was written, to differ
  from the correct answer by *tens of degrees*
  (`docs/build_log_2026-09-04.md`, step 1b "48-frame result").
  `frame_geometry(epoch, ...)` takes the epoch as a direct argument
  precisely so nothing upstream can silently add an offset; `geo.py`
  never does. JunoCam has a different acquisition model: its
  `frame_epochs` applies instrument timing terms and an optional refined
  offset to individual frames. Section 4.18 derives that expression.
- **The engine is deliberately mirror-blind.** JIRAM has a physical
  de-spinning mirror that counter-rotates to hold the line of sight
  still during each exposure and can be commanded to point ahead of or
  behind straight nadir within its one degree of pointing freedom
  (`docs/reports/jiram_pointing_overlap.md`, §1, quoting Adriani et
  al. 2017 and Grassi et al. 2018 directly). The engine models none of
  this — it uses only the spacecraft bus attitude (the CK kernel) and
  the static band-frame geometry (the IK/FK kernels), as if the camera
  were rigidly bolted to the bus. This was a decision made *before*
  writing any code, justified by measuring the residual it leaves: at
  `IMAGE_TIME`, mirror-blind SPICE matched all 48 orbit-4 label
  centres to a latitude/longitude median of 0.006 deg (max 0.31 deg,
  at 89 deg latitude where a degree of longitude is a much shorter
  physical distance), emission angle to 0.017 deg, and altitude to
  28 km (`docs/build_log_2026-09-04.md`, step 1b). That residual is
  small enough that the mirror's own commanded offset apparently
  contributes negligibly to where the boresight actually points — a
  genuinely non-obvious result, and one the project checked
  empirically rather than assumed.
- `frame_geometry` accepts `band="LM"` for a 256-line dual-band
  product; internally this computes the L and M halves as two entirely
  independent 128-line geometries (their own `sincpt` calls, their own
  ellipsoid intercepts) and stacks them in `LM_HALF_ORDER` order — the
  two halves see genuinely different parts of the planet through
  different band frames, so there is no shortcut that reuses one
  half's intercept for the other.

**Validation gate.** `tests/test_gate_geometry_pj4.py` (read-only,
needs the real mirror and kernels): boresight geometry for all 48
paper frames must agree with the label to within 0.02 deg
great-circle, 0.02 deg emission, 0.02 deg incidence, 50 km altitude,
0.03 deg minimum latitude; a separate parametrised test checks nine
sample pixels of three frames against a *fresh, independent* SPICE
oracle call (`sincpt`/`illumf` invoked directly, not through this
module) to 0.002 deg latitude/longitude and 0.01 deg for every angle;
and a timing test requires `frame_geometry` to complete in under one
second per frame. `docs/build_log_2026-09-04.md` records the actual
achieved oracle agreement as 8e-5 deg (worst pixel) at 35 ms per frame
— both comfortably inside the gate, with headroom the gate does not
require but the design does not need to sacrifice.

**Scars.** The stellar-aberration story above is the headline scar,
but it came at the end of a longer one: the mechanical executor
assigned this milestone was *correctly* dispatched twice, stopping
each time on a genuine arithmetic ambiguity in the spec rather than
guessing — exactly the behaviour the harness wants — and then, on a
third attempt, stalled for two hours and twelve minutes producing
nothing (`docs/agent_harness.md`, "Experience so far"). The lead then
routed the same spec to a stronger executor, which delivered a working
engine, including the aberration fix above, in sixteen minutes
(`docs/build_log_2026-09-04.md`, step 5'). This is discussed further
as a concrete data point in section 6.


### 4.5. `kernels.py` — SPICE kernel manifest and download

**Purpose.** Decide exactly which SPICE kernel files a given set of
orbits needs, check what the mirror already has, and download whatever
is missing — from the operational NAIF server first, falling back to
NAIF's own PDS-archived SPICE mirror (which renames some files) if the
first attempt fails.

**Key data structures.** `needed_kernels(frames)` returns a flat list
of `(kind, name)` pairs: a fixed static set (leap-seconds, planetary
constants, the one frame kernel and one instrument kernel this project
uses, the highest-numbered spacecraft-clock kernel currently listed at
NAIF, and three optional ephemeris SPKs used only if already present)
plus, per orbit, whatever date-specific attitude (`CK`) and trajectory
(`SPK`) files the frames' own labels name via `SPICE_FILE_NAME`. Only
two kinds of dynamic kernel are ever pulled from that label list — a
reconstructed CK (`*.bc`) and a reconstructed or predicted SPK
(`spk_rec_*`/`juno_rec_*`/`spk_pre_*`/`juno_pre_*`) — every other name
the label lists is ignored, superseded by the fixed static set.

**Algorithm.** `orbit_kernel_names` (in `geometry.py`, reused here)
scans every parsed label's `spice_kernels` field, classifies each
semicolon-separated name by suffix (`classify_kernel`), and keeps only
the CK/SPK ones. `resolve_present`/`spk_aliases` handle a genuine
naming inconsistency between NAIF's two distribution trees: the
operational server names a reconstructed trajectory kernel
`spk_rec_170106_170228_170307.bsp`, but the *identical* file lives in
the PDS SPICE archive renamed `juno_rec_170106_170228_170307.bsp` —
same coverage, same bytes, different prefix
(`docs/reports/spice_kernel_coverage.md`, "Task 5"). `spk_aliases`
generates both possible names for any SPK so that a file downloaded
under either name is recognized as present.

**What a modifier must know.**
- `naif.jpl.nasa.gov` fails TLS certificate verification against the
  *system* CA bundle on this cluster (a missing Sectigo root); every
  download is pinned to the `certifi` package's own bundle instead
  (`ca_bundle()`, used both for `wget --ca-certificate=...` and for
  Python's own `urlopen` calls in `latest_sclk_name`) — this is a
  cluster-environment fact, documented in `CLAUDE.md`, not a code bug.
- `KernelSet.for_orbits` (in `geometry.py`) is what actually *loads*
  the kernels this module downloaded; `kernels.py` itself never calls
  `furnsh`. If a needed kernel is missing on disk, `for_orbits` raises
  `FileNotFoundError` naming every missing file and suggesting exactly
  the `jiram-catalog kernels --orbits ...` command that would fetch it
  — a deliberately actionable error rather than a bare traceback.
- The PDS archive's kernel directories keep only the *current latest*
  version of each static kernel, not every historical version side by
  side: an old label naming `juno_jiram_v01.ti` will find only
  `juno_jiram_v02.ti` in a freshly crawled archive listing, because
  v01 has been removed upstream (`docs/reports/
  spice_kernel_coverage.md`, "Task 3", "Judgment call / gotcha"). This
  does not affect this project directly (it hard-codes the one
  instrument-kernel version it validated against), but it matters for
  anyone trying to reproduce a specific historical processing run
  byte-for-byte.

**Validation gate.** No dedicated gate file; exercised indirectly by
every gate that needs kernels loaded (`test_gate_geometry_pj4.py` and
everything downstream of it) and directly by the CLI validation block
in `docs/specs/2026-09-04_geometry_engine.md` (`kernels --orbits 4
--dry-run` must list every required file present except possibly the
optional ephemeris SPKs).

**Scars.** A dedicated reconnaissance pass before any kernel code was
written found six gaps longer than seven days in the archive's weekly
reconstructed-CK series over the full 2011–2026 span, each one covered
in the mission's own yearly metakernel by a lower-accuracy
`juno_sc_raw_*.bc` file instead of the normal reconstructed one
(`docs/reports/spice_kernel_coverage.md`, "Task 2"). This mattered
later: three orbits (38, 55, 70) fail geometry entirely or partially
for exactly this class of reason — a short attitude or trajectory gap
this project's kernel resolution logic does not yet fall back
through — and `docs/open_items.md` records that none of the three
failing dates actually falls inside one of the six gaps this earlier
report found, meaning the short gaps responsible for those three
orbits are still unidentified. `kernels.py`/`KernelSet.for_orbits`
today only resolves the exact reconstructed CK/SPK name the label
gives; it never substitutes a `_raw_`/`_pre_` file when the named one
is short. This is an acknowledged, open gap, not a silent one: see
section 4.6's discussion of the same three orbits from `geo.py`'s
side.

### 4.6. `geo.py` — geometry augmentation of the frame index

**Purpose.** Run the geometry engine over *every* mirrored frame and
band half — not just the labelled 25% — and write the one table
(`frames_geo.parquet`) that section 2.1 established as mandatory
infrastructure: `frames_with_geo()`, the join of this table back onto
the label index, is what nearly every downstream module actually
queries.

**Key data structures.** `GEO_COLUMNS`/`GEO_SCHEMA`: 38 columns, one
row per `(product_id, half)` — `half` is `"L"` or `"M"`, and a
256-line `LM` product contributes *two* rows, because (as section 4.4
noted) the two halves see genuinely different parts of the planet.
Beyond the boresight and corner intercepts, the table carries derived
summaries every downstream module reuses rather than recomputing:
`on_planet_frac`, the longitude arc (`lon_min_east`/`lon_max_east`/
`lon_span_deg`), `pole_inside`, `median_pixel_km` (the frame's ground
sample distance, `median(range_km * ifov_rad)` over on-planet pixels),
and `local_time_h` (boresight local solar time).

**Algorithm.** `build_geo` batches frames into chunks of 32 grouped by
orbit (`CHUNK_FRAMES`) and dispatches them to a `multiprocessing.Pool`
using the **`spawn`** start method — not the default `fork` — because
SPICE's kernel pool is C-library global state per process, and a
forked child would inherit a `furnsh`'d-looking handle table that is
actually unusable in the child. Each worker calls `_worker_kernels`,
which caches one `KernelSet.for_orbits(mirror, [orbit])` per worker
process and only reloads it when handed a different orbit — since
frames are grouped by orbit before dispatch, a worker typically
furnsh's kernels once and reuses them for its whole chunk, not once
per frame. `longitude_arc` computes the smallest longitude interval
containing a frame's on-planet footprint by unwrapping every longitude
about their circular mean (not their arithmetic mean, which fails
across the 0°/360° seam) and reporting the arc's low/high/span in that
unwrapped frame; an arc of 350 degrees or more, or a footprint whose
maximum latitude exceeds 89.5° (or minimum is below −89.5°), is
reported as the whole circle (`lon_min_east=0, lon_max_east=360,
lon_span_deg=360`) rather than a numerically meaningless "arc," because
a footprint that contains a pole genuinely has no well-defined
longitude range.

**What a modifier must know.**
- **A frame whose computation raises never aborts the run.** Every
  per-frame call is wrapped so that a SPICE exception (kernel coverage
  gap, numerical failure) is caught, truncated to 400 characters, and
  recorded as `geo_ok=False, geo_error=<text>` in that frame's row —
  the row still exists, with every geometric column `NaN`. This is
  what lets `geo --orbits all` complete even though three orbits fail
  entirely or partially (below).
- **The archive label's `CENTER_LATITUDE` is not the same concept as
  this table's `bore_lat` for a partial (limb) frame.** `bore_lat` is
  specifically the boresight's own ellipsoid intercept — `NaN` if the
  boresight itself misses the planet, even when other pixels of the
  same frame are on it. The label's centre, by contrast, behaves like
  a centroid of whatever *is* visible for a partially-off-planet
  frame. `tests/test_gate_geo_pj4.py`'s own consistency test names
  this directly in its docstring: it restricts its comparison to
  frames "fully on the planet with boresight emission < 40 deg," "the
  label centre is a visible-area centroid for partial frames, so those
  are excluded by construction." Comparing `bore_lat` to a label
  centre on a limb frame without this filter will look like a large,
  spurious geometric error that is really just two different
  definitions of "centre" disagreeing on a frame where they are not
  supposed to agree.
- Orbits **38** (2021-11-29) and **70** (2025-03-02/03) fail geometry
  entirely, and orbit **55** (2023-10-14) fails for 82 of 4,000 rows —
  and the two failure classes are different in kind, not just in
  orbit number. Reading the actual `geo_error` text (something no
  earlier report had done) distinguishes them precisely:
  `SpiceNOFRAMECONNECT` for orbits 38/55 ("insufficient information
  available to transform from reference frame ... `JUNO_JIRAM_I_MBAND`
  ... to reference frame ... `J2000`" — the attitude CK loaded for that
  orbit does not cover the frame's exact epoch), versus
  `SpiceSPKINSUFFDATA` for orbit 70 ("insufficient ephemeris data ...
  to compute the state of -61 (JUNO)" — the trajectory SPK does not
  cover the epoch) (`docs/open_items.md`, "SPICE geometry fails
  entirely for orbits 38 and 70, partially for 55"). One is a pointing
  gap, the other a position gap, and fixing them (see section 4.5's
  scar) would need different kernel substitutions.

**Validation gate.** `tests/test_gate_geo_pj4.py`: all 48 paper frames
have `geo_ok`, `on_planet_frac == 1.0`, boresight within 0.02 deg
great-circle of the label centre, `min_lat` within 0.03 deg,
`pole_inside=True` exactly where the label's `MAXLAT >= 89.5`, and
`median_pixel_km` between 10 and 40; a second test checks the
consistency-report medians on the well-conditioned subset described
above.

**Scars.** This is one of the two places in the codebase where a gate
was rewritten because it was measuring the wrong population, discussed
fully in section 5: the original all-orbit consistency numbers were
inflated by exactly the partial/limb-frame effect described above, and
the gate now filters to fully-on-planet, low-emission frames before
computing its statistics. Separately, an open question about accuracy
remains unresolved rather than papered over: reproducing the gate's
own well-conditioned filter for orbit 4 and splitting the boresight-
vs-label residual by band gives L median 0.0239 deg (p95 0.059,
n=277) against M median 0.0104 deg (p95 0.056, n=427) — the L-band
residual is roughly double the M-band one. M is inside the gate's
0.02 deg tolerance; L is slightly above it, but the gate applies that
assertion to M only — `docs/open_items.md`, "L-band geometry
is roughly twice as noisy as M-band against the label." Whether this
is a real property of the L-band instrument kernel (less well
characterised, since L is the lower-traffic auroral channel) or an
artefact of the L-band label comparisons available being more polar
and therefore more sensitive to the same angular error is explicitly
left open.


### 4.7. `vicar.py` — reading the ground-truth file format

**Purpose.** The published perijove-4 maps and the TRACKER4 wind
vector tables this project validates against are not PDS products at
all — they are JPL's older VICAR image format, self-contained files
whose label is a block of ASCII `KEY=VALUE` text at the *start* of the
file rather than a separate sidecar. This module reads that format
generically enough to serve both file types, which turn out to differ
in almost every structural parameter (label size, record layout, even
whether a second label appears *after* the data).

**Key data structures.** `read_vicar(path) -> (array, label)`. The
label is a plain dict, with one twist: some VICAR keys legitimately
repeat (`TASK`, recording each processing step a file has been
through), and `_store` promotes a repeated key to a list rather than
overwriting it, so a file's full processing history —
`JIR2VIC → APIMAP6E → COPY` for the maps, with `TRACKER4` appended for
the `.tp4` vector tables — survives intact.

**Algorithm.** The label's own `LBLSIZE` field (found by a regex
search of the first 256 bytes, before anything else about the file is
known) gives the exact byte length of the label block; everything
after that, minus any `NLB` binary header lines and `NBB` per-record
prefix bytes the label declares, is pixel data, laid out according to
`ORG` (`BSQ`/`BIL`/`BIP` — band-sequential, band-interleaved-by-line,
or band-interleaved-by-pixel) and read with a dtype derived from
`FORMAT` (`REAL` → float32, etc.) and byte order from `INTFMT`/
`REALFMT` (`LOW`/`RIEEE` → little-endian, `HIGH`/`IEEE` → big-endian;
`VAX` is explicitly unsupported and raises, since VAX floats are not
IEEE 754 and no file this project reads uses it). A `.tp4` velocity
table additionally sets `EOL=1`, meaning a *second*, shorter label
follows the data — `read_vicar` parses this trailing block separately
under the key `"EOL_LABELS"` rather than merging it into the main
label, since both blocks can legitimately define the same key
(`LBLSIZE` in particular) with different, non-interchangeable
meanings.

**What a modifier must know.**
- The two file types this project actually reads have wildly different
  label sizes — 12,800 bytes for the `.map` polar mosaics,
  1,728 bytes for the `.tp4` vector tables — and a hard-coded header
  size is exactly the bug the *published paper's own supplementary
  Python readers* had: `docs/reports/local_jiram_inventory.md` found
  that both `vicar_reader.py` and `vicar_pair_reader.py` in the
  paper's data folder assume a fixed 12,800-byte header, which
  silently misparses every `.tp4` file. `read_vicar` always reads
  `LBLSIZE` from the file itself, precisely to avoid inheriting that
  bug.
- VICAR labels are 1-indexed in spirit (line/sample numbering) but the
  reader itself never needs to know or care — it hands back a plain
  0-indexed numpy array in `(line, sample)` order, and any 1-based
  bookkeeping is the *caller's* responsibility (see section 4.12's
  discussion of the TRACKER4 tables' own 0-vs-1-based ambiguity, which
  this module deliberately leaves unresolved because the file format
  itself does not settle it).
- `read_vicar_label(path)` is exposed separately from `read_vicar` for
  callers (like the projection-fit script) that need the metadata —
  `IMAGE_TIME`, `CENLAT`, `MPS`, and so on — without paying for a
  3000×3200 array read.

**Validation gate.** No dedicated gate; exercised by
`tests/test_reproject_offline.py` (a synthetic VICAR file in both byte
orders, with `NLB`/`NBB` > 0, plus a real read of `n01a.map` checked
against its known nonzero pixel count, and a real `.tp4` file) and
transitively by every gate downstream that opens a published map or
vector table.

**Scars.** None specific to this module beyond the byte-order fact
documented in section 2.2, which this module's *dtype resolution* (not
its byte-order default — VICAR's own `INTFMT`/`REALFMT` keys are
trustworthy, unlike the PDS labels) correctly handles by reading
whatever the file's own label says, since VICAR's declared byte order
for these particular files is in fact accurate.

### 4.8. `reproject.py` — map grids and the inverse camera model

**Purpose.** Two things live in this module, and they compose. First,
a parametrised polar azimuthal map grid, `PolarStereo` — a plane with
a pole pixel, a scale in km per pixel, and a choice of radial law,
from which every named region in `regions.py` (section 4.9) is built.
Second, `project_to_pixels`, the exact algebraic *inverse* of
`geometry.py`'s forward chain: given a (latitude, longitude) on the
ellipsoid, which fractional detector pixel saw it? Composing the two —
ask the map grid what (lat, lon) a target output pixel represents,
then ask the inverse camera model which input pixel that corresponds
to — is `reproject_frame`, used by the original JIRAM product regimes.
`junocam/reproject.py` follows the same destination-grid strategy with
JunoCam distortion and frame timing.

**Key data structures.** `PolarStereo` is a frozen dataclass:
`pole_line`, `pole_sample` (0-based pixel coordinates of the
projection pole), `km_per_px`, `lon0_deg` (the east longitude running
along +sample from the pole), `clockwise`, `hemisphere`,
`radius_km`/`equatorial_radius_km`, and `projection` — a string
selecting which of four radial laws `rho(zeta)` (zeta = colatitude
from the pole) relates map distance to angular distance: stereographic
(`2R tan(zeta/2)`, conformal — locally correct pixel scale in every
direction, which is why it is the textbook default for planetary polar
maps), equidistant (`R·zeta`), Lambert equal-area
(`2R sin(zeta/2)`), and orthographic (the literal parallel projection
of the ellipsoid surface point onto the equatorial plane). All four
laws agree to first order in `zeta`, which matters enormously for what
follows.

**Algorithm — the inverse camera model.** `project_to_pixels(geo, lat,
lon)` runs `geometry.py`'s chain in reverse, term for term: convert
(lat, lon) to an ellipsoid surface point in `IAU_JUPITER`
(`surface_point`, the algebraic inverse of `planetocentric`), form the
geometric ray from the frame's stored `obspos_km` to that point,
rotate it `IAU_JUPITER → J2000` and then **re-apply the forward
stellar-aberration correction** (`aberrate(..., inverse=False)`) — the
engine removed aberration from its rays; the inverse model has to put
it back, using the identical observer-velocity source, before stepping
into the band frame — and finally invert the pinhole model itself
(`x = dx/dz`, `y = dy/dz`, then solve `pixel_directions`' formula for
line/sample). The result is a *fractional*, sub-pixel (line, sample):
this is what makes `reproject_frame`'s exact-inverse-mapping resampling
possible, rather than only ever landing on integer pixels. Skipping
the forward aberration re-application leaves the same order-`|v|/c`
systematic error section 4.4 measured on the forward side — about a
third of a JIRAM pixel (`src/jiram_catalog/reproject.py`, module
docstring) — which is exactly why the round-trip test
(`project_to_pixels(geo, geo.lat[i,j], geo.lon_east[i,j])` must return
`(i+1, j+1)` to within `1e-3` px for every on-planet pixel) is one of
the offline tests, and why the achieved round-trip residual, `1.22e-05`
px (`docs/reports/paper_projection_fit.md`, "Result"), is reported as
the single number that certifies the forward and inverse chains are
truly mirror images of each other, not merely close.

**Algorithm — exact inverse-mapping resampling.** `reproject_frame`
evaluates each destination pixel through the inverse map: for every *output* pixel inside a
padded footprint bounding box, it asks the map grid what (lat, lon)
that pixel represents, then asks `project_to_pixels` which fractional
input pixel that came from, then bilinearly samples the *input* image
there (`_bilinear`, which returns both a value and an `ok` mask —
`False` outside the detector, off the visible hemisphere, or wherever
any of the four bilinear neighbours is non-finite). This is the
standard graphics technique of resampling by walking the *destination*
grid and pulling from the source, rather than scattering into the
destination. This avoids holes caused solely by forward scattering;
invalid or off-detector neighbors still produce legitimate masked output.
Several map pixels may sample the same detector neighborhood. The cost is
one inverse-camera-model
evaluation per output pixel rather than one per input pixel (output
canvases here are typically larger than input frames, so this is the
more expensive direction, but the only one that avoids resampling
artefacts).

**The paper grid: the fit story, and the radius degeneracy.** The
published perijove-4 maps' own VICAR labels declare `MPROJ=4`, and
every piece of JIRAM literature checked (Dinelli et al. 2017, Adriani
et al. 2020) describes JIRAM's polar mosaics as "polar stereographic."
Believing this, and building `PolarStereo`'s default radial law around
it, would have been wrong — and provably so, because the fit script
(`scripts/fit_paper_projection.py`) tried every candidate law against
all 48 published frames' footprint centroids and one law won
decisively:

| candidate | centroid RMS (px) | singular-value ratio | fitted km/px |
| --- | --- | --- | --- |
| stereographic | 0.828 | 1.001777 | 15.069 (polar R) |
| equidistant | 0.552 | 1.001011 | 15.040 |
| Lambert | 0.437 | 1.000636 | 15.026 |
| orthographic (sphere) | 0.391 | 1.000486 | 14.983 |
| **orthographic (ellipsoid)** | **0.339** | **1.000095** | **15.005** |

(`docs/reports/paper_projection_fit.md`, "How the radial law was
identified")

The orthographic-on-the-ellipsoid law is the only candidate that
simultaneously drives the fitted axis anisotropy (the ratio of the two
singular values of the fitted linear map) down to `~1e-4`, lands the
pole on an integer pixel and the central meridian on an integer
degree, and reproduces the label's own declared `MPS=15` km/px scale
exactly. Registering six frames against the best *stereographic* grid
instead gives shifts up to 1.04 px and NCC (normalised cross-
correlation, a similarity score of 1.0 for identical images) down to
0.957; the winning orthographic law manages 0.85 px worst-case and NCC
0.985 or better over all 48 (`docs/decisions.md`, "The paper's map
grid"). In effect, "MPROJ=4" is not a curved projection at all: the
map pixel grid *is* the body-fixed equatorial plane, viewed by simple
orthographic (parallel-ray) projection along the rotation axis —
`line = 1800 + x/15`, `sample = 1800 + y/15` in kilometres, no
trigonometry involved once you already have the 3-D surface point. The
true meaning of `MPROJ=4` in MIPL's (JPL's map-projection software
suite) internal numeric-code table was never confirmed against
official documentation — the relevant documentation site was offline
throughout the investigation (`docs/reports/
vicar_map_projection_notes.md`) — so "4 = orthographic" is this
project's working conclusion from the fit, stated as such, not a
verified lookup.

The fit script also had to resolve **a genuine radius degeneracy**:
three physically distinct candidate radii were proposed for the
computation (Jupiter's polar radius, its equatorial radius, and the
polar-region conformal radius `sqrt(a·c)`), and the fit cannot
distinguish between them at all by residual — "the three radii the
brief asked for ... are *exactly* degenerate with the pixel scale —
`rho` is proportional to `R` for every law — so they cannot be
distinguished by residual; they differ only in the `km_per_px` they
imply, and only the polar radius returns 15" (`docs/reports/
paper_projection_fit.md`, "How the radial law was identified"). This
is worth sitting with: a projection's radial law and its reference
radius are not independent free parameters you can both fit from image
registration alone, because registration only ever measures where
pixels land on screen, and screen position depends on the *ratio*
`R / km_per_px`, never on `R` by itself. The radius is settled here not
by the fit but by requiring self-consistency with the one independent
number the label states outright (`MPS = 15.0`).

`PAPER_GRID`, the fitted result, is written directly into
`reproject.py` as a module-level constant, rounded to `1e-4` — not
recomputed at import time — precisely so that the gate
(`test_gate_paper_projection.py`) can assert the *live* constant
matches the *frozen* fit JSON, catching any future accidental edit of
one without the other.

**Validation gate.** `tests/test_gate_paper_projection.py`: the fit
report holds exactly 48 frame records, every registration shift is
within 1.0 px, every NCC is at least 0.90, the fitted linear map's two
singular values agree to within 1%; `PAPER_GRID`'s frozen constants
match the fit JSON to `1e-3`; and a *live* reprojection of the n01a
frame through the current code (not the historical fit run) still
matches the published map with NCC at least 0.90 and centroid offset
at most 1 px.

**Scars.** The stereographic-vs-orthographic story above is the
headline one. A second, quieter scar: `PolarStereo`'s handedness
convention (`clockwise`) and its interaction with hemisphere required
care to generalise correctly to the southern hemisphere in
`regions.py` — flipping the column axis for a south-polar map (so both
poles are "viewed from outside the planet," matching how a reader
expects a map to look) means east longitude runs clockwise on a
south-polar map and counter-clockwise on a north-polar one *under the
same displayed convention*, which the `regions.py` module docstring
calls out explicitly as unavoidable rather than a bug: "the two senses
cannot be made to agree while both maps are outside views."


### 4.9. `regions.py` — named regions and their grids

**Purpose.** Turn a small human-readable registry (`configs/
regions.yaml`) into `RegionGrid` objects: rectangular rasters with an
exact rule tying every pixel to a point on Jupiter's ellipsoid, in
either of two flavours — `polar_ortho` (the published map's own rule,
generalised to either pole and any central meridian) and `local_ortho`
(a tangent-plane patch centred anywhere, used for every mid-latitude
strip). `north_pole_paper` is defined so that it reproduces
`reproject.PAPER_GRID` exactly (checked by a 1000-point round-trip
test), which is what lets the same region machinery serve both "the
exact published grid" and "any other pole, any other scale."

**Key data structures.** `RegionGrid`: a frozen dataclass covering
both projection kinds with a discriminated set of fields
(`hemisphere`/`pole_pixel`/`lon0_east_deg` for `polar_ortho`;
`center`/`center_pixel` for `local_ortho`). `CanvasGrid`: a thin
wrapper that offsets a `RegionGrid` by a crop origin, giving
`reproject_frame` the plain two-value `latlon_to_pixel`/
`pixel_to_latlon` interface it expects regardless of which underlying
projection or crop is in play — this is the seam where regime-1 crop
bookkeeping (a stack's `row0`/`col0` attributes) and regime-2 chunk
bookkeeping (each strip's own small canvas, `strips.chunk_grid`) share
one code path.

**Algorithm.** `polar_ortho` is literally `reproject.PolarStereo` under
another name (`RegionGrid._polar`, a cached property), reusing section
4.8's orthographic-on-the-ellipsoid law directly; the only new work is
generalising it to an arbitrary central meridian and, for the southern
hemisphere, negating the column axis (discussed as a scar in section
4.8). `local_ortho` is genuinely new geometry: at the centre surface
point `c`, build a local right-handed frame — `up` (the outward
ellipsoid normal at `c`), `north` (the component of the planet's
rotation-axis direction `+Z` orthogonal to `up`, i.e. what "north"
means locally on a curved surface), `east = north × up` — and place
each pixel in the tangent *plane* at `p = c + x_km·east + y_km·north`.
Converting a *pixel* to a *point on the ellipsoid* then means
dropping a vertical ray from that tangent-plane point down onto the
curved surface (`p + 2a·up`, intersected with the ellipsoid along
`-up`, using the same `ellipsoid_intercept` machinery from
`geometry.py` but transposed to one ray per pixel instead of one
observer for a stack of rays — `regions._ray_intercepts`, checked
against `geometry.ellipsoid_intercept` in the offline tests). Reading
a *point* back to a pixel is simpler, pure dot products against `east`
and `north`.

**The visibility subtlety.** For a `local_ortho` grid, deciding whether
a given ellipsoid point is actually visible from that tangent plane —
i.e. on the *near* side of the planet as seen looking straight down
`up` — cannot use the obvious test `(P - c)·up > 0`, because the
tangent plane by definition *touches* the ellipsoid only at `c`, so
`(P - c)·up ≤ 0` for essentially every point on a convex ellipsoid,
near side and far side alike; the naive test would classify almost
nothing as visible. The correct test instead looks at the *surface
normal at the candidate point itself*: `visible = (surface_normal(P) ·
up) > -tol`. This is exactly the silhouette condition a rendering
engineer would recognise — a point is on the visible hemisphere from a
given viewing direction precisely when its own outward normal has a
non-negative component along that viewing direction — and the
`regions.py` module docstring states the reasoning explicitly, because
it is exactly the kind of thing that looks like an arbitrary formula
choice unless you have derived it yourself once.

**What a modifier must know.**
- `local_ortho`'s tangent frame construction raises `ValueError`
  outright if the requested centre is within numerical precision of a
  pole — `north` becomes ill-defined there (the projection of `+Z`
  orthogonal to `up` has zero length) — and the error message tells
  the caller to use `polar_ortho` instead. This is not a bug to route
  around; it is the two projection kinds' natural domain boundary.
- `RegionGrid.bbox_latlon()` (used by every prefilter in `stacks.py`
  and `strips.py`) samples the grid's *boundary* at full resolution
  plus a coarse interior grid, then adds a candidate pole exactly if
  it both projects as visible and lands inside the canvas — the pole
  is the one latitude extremum that need not lie on a rectangular
  canvas's boundary, so it has to be checked separately rather than
  assumed to be found by boundary sampling alone.
- `configs/regions.yaml`'s `radii_km` defaults to Jupiter's `pck00010`
  triaxial radii hard-coded as a plain tuple in `regions.py`
  (`JUPITER_RADII_KM`), deliberately *not* read from a loaded SPICE
  kernel — a region grid has to be usable (for display, for a coverage
  query) without any kernel loaded at all.

**Validation gate.** No standalone gate file; exercised through
`tests/test_regions_offline.py` (round trips on `north_pole_paper`
against `PAPER_GRID`, `south_pole`, and a synthetic `local_ortho`
region; far-side points confirmed not visible) and transitively
through `test_gate_regions_pj4.py` (section 4.10) and
`test_gate_strips.py` (section 4.13), both of which depend on this
module's grids being correct.

**Scars.** None distinct from the two already described in this
section and in section 4.8 (the handedness/hemisphere convention, and
the visibility test) — this module's scars are really `reproject.py`'s
scars inherited and generalised.

### 4.10. `stacks.py` — region time stacks

This is the JIRAM builder. JunoCam shares named map grids but uses
`junocam/stacks.py` and retains a band axis (section 4.18); its swaths are
not JIRAM spin sequences and must not be passed to JIRAM compositing.

**Purpose.** For a named region, find every frame that overlaps it,
reproject each one, and assemble the results into one time-indexed
NetCDF with per-pixel validity and emission-angle channels — regime
1's central product.

**Key data structures.** The output `xarray.Dataset` has dims
`(time, y, x)`; coordinates carry both per-time metadata
(`product_id`, `seq_id`, `orbit`, `start_time_iso`, `bore_emission`)
and 2-D `lat`/`lon_east` grids; variables are `image` (float32, NaN
where no frame painted that pixel), `valid` (bool), and `emission`
(float32). Attrs record the region's own serialised description
(`projection`, a JSON dump of `RegionGrid.to_dict()`) so that a stack
file is self-describing without needing `configs/regions.yaml` to
reopen correctly.

**Algorithm — selection.** `select_frames` is a three-stage funnel,
cheapest test first, deliberately, because the full archive has over
a hundred thousand (frame, band-half) rows and only a tiny fraction of
any given orbit's frames will ever overlap a small polar region.
Stage one is scalar quality cuts on the pre-computed geometry table
(`geo_ok`, `on_planet_frac`, `mean_emission`) — free, no geometry
recomputation. Stage two, `_prefilter`, compares each frame's
*pre-computed* footprint bounding box (`min_lat`/`max_lat`/longitude
arc, already sitting in `frames_geo.parquet`) against the region's own
bounding box, padded by one degree — still free, pure DataFrame
filtering, but only an approximate box-against-box test, so it is
allowed to be generous rather than exact. Stage three is the only
expensive step: for every frame that survives the first two filters, a
*fresh* `frame_geometry` call is made (kernels loaded once per orbit
via `KernelSet.for_orbits`, reused across every frame of that orbit in
the loop), every fourth line and sample of its footprint is mapped
through the region's own `latlon_to_pixel`, and the frame is kept only
if at least one such sampled pixel actually lands inside the canvas
(`_pixels_inside`). Three stages, three cost tiers, run in that order —
exactly the shape you want when the exact test is the only one you can
fully trust, but recomputing it for the entire archive would be
wasteful.

**Algorithm — memory design.** Before any pixel is reprojected,
`build_stack` runs one geometry-only pass (`_footprint_boxes`) that
computes, for every selected frame, only its footprint's bounding box
on the *full* region canvas — no image data touched — and unions those
boxes to find the tightest window any frame will actually paint into.
The full-size output arrays are allocated exactly once, at that
window's size (optionally padded by `margin_px`), never at the
region's full canvas size; a `north_pole` region is 3600×3600, and
allocating that at full size for every one of a few hundred frames
would be tens of gigabytes for no reason, since most polar sequences
paint only a modest fraction of the full canvas. The reprojection pass
itself is optionally parallelised across frames with `spawn` workers
(the same SPICE-per-process discipline as `geo.py`), and each worker
returns *only the pixels inside its own frame's footprint box*, not
the whole shared window — kilobytes for a near-perijove frame rather
than the window's full megabytes, which matters when hundreds of
worker results are being collected back into one process.

**Algorithm — composites.** `composite_sequences` collapses a
frame-level stack to one time step per spin sequence: the mean of
`image` over whichever frames actually painted each pixel, `valid` set
wherever *any* contributing frame painted it, and `emission` set to
the *best* (smallest) emission angle any contributing frame achieved
at that pixel — because averaging emission angles would be physically
meaningless, but "the best look any frame got" is exactly the
quantity a later consumer wants to know. (A minor numerical detail
worth knowing if you touch this function: it takes the minimum via
`np.where(mask & isfinite, emission, np.inf).min(axis=0)` rather than
`np.nanmin`, specifically because a pixel *no* frame painted would make
`nanmin` emit an all-NaN-slice warning on every such pixel, and there
are many of those in any cropped stack.)

**What a modifier must know.**
- `select_frames`'s exact overlap test samples every fourth line and
  sample of a frame's footprint, not every pixel — a deliberate
  speed/precision tradeoff, since consecutive detector pixels are a
  small fraction of a map pixel apart at the resolutions this project
  works at, and missing a frame whose overlap is only a handful of
  pixels wide is an acceptable cost against recomputing full geometry
  for every candidate frame of an orbit.
- The reprojection worker pool uses **`spawn`**, never the platform
  default; any code path that calls `build_stack` from inside a script
  (not just the CLI) must be guarded by `if __name__ == "__main__":`,
  exactly as multiprocessing on Windows always requires and as this
  project requires here specifically because of SPICE's process-global
  state, not because of the platform.
- `read_frame_image` is the one place `stacks.py` reads raw pixel
  bytes, and it hard-codes `'<f4'` per section 2.2 — if you are
  debugging a stack that looks like static or extreme brightness
  swings, check this function's dtype before suspecting the geometry.

**Validation gate.** `tests/test_gate_regions_pj4.py`: the orbit-4
frame-level stack contains all 48 paper frames; the n01a stack image
(after applying its stored crop offset) correlates with the published
map at NCC ≥ 0.98 over jointly-valid pixels; the sequence-level
composite recovers the paper's four 12-frame sequences with strictly
more valid pixels than any single contributing frame; a goflow export
run of at least 3 frames lands within 5% of the known 487 s cadence
with `dx_img_m == 15000` exactly and finite `loggrad` wherever valid;
and the rendered movie has exactly as many video frames as the stack
has time steps.

**Scars.** None specific to this module beyond the aberration and
byte-order facts it inherits from `geometry.py`/`reproject.py` and
section 2.2. In the original JIRAM build record, the orbit-4 frame-level stack has
294 time steps at 2.25 GB, and the sequence-level composite compresses
that to 25 time steps, each with strictly more valid coverage than any
one contributing frame (`docs/usage.md`, "Worked example: build a
polar stack and a movie") — a concrete sense of the compression a
sequence composite buys, and of why the GUI (section 4.16) is careful
never to load a frame-level stack fully into memory.


### 4.11. `movie.py` and `export_goflow.py` — rendering and the velocity-model layout

**Purpose.** Two small consumers of a finished region stack. `movie.py`
renders it to MP4/GIF, purely for human inspection. `export_goflow.py`
preflights it, cuts eligible observations into constant-cadence runs,
and writes NetCDF image/mask/gradient inputs for a downstream optical-flow
velocity-retrieval model,
because that model was designed around synthetic data with a fixed
frame spacing and this project's real data does not naturally arrive
that way.

**Key data structures.** `movie.py` uses plain NumPy frame buffers and a
streaming iterator for MP4 encoding. GUI/CLI callers select one physical
band and pass the requested normalization before rendering. The GIF writer
still materializes its frame list. `export_goflow.py`'s unit of output is a *realization*:
`rNNNNN/realization.nc`, dims `(frame, y_img, x_img)`, variables
`image` (invalid pixels **zero-encoded**, not NaN — a deliberate
departure from every other product in this codebase, forced by the
consuming model's own convention), `valid` (bool), and `loggrad`
(`log10(max(|∇image|, 1e-30))`, gradient by central differences in
per-metre units). There is deliberately no `u_mid`/`v_mid` velocity
target: real archive data has no pixel-resolution ground truth, unlike
the synthetic datasets this layout was originally designed to match
(`docs/reports/goflow_summary.md`, §3 — "**No `u_mid`/`v_mid`**: no
ground-truth velocity exists for real data").

**Algorithm — the movie stretch.** `stretch_limits` computes one
percentile-based brightness stretch over the *entire* stack (sampled
if it is very large, since reading every pixel of every time step
would cost a full read of the multi-gigabyte cube) and applies that
one stretch to every rendered frame. The reasoning is stated directly
in the module docstring: "a per-frame stretch hides exactly the
brightness drift that reveals a bad frame" — if geometry or
reprojection is subtly wrong, the tell-tale sign is the scene
*wobbling* or a boundary *jumping* between frames, and normalising
brightness independently per frame would erase precisely that signal
by construction. This is a genuinely transferable idea for anyone
building a QA movie of any reprojected time series: a movie is a
diagnostic tool, and diagnostics should be built to reveal failure
modes, not to look good despite them.

**Before the run finder.** `export_stack` now calls `stack_readiness`
(section 4.19): select a physical band, preserve its units, collapse
processing versions, check the square map grid, require positive regular
cadence and a nonempty common-valid mask. Failure raises before creating
the destination. It prepares one accepted run at a time. Native JunoCam
exports retain DN and their normalization; JIRAM defaults retain radiance
in `W m-2 sr-1 um-1`. RGB is rejected as an analysis band. Source identities,
versions, times, units and transformation provenance accompany the exported
specification, realization manifest and NetCDF attributes.

**Algorithm — constant-cadence runs.** `constant_cadence_runs` grows a
run from the left, checking at each step whether every gap seen *so
far in this run* stays within `dt_tol` of the run's own running median
spacing — comparing against the run's own median, not a fixed
absolute tolerance, is what makes the same 5% test apply equally to a
roughly 30-second spacing and a several-minute revisit, wherever
the actual observations support either. A run must reach at least `min_frames` (default 3) to be kept
at all, but a frame that ends one run is free to start the next
immediately — no time step is wasted just because it happened to be
the moment the cadence changed.

**`loggrad`, precisely.** This exporter's decision to zero-encode invalid
pixels rather than leave them NaN matters here specifically: `loggrad`
is computed with `np.gradient` on the *already zero-filled* image, so
a valid pixel sitting next to an invalid one sees the mask boundary
itself as a sharp edge in its gradient — exactly the behaviour the
downstream model's own synthetic-data convention prescribes for
missing data (`docs/reports/goflow_summary.md`, §3, "Missing data
model"), and exactly why the gradient is computed *before* an optional
crop to a jointly-valid bounding box, so that a pixel on the eventual
crop boundary still differences against its true neighbour rather than
against an artificial edge introduced by the crop itself.

**What a modifier must know.**
- `movie.py` does not require a system `ffmpeg` binary; MP4
  output goes through `imageio`'s `imageio-ffmpeg` backend, which
  ships its own ffmpeg binary bundled in the Python package
  ([movie.render_movie](src/jiram_catalog/movie.py); the dependency is
  declared in `pyproject.toml`).
  Frame dimensions are rounded to a
  multiple of 16 pixels (`MACRO_BLOCK`) specifically so the H.264
  encoder stores exactly what it is given rather than silently
  rescaling a non-conforming frame size.
- `dt_img_s` and `dx_img_m` are each **one constant per realization**,
  not per frame — the whole point of cutting a variable-cadence stack
  into constant-cadence runs in the first place is to hand the
  downstream model something with the fixed-spacing structure its
  architecture assumes.
- `export_stack`'s dataset-root `spec.json` explicitly records
  `"truth_velocities": false` — a small but meaningful piece of
  self-documentation, so that a consumer three steps removed from this
  code cannot mistake a real-data export for a synthetic one with
  known answers.

**Validation gate.** Both modules are covered by
`tests/test_regions_offline.py` (constant-cadence run detection on
synthetic time axes; `loggrad` on a synthetic image with a known
gradient; a tiny synthetic movie written and read back with the
expected frame count) and by `test_gate_regions_pj4.py`'s export and
movie assertions described in section 4.10.

**Scars.** The zero-vs-NaN convention is a deliberate model-input
choice. The later review found different failures: repeated processing
versions looked like extra times, an implicit RGB band was not a scalar
field, fixed JIRAM unit strings misdescribed JunoCam, and reusing export
directories could leave obsolete realizations beside new ones. The GUI
now keys default destinations by source/policy/settings and refuses explicit
nonempty destinations. Movie publication is atomic; a failed encoder cannot
leave a partial file at the public movie path. These are scientific
provenance and publication fixes, not changes to the gradient formula.

### 4.12. `tracking.py` — the classical cloud tracker

**Purpose.** Not a pipeline stage — this module exists purely as the
project's standing acceptance test. It is a textbook template-matching
cloud tracker (normalised cross-correlation, sub-pixel parabolic
refinement), run first on the *published* maps against the *published*
TRACKER4 vectors (does the tracker itself work?), then on *this
project's own reprojections* of the raw archive against the same
published vectors (does the whole geometry-and-reprojection pipeline,
camera model included, reproduce a published scientific result?). Two
independent questions, one tool, run twice.

**Key data structures.** `track_pair(img0, img1, ...)` returns an
`(M, 6)` array: `row0, col0, drow, dcol, ncc, n_valid` per accepted
template. `read_tp4(path)` returns TRACKER4's own `(N, 8)` table via
`vicar.read_vicar`. `match_vectors` joins the two on integer template
position, handling the index-base and axis conventions discussed
below.

**Algorithm — masked NCC as cost volumes.** The two facts that make a
JIRAM polar map hard to track are stated directly in the module
docstring: more than 90% of a 3000×3200 published map is exact-zero
background outside the one frame's footprint, so validity has to be a
first-class concept (a template counts a pixel as data only if it is
neither exactly zero nor non-finite, needs at least 90% of its own
pixels valid to be attempted at all, and the correlation itself is
evaluated only over pixels valid in *both* patches at the trial
displacement being scored — meaning the effective validity mask
*depends on the displacement*, not just on the two images
individually). Computing this honestly needs six masked window sums
per trial displacement (`n, Sa, Saa, Sb, Sbb, Sab` — the running counts
and sums a normalised-cross-correlation formula needs), and a 35×35
search neighbourhood means 1,225 trial displacements per template.
Done one template at a time this is hopeless; done as **cost
volumes**, it is fast: for one *fixed* trial displacement, every one
of the six masked sums is a box filter (a fixed-size sliding-window
sum) of a pointwise product of the two images, so a whole *tile* of
templates is served in one multiply-and-summed-area pass rather than
one per template (`tracking._box_sums`, two cumulative-sum passes
standing in for a full 2-D convolution). This is the same idea as an
integral image / summed-area table from classical computer vision,
applied here to make a per-template operation into a per-tile one; the
throughput measured on real data is 2,141 templates per second on one
core — 20,000 templates in about nine seconds
(`docs/reports/tracking_pj4.md`, "Throughput"). Both patches are also
mean-centred before the window sums, which is exact (NCC is invariant
under adding a constant to either patch) and removes a catastrophic
cancellation that `n·Saa - Sa²` would otherwise suffer in float32 —
subtracting two large, nearly-equal numbers loses precision fast, and
centring first keeps both terms small.

**Algorithm — sub-pixel refinement.** `parabolic_peak` fits a full
six-term quadratic surface (`c0 + c1x + c2y + c3x² + c4y² + c5xy`) to
the 3×3 neighbourhood around each integer correlation peak by least
squares — on exactly nine samples the normal equations close in three
sums, so this is cheap — and returns the stationary point of that
quadratic as the sub-pixel offset. Where the fitted quadratic is not
itself an interior maximum (a saddle, or a peak sitting right on the
edge of the 3×3 stencil), the estimate falls back to a simpler
separable one-dimensional parabola through the centre along each axis
independently, and if even that fails, the offset is zero rather than
an extrapolated guess.

**The reversed line axis, and the unresolved index-base bit.**
Reconciling this project's own `(row, col)` convention with
TRACKER4's stored `(sample, line)` convention took direct empirical
investigation, because the archive documentation does not state either
one: laying the TRACKER4 template positions straight onto a published
map's line axis puts every template centre in the *empty* half of the
array (a footprint-overlap fraction of essentially 0), while reversing
the line axis (`row = 3000 - 1 - (line - 1)`) puts 96% of template
centres and 91% of full templates inside the real footprint
(`docs/reports/tracking_pj4.md`, "Index convention" table) — settling
the direction unambiguously. The remaining one-pixel choice (is
`line`/`sample` 0-based or 1-based in the file?) could *not* be settled
the same way, because the footprint's edge is oblique and TRACKER4
plainly did not require a fully-valid template at every position; the
project instead measured the *tracking result itself* under both
conventions and found the two differ by only 0.0005 px in the pooled
median displacement — "the tracking cannot separate them, which is the
point: whichever is adopted, the choice cannot move the comparison
[below the gate's threshold]" (`docs/reports/tracking_pj4.md`). This is
a good example of a question that is genuinely underdetermined by the
available evidence, correctly reported as such, with the report also
noting that a *two*-pixel error would have been plainly visible and is
therefore ruled out — the convention is right to within the one pixel
that provably cannot matter.

**What a modifier must know.**
- `track_pair`'s `positions` argument, when given explicitly, is
  `(row, col)` 0-based — the caller is responsible for converting from
  whatever convention a ground-truth table uses (see above) before
  calling it; the function itself has no opinion about VICAR's storage
  order.
- The correlation is invariant to a constant offset in either image
  but *not* to a constant scale — this tracker measures displacement,
  not calibration, and a systematic radiometric difference between two
  frames (different emission angle, say) does not by itself defeat it,
  but a multiplicative gain difference would need to be normalised out
  separately if it mattered to a particular use.
- `min_ncc` (default 0.5) is a hard floor below which a peak is
  rejected outright, deliberately not tuned down to force more
  matches through — see section 5 for the project's general rule
  against tuning parameters to pass a threshold rather than
  investigating why they were not met.

**Validation gate.** `tests/test_gate_tracking_pj4.py`: tracking the
published maps against TRACKER4 directly (tracker validation, "A")
needs a pooled n ≥ 20,000 with median error ≤ 1.0 px and at least half
the vectors within 1 px; tracking this project's own reprojections of
the raw archive against the same TRACKER4 vectors (pipeline
validation, "B") needs a pooled n ≥ 10,000 with median error ≤ 1.5 px;
the index base must resolve to 0 or 1 (either is accepted, per the
discussion above). The achieved numbers, pooled over 24 map pairs
each: A gives n=516,517, median 0.224 px, 83.4% of vectors within 1 px;
B gives n=515,377, median 0.434 px, 80.1% within 1 px
(`docs/reports/tracking_pj4.md`). The roughly 0.2-pixel gap between A
and B is the honest cost of the full geometry-and-reprojection chain
relative to tracking the paper's own maps directly — everything this
project adds (SPICE geometry, the inverse camera model, the fitted
paper grid) contributes that much extra scatter on top of the
tracker's own irreducible noise floor, and the gate's separate,
looser tolerance for B (1.5 px vs. A's 1.0 px) is calibrated to that
honestly measured gap rather than to whatever number would make both
pass identically.

**Scars.** Looking closely at the per-pair table in
`docs/reports/tracking_pj4.md` turns up something worth flagging even
though it does not fail the gate: two of the 24 pipeline-validation
pairs (`n0204aa`, `n0204bb`) show a median error of 1.038-1.048 px with only
1.6-2.4% of vectors landing within 0.5 px, starkly worse than every other
pair (which cluster around 0.35–0.5 px median). The gate's *pooled*
threshold absorbs this because the other 22 pairs comfortably carry
the average; the report itself does not call the two outliers out
individually. This is exactly the kind of thing a pooled statistic can
hide and a per-pair table reveals — worth investigating (a specific
frame-pair geometry issue in the `n02`/`n04` sequence group, perhaps
related to the sequence's own timing or a marginal kernel-coverage
epoch) before trusting velocity retrieval specifically from that
sequence pairing, even though the tool as a whole passes its own gate.


### 4.13. `strips.py` — the strip library

**Purpose.** Regime 2's core, and the module that translates section
1's "JIRAM does not revisit anything outside the poles" observation
into a concrete data structure: split every usable spin sequence into
geometrically coherent *chunks*, build one tangent-plane grid per
chunk at a resolution snapped to a shared set of classes, and maintain
a queryable library index across the whole archive.

**Key data structures.** `unit_rows`: the survey's definition of "a
usable look" — `frames_with_geo` rows with `geo_ok`,
`on_planet_frac >= 0.3`, `bore_emission <= 75`. `chunk_table`: splits
each `seq_id` group into `[(chunk_index, rows), ...]` plus a count of
chunks dropped for being too short. `RESOLUTION_CLASSES`: the fixed
tuple `(2, 3, 5, 7, 10, 15, 20, 30, 50, 70, 100, 150, 200, 300)`
km/px. The strip NetCDF itself (`build_strip`'s output) carries
`image`/`valid`/`emission`/`incidence` (best, i.e. smallest, over
contributing frames) plus `n_frames` and `frame_index` per pixel, and
26 scalar attrs recording everything from `center_lat` to
`lon_span_deg` to whether the canvas was capped.

**Algorithm — chunking.** Within one `seq_id`, sorted in time, a chunk
starts at the first row and ends whenever the great-circle distance
from the *chunk's first row's* boresight (not the previous row's —
this matters, see below) exceeds `CHUNK_SEPARATION_DEG` (12°), or the
time gap to the previous row exceeds `CHUNK_GAP_S` (120 s). The reason
one nominal spin sequence can need splitting at all is that a single
commanded sequence can itself slew tens of degrees across the sky
while it runs; without this rule, one `local_ortho` grid would be
forced to cover geometrically unrelated looks, which would either blur
them together or force a huge, mostly-empty canvas. Chunks below
`min_frames` (default 2) are dropped but still *consume* a
`chunk_index`, specifically so that a strip's identity (`strip_id`
embeds the chunk index) does not silently shift if `min_frames` is
later changed — a strip built today keeps the same name if the same
data is rebuilt tomorrow with a different threshold, as long as the
underlying chunk boundaries themselves don't move.

**Algorithm — grid per chunk, and resolution quantisation.**
`chunk_grid` fixes the centre (the unit-vector mean of the chunk's
boresights — `boresight_centroid`, correct across the antimeridian
because averaging unit vectors and re-deriving angles cannot produce
the "averaging 359° and 1° gives 180°" failure that averaging angles
directly would) and the scale first — `resolution_class`, the entry
of `RESOLUTION_CLASSES` nearest in *log* space to the chunk's own
median pixel scale, ties going to the finer (smaller) class — and only
*then* asks where the frames actually land on that fixed tangent plane
to determine the extent, padded by 8 px and capped at 6000 px per
side. Quantising resolution to a small shared set of classes is the
entire point of a *library*: two strips of the same class can be
compared spectrum-to-spectrum or structure-function-to-structure-
function without resampling either one, which matters enormously for
`stats2d.population_statistics` (section 4.14) — pooling statistics
across strips of *different* native resolutions would require
resampling every one of them onto a common grid first, contaminating
exactly the small-scale statistics a spectrum or structure function is
meant to characterise.

**What a modifier must know.**
- Every chunk is built **end to end inside one `spawn` worker** —
  geometry, reprojection, and NetCDF write all happen in the same
  process that furnsh'd that chunk's orbit kernels — and a chunk whose
  computation raises is caught, logged, and skipped, never aborting
  the survey: "a strip library is a survey product and one bad pass
  must not cost the rest" (`src/jiram_catalog/strips.py`, module
  docstring). This is the same fault-isolation philosophy as `geo.py`
  (section 4.6), applied at the granularity of a whole chunk rather
  than a whole frame.
- A chunk whose frames are only *partially* mirrored is not skipped
  outright — `build_strip` drops the missing frames and proceeds with
  whatever is actually on disk, logging a warning; this means a strip
  built while a background image-mirroring job is still running can
  legitimately have fewer contributing frames than its own chunk
  definition implies, and this is by design, not a bug to chase.
- `update_index`'s replacement granularity is per `(orbit, band)`, not
  per orbit: the two bands are built by separate CLI invocations, and
  a run of one band must never delete the other band's already-built
  strips for the same orbit. It also deletes on-disk NetCDF files that
  belong to a replaced `(orbit, band)` selection but are no longer
  named by the fresh index — orphan cleanup, not just index
  bookkeeping.

**Validation gate.** `tests/test_gate_strips.py`: the library index has
rows for both orbits 4 and 24 in both bands and every listed file
opens; the paper's first published sequence (12 frames) is covered by
at most 3 M-band strips whose combined `product_ids` union equals
exactly those 12 frames; for the strip containing the n01a frame, at
least 95% of that frame's own on-planet pixels map through the strip's
grid onto valid strip pixels inside the canvas (a direct check that
the strip grid and the frame geometry that built it are mutually
consistent); every `km_per_px` value is one of the fourteen resolution
classes; and a latitude-band query returns only genuinely overlapping
strips. The original JIRAM build produced 289 strips across orbits 4
and 24 (both bands), 392 MB total (`docs/build_log_2026-09-04.md`,
step 9b).

**Scars.** None distinct from the design choices already described —
this module's difficulty was almost entirely in the chunking and grid
design decided up front, which the offline tests and gate then
confirmed rather than a case of something built wrong and later fixed.

### 4.14. `stats2d.py` — masked spectra, structure functions, bicoherence

**Purpose.** The core numerical diagnostics for a scalar strip, all built
around one recurring fact: a strip is a field *with holes*, and every
formula that assumes a complete rectangular grid (an FFT, a lag
difference, a bispectrum) has to be adapted to survive that. This
module deliberately adopts a downstream velocity-retrieval model's own
numerical conventions verbatim (angular wavenumbers, forward-normalised
FFT, `(y, x)` array order with row increasing with `y`) — tagged
`conventions-v1` and quoted directly in the module's own docstring —
so that the wavenumber axes and Fourier normalization match the model's
diagnostics. The physical field and its units still matter: radiance
variance is not kinetic energy. These kernels now also receive selected
JunoCam DN bands through the scientific API; band selection, normalization
and compatible population grouping belong to the preparation layer in
section 4.19.

**Key data structures.** `power_spectrum_2d` returns a dict, not a
bespoke class, deliberately — `power` (the 2-D periodogram),
`kx`/`ky` (angular wavenumber axes, rad/m), `variance`, `valid_frac`,
`window_power`. `isotropic_spectrum` reduces that to `E(k)` on shells.
`BicoherenceAccumulator` is the one genuinely stateful object in the
module: it accumulates three running sums (`_bispectrum`, `_parents`,
`_sum_mode`) across many independent segments without ever holding all
of them in memory at once, because a population bicoherence needs
every strip's rows, not just one strip's.

**The recipe, stated once because every function in the module follows
it.** Detrend over *valid* pixels only (subtract their mean); set
invalid pixels to that same fill value (usually the just-subtracted
mean, i.e. zero after detrending) — this adds no spurious signal and
no discontinuity beyond the hole's own edge, since the fill matches
what the valid data already averages to; apply a separable taper
(Tukey, or Hann as taper→1) to the *whole* canvas so the finite strip
does not leak spectral power from ringing at its edges; then divide
the resulting periodogram by `valid_frac · window_power` so that a
fully-valid, untapered field obeys Parseval's theorem exactly (total
power equals variance) and a masked field is unbiased in *total*
variance to first order.

**Shell counting, precisely.** `isotropic_spectrum`'s shell `j` holds
every lattice mode with `round(|k|/dk) == j`; its population is
*counted on the actual 2-D lattice*, never assumed proportional to `k`
(which is the naive, and wrong, continuum approximation — a discrete
FFT lattice has a genuinely different, and eventually very anisotropic,
population of modes per shell near the Nyquist corners than a
continuous disc would). The result: `sum(E · dk)` equals `sum(power)`
to numerical rounding, checked directly as a Parseval-identity offline
test on a 256×256 white-noise field. This module's docstring documents
a deliberate departure from the source conventions document on exactly
this point: the conventions document restricts the isotropic spectrum
to shells *inside* the inscribed Nyquist disc and drops the partial
corner shells, but `stats2d.py` instead returns *every* shell out to
the lattice corner, flagged `inside_disc`, "because the module's own
spec calls for a population counting that 'covers every lattice
point' and dropping the corner shells would break the exact Parseval
identity the offline tests check" (`src/jiram_catalog/stats2d.py`,
module docstring). A second, separate departure: the conventions
document defines `E` with a kinetic-energy factor of `0.5·|û|²`,
appropriate for a velocity field; these strips carry a scalar image
field (JIRAM radiance or JunoCam DN), so the factor is absent here and
`E` integrates to plain variance instead.

**Mask leakage — a limitation stated plainly, not corrected away.**
The `valid_frac · window_power` correction above is only approximately
unbiased in total variance when the mask and taper are uncorrelated.
Preferential holes at a tapered border can bias even the total, since
`mean(mask × window²)` need not equal `mean(mask) × mean(window²)`.
The module's docstring also states the larger spectral limitation: masking a strip
*convolves* the true spectrum with the mask's own power spectrum, which
moves variance across wavenumber `k` even when the total is right.
Smooth, sparse, large-scale masks (a few big holes) have a "red" mask
spectrum — concentrated at low `k` — and distort a power-law slope only
mildly; a speckle-like mask (many small, scattered invalid pixels) has
a broadband mask spectrum and "no scalar correction can repair" the
resulting distortion (module docstring, echoed in
`docs/open_items.md`, "Mask leakage in spectra has no correction").
This was measured directly, not just argued: a synthetic `k^-3` field's
fitted spectral slope is `-2.97` unmasked and `-2.92` with 15% of
pixels masked as a few smooth blobs (`docs/build_log_2026-09-04.md`,
step 9a) — a real but mild distortion, consistent with the "smooth,
sparse masks are mostly forgivable" claim, and the module makes no
attempt to claim more than that. Section 4.6's L-band-emission
discussion and this mask-leakage discussion share a pattern worth
naming: the codebase is comfortable shipping a documented, *measured*
limitation rather than either hiding it or over-engineering a
correction for a case (speckle masks) that provably cannot be
corrected by any scalar factor.

**Bicoherence, and why it needs many segments.** The estimator is the
standard normalised one,
`b² = |Σ X(k1)X(k2)X*(k1+k2)|² / (Σ|X(k1)X(k2)|² · Σ|X(k1+k2)|²)`,
which the Cauchy-Schwarz inequality confines to `[0, 1]` by
construction: it reaches 1 exactly when the phase of the "sum" mode is
*locked* to the sum of the two parent modes' phases in every segment
(genuine three-wave phase coupling, the kind a nonlinear interaction
produces), and it falls toward `1/n_segments` when that phase is
effectively random across segments — meaning a *single* segment's
bicoherence is meaningless (it is trivially 1, since one segment's
phases are always "locked to themselves"), and the estimator is only
informative once many independent segments have been accumulated. The
offline test constructs exactly this contrast directly: 400 segments
of `cos(k1·x + φ1) + cos(k2·x + φ2) + 0.5·cos((k1+k2)·x + φ1+φ2) +
noise`, with `φ1, φ2` drawn fresh per segment, gives `b² > 0.5` at the
coupled `(k1, k2)` bin; replacing the third term's phase with an
*independent* random phase (breaking the coupling while leaving the
power spectrum identical) drops `b²` below 0.1 at the same bin — the
same power spectrum, radically different bicoherence, which is exactly
the point of a bicoherence diagnostic: it detects phase coupling that
an ordinary spectrum cannot see at all.

**What a modifier must know.**
- Structure functions are computed as whole-array shifted-slice
  differences, one pass per lag, never per pixel — `_shift_pair`
  returns the increment field and the joint validity mask for one lag
  as plain numpy slicing, checked directly against a brute-force
  double loop over valid pairs to `1e-12` in the offline tests.
- Odd integer orders of the structure function keep their *sign* — `S3`
  is the signed third-order structure function, not `⟨|δf|³⟩` — while
  a non-integer order uses `|δf|^p`; this is a deliberate convention
  choice (signed odd moments carry directional/asymmetry information a
  magnitude would discard) stated explicitly in the function
  docstring, not an accident of how `**` behaves on negative numbers.
- `population_statistics` requires every strip it is given to share
  the *same* `km_per_px` and raises if they do not — this is
  deliberately a hard error, not a warning, because pooling spectra
  across resolution classes without resampling (which the whole
  point of resolution quantisation in `strips.py` was to avoid) would
  silently average together numbers that are not directly comparable.

**Validation gate.** No read-only gate file (this module predates
having any real strips to gate against — it was built and tested on
synthetic fields, per its own spec: "the strip files may not exist yet
while you work, so build and test on synthetic fields," `docs/specs/
2026-09-05_stats2d.md`) — but its seven offline tests
(`tests/test_stats2d_offline.py`) *are* the gate in the sense that they
were written before implementation and check exact numerical
properties (Parseval to `1e-9` relative, a known sinusoid's spectral
peak location, the `-3`-slope recovery above, structure functions
against brute force, the bicoherence phase-coupling contrast) rather
than looser regression-style comparisons.

**Scars.** The mask-leakage limitation above is the honest one. A
smaller, easy-to-miss numerical detail worth flagging for a modifier:
`isotropic_spectrum`'s shell-population counting depends on getting
`dk` (the shell bin width) right for the *actual* canvas aspect ratio —
the default is `2π/(max(nx, ny)·dx)`, the fundamental of the *longer*
side, not an assumption that `nx == ny`, because strip canvases are
essentially never square (a spin-sequence footprint is much longer
along-track than across).


### 4.15. `trackability.py` — repeat-view and displacement resolvability

**Purpose.** A pure numpy/pandas analysis of the JIRAM geometry table,
with no SPICE calls or imagery, that answers a question logically prior
to actual tracking: for a given orbit and latitude band, does the archive
provide two views of the *same* patch of Jupiter close enough in time
that a plausible cloud displacement would be measurable at that
frame's own pixel scale — before a single pixel is ever reprojected?
It tests the repeat-view opportunity behind section 1's two regimes,
producing per-orbit, per-band candidate counts. It does not establish
that every nonpolar location lacks repeats, or supply a completed
JunoCam revisit assessment.

**Key data structures.** `select_unit_of_analysis`: `frames_with_geo`
rows passing `geo_ok`, `on_planet_frac >= 0.3`, `bore_emission <= 70`,
each labelled with one of seven latitude bands (`LAT_BAND_NAMES` — S
polar/mid/low, equator, N low/mid/polar — the same seven bands the GUI
uses for its coverage charts). `build_pairs`: every *candidate* repeat
view — two frames of the same orbit and band half, 90 seconds to 6
hours apart, whose boresights fall within a generous separation
threshold. `add_per_frame_stats`: per-frame `has_partner`,
`n_partners`, `best_dt_s`, and `trackable_{10,30,100}` at three
reference cloud speeds.

**Algorithm — the overlap proxy, and why it needs a second reduction on
top of the spec's literal threshold.** The pairing threshold itself is
deliberately generous and boresight-only, not a real footprint
intersection: `sep_km <= 0.5 · 432 · min(pixel_km of the two frames)`,
432 being JIRAM's across-track sample count — wide enough that it
"rarely misses a genuine repeat view, at the cost of also admitting
boresight-near pairs whose footprints do not truly overlap"
(`src/jiram_catalog/trackability.py`, module docstring). Applied
literally to PJ4's dense polar mosaics, this threshold alone turned
out to be *too* generous in a specific way: one frame's boresight
routinely falls within range of *several* neighbouring raster
positions in the next repeat sequence, not only its true same-position
repeat, because at these polar latitudes the boresight-separation
ranges of "same raster position, next sequence" and "adjacent raster
position, next sequence" genuinely overlap. This was verified
numerically, not assumed: against the paper's own PJ4 fixture, the
*closest wrong-position pair* is measurably nearer than the *farthest
correct-position pair*, so no single fixed threshold can cleanly
separate the two cases while still keeping every correct-position
pair. `build_pairs` therefore adds a **nearest-partner-per-sequence
reduction** on top of the threshold, not instead of it: for each frame
and each *other sequence* within the time window, keep only the single
closest match — its true nearest repeat view in that sequence — rather
than every raster position within the generous proxy radius. This is
explicitly flagged in the module docstring as a judgment call beyond
the letter of the spec, with the reasoning laid out in full, and it is
what makes `best_dt_s` land on the correct repeat interval rather than
an adjacent raster step.

**The best-baseline redefinition.** `best_dt_s`'s current definition —
the smallest `dt` among a frame's partners that are trackable at
30 m/s — replaced an earlier one that asked for the partner whose
expected displacement was "closest to 5 pixels," which is what the
original spec asked for literally. That definition failed on real
data: PJ4's north-polar campaign runs roughly 30 sequences spaced
about 487 s apart over an hour and a half, and a sequence several
repeat-intervals away could *coincidentally* land closer to the
`d_px = 5` target than the frame's true nearest repeat, purely from
`dt`/pixel-scale arithmetic, and win the comparison outright. The
revised rule — always prefer the smallest usable `dt`, i.e. the
nearest-in-time trackable baseline — has no equivalent failure mode,
because "nearest in time, among the trackable options" cannot be
beaten by an arithmetic coincidence at a much longer baseline the way
"closest to a fixed target displacement" can. This revision is
discussed further in section 5 as one of the two cases where a gate
itself, not the code under it, turned out to encode the wrong
definition.

**What a modifier must know.**
- Every count this module produces should be read as "at least this
  many candidate repeat views," never as a verified re-detection count
  — the nearest-partner-per-sequence reduction removes *double-
  counting* of one real repeat view, it does not verify that the two
  frames in a pair actually show the same cloud feature (that
  verification is exactly what `tracking.py`, section 4.12, is for).
- The separation threshold's `min(pixel_km)` and the displacement
  formula's `max(pixel_km)` are two *different* normative quantities
  from the spec, deliberately not the same number reused: the
  separation test asks "is this close enough to plausibly be the same
  target," where the finer-resolution frame's pixel size is the
  relevant scale for "close," while the displacement-in-pixels formula
  asks "would a cloud crossing be resolvable," where the *coarser*
  frame's pixel size is the one that actually limits what is
  measurable. The module docstring flags this explicitly as a place a
  future modifier could easily and silently introduce a bug by
  "simplifying" the two uses of `pixel_km` into one.
- `great_circle_km` uses a fixed sphere radius of 69,911 km (Jupiter's
  mean radius) for the separation proxy — an approximation appropriate
  to a coarse overlap test, not the triaxial-ellipsoid precision the
  geometry engine itself uses; this module never needs SPICE precision
  because it is answering a survey-level question, not a per-pixel
  one.

**Validation gate.** `tests/test_gate_trackability.py`: all 48 paper
frames have a partner, and every one of their `best_dt_s` values lands
within 60 s of an integer multiple (1 to 3) of the 487 s sequence
cadence; the pairs table contains at least 6 pairs between the paper's
first and third sequences with `dt_s` within 30 s of 974 s (two
sequence intervals); trackable counts never exceed with-partner counts,
which never exceed total frame counts; and the heatmap figure exists.
The original JIRAM mirror analysis produced 29,862 unit-of-analysis frames,
16,541 with at least one partner, 7,493 trackable at 30 m/s, from
31,308 candidate pairs and 2,097 deliberate repeat-sequence pairs
across 61 orbits (`docs/reports/trackability.md`, "Totals").

**Scars.** The best-baseline redefinition above is the headline scar,
discussed further in section 5 and section 6.

### 4.16. `api/` and `frontend/` — the browser

**Purpose.** The browser makes the observation-to-analysis path visible.
FastAPI serves JSON, Arrow and PNG under `/api` and the compiled React /
TypeScript / deck.gl application at `/`. One process serves both halves,
so an SSH tunnel exposes one port. The original API contract established
the catalog, stack and strip schemas; the
[accepted review specification](docs/specs/2026-09-07_review_implementation.md)
and its companion specs add the scientific workflows. The current GUI can
request Python scientific calculations and builds as well as inspect files.
There is no separate browser implementation of the spectral or navigation
estimators.

**Task names and product names are different layers.**
[App.tsx](frontend/src/components/App.tsx) mounts all five views and displays
only the active one. The internal state keys `catalog`, `poles` and `strips`
remain stable even though their user-facing names have changed:

| Task view | What it lets the user do | Main implementation |
| --- | --- | --- |
| Explore | filter mapped observations, inspect footprints/details, build a selection | `CatalogView`, `api/catalog.py` |
| Time series | inspect/build region stacks, choose a physical band, check readiness, render/export | `PolesView`, `api/stacks.py` |
| Image library | browse mapped swaths, open native statistics, summarize selected populations | `StripsView`, `api/strips.py`, `api/science.py` |
| Compare | linked split/blink inspection and measured registration on compatible grids | `CompareView`, `api/science.py` |
| Coverage | distinguish archive, indexed, mapped and eligible data; inspect sources and references | `CoverageView`, `api/coverage.py` |

The task presets and instrument-specific controls narrow what matters for a
particular question. A latitude search uses footprint overlap rather than
assuming the boresight lies inside every swath of interest. JunoCam's
unassessed revisit state stays distinct from a failed revisit criterion.
Its mapped coverage is deliberately smaller than its archive inventory;
Coverage and product details make the reason inspectable.

**State is a scientific interface concern.**
[store.ts](frontend/src/store/store.ts) keeps catalog columns, filtered row
indices, the working selection, source metadata, image settings and request
state. The catalog arrives as Arrow IPC and is unpacked into typed arrays,
so filters can produce a compact `Uint32Array` of surviving rows without
allocating a JavaScript object for every point. Geometry uses numeric arrays;
epoch milliseconds are a `Float64Array`. The timestamp conversion must be
explicit on the server: the old JunoCam 1970 dates were unit conversion,
not a bad spacecraft clock.

The current snapshot has 47,731 mapped rows, including 72 preferred eligible
JunoCam observations ([delivery record](docs/build_log_2026-09-07.md)). This
is an inventory of the local policy view, not a promise that all those rows
have independent motion partners. Instrument-aware detail responses include
identity, available versions, label/source links, geometry and the quality
rationale. An excluded product can remain discoverable as metadata while
its thumbnail returns HTTP 403.

At overview scale the map uses density aggregation so overlapping
boresights do not hide the sampling pattern. Points, selected footprint
outlines, coordinate conventions, legends and hover detail restore the
individual-observation context. `OrthographicView` uses a scalar zoom,
keeping x/y aspect fixed. The actual ellipsoid geometry remains in Python;
the browser's map and graticules are display coordinates.

**Selections survive changes in row order.** The selection tray stores
`product_id|half` identities rather than table positions. It can be collapsed
to give space back to the image, and can save selections, pass them to a
stack build, or filter the image library using actual source membership.
Matching by shared orbit alone would include unrelated swaths and is no
longer the selection-to-library rule. Dialogs have keyboard/focus handling;
wrapping controls, collapsible metadata and a minimum image height prevent
opening statistics from reducing the scientific image to a narrow strip.

**Physical normalization precedes display mapping.** The image API reads
a chosen physical band, applies requested normalization and validity, then
produces an 8-bit image for viewing. For grayscale images, the frontend's
lookup table maps those bytes to a color palette; changing that palette can
operate on pixels already downloaded. RGB composites carry three channels,
with explicit channel limits and intersection validity. No RGB canvas is
used as a scalar input to a spectrum or optical-flow export.

`X-Rows`, `X-Cols`, `X-Stride` and `X-Bounds` describe the actual served
raster. The server chooses the integer sampling stride, and the browser
places that sampled image in physical map coordinates. This distinction is
especially important for `flat:sigma`: its display implementation measures
sigma in served pixels, whereas scientific preparation uses native map
pixels (section 4.18). Percentile/asinh stretching is a viewing operation,
not another calibration.

**An old successful response can still be the wrong response.** Suppose a
RED image request starts, the user chooses GREEN/Lambert, and the slow RED
request finishes last. Checking only that the stack ID still matches would
paint obsolete pixels under correct-looking GREEN controls. The store now
compares the full request identity: source, frame, band/composite choice,
linked or per-channel limits, normalization and parameters, and stretch.
It clears previous pixels while an uncached replacement loads and accepts a
response only if it still answers the current request. Statistics use
corresponding band/normalization guards. `ImageView` also clears/replaces
asynchronous bitmaps, so stale GPU content cannot outlive a cleared source.

The lesson is transferable to model diagnostics: a field is identified by
its processing settings as well as its filename and time index. A loading
state is preferable to a plausible plot whose controls describe another
calculation. Browser tests therefore wait for actual replacement pixels;
an empty intermediate canvas is not evidence that a band change worked.

**Lazy statistics make browsing cheaper without changing the estimator.**
Opening a library image loads its metadata and pixels. Native spectral and
structure-function calculations are requested when the statistics panel is
opened, and old curves are cleared if band or normalization changes. A
6000-square image can still take minutes for full native statistics. The
responsiveness improvement is avoiding unrequested work and identifying
pending results honestly; it does not replace the estimator with a cheap
thumbnail spectrum. Population analysis and export recipes are discussed in
section 4.19.

**NetCDF serialization is a deliberate concurrency limit.** FastAPI handlers
and background jobs are threaded, but the native NetCDF library is not safe
for arbitrary concurrent use. Real browser concurrency exposed a native
crash, so [io_guard.py](src/jiram_catalog/api/io_guard.py) supplies a
process-wide reentrant lock. Its route wrapper is intentionally small:

```python
with NETCDF_IO_LOCK:
    return function(*args, **kwargs)
```

`NetCDFRoute` wraps stack, strip and science endpoints at registration;
Coverage's native-data access and background movie/build/export jobs use the
same guard. It spans the whole operation, including expensive calculations,
not just an individual `.values` call. This matters because xarray reads
lazily and file handles can outlive a superficially guarded open. The
tradeoff is equally real: a long native statistics request can queue other
image work. Health, configuration, job polling and catalog operations stay
outside the native I/O critical section. Do not promise that every image
interaction remains immediate while statistics run.

**Bounded reads and cache keys are part of correctness.** A display sample
must slice the lazy DataArray *before* accessing `.values`; slicing a NumPy
array after materialization has already paid for the full native read.
Metadata now shares one band's sampled variables across requested display
normalizations. Graticules and local-time contours use at most roughly
400 pixels per side for display, with the matching x/y coordinates and
periodic seam handling.

The [performance record](docs/build_log_2026-09-07.md) measured native
6000×6000 local-time contouring at 6.471 seconds and 1.87 GB peak process
memory. The sampled version took 0.581 seconds and 243 MB; both produced
zero lines for the examined strip. This is a useful caution against
assuming an invisible overlay is cheap. Source-size/mtime caching prevents
repeating it. A midnight-crossing fixture checks that reducing the contour
grid preserves the physical coordinate and seam conventions.

Cold strip metadata can still take seconds because large compressed
NetCDF chunks must be decompressed even for sparse scene-wide samples.
The final recorded large-strip metadata times were 12.294 seconds cold and
0.011 seconds repeated. These are measurements from that deployment, not
latency guarantees for every node or filesystem state. Native scientific
arrays are unchanged by the display-read optimization.

Catalog/strip-table caches include source-index and quality-policy
signatures and share one cold build among concurrent readers. A policy
signature covers source size/mtime and the quality YAML; the loader also
tracks size because a same-second configuration update on Lustre exposed an
mtime-only invalidation failure. Arrow gets a separate bounded thread pool
(default four, configurable with `JIRAM_ARROW_THREADS`) rather than changing
BLAS/OpenMP settings used by scientific workers. These fixes address table
construction and duplicate work, not model physics.

**Jobs, publication and paths.** Movie rendering, stack building and goflow
export use an in-process job manager, with persisted JSON job records and
browser polling. The worker pool does not override the NetCDF serialization
rule. Stack builds write the appropriate product under `regions/`; other
GUI selections, caches, statistics and exports live under `gui_cache/`.
The GUI constrains explicit export destinations to its mirror export
subdirectory. It does not offer unrestricted server-side output paths.

Movies are rendered from policy-aware selected-band data, written to a
temporary output, and published by atomic rename after encoding succeeds.
GET also validates the requested physical band. Historical JIRAM movies
have a controlled native/unnormalized fallback; historical JunoCam movies
are refused because their unfiltered frames cannot establish current
eligibility. Current JunoCam movie identity includes source state and
eligible observation identities, band and normalization, and responses
avoid browser storage that could bypass a later policy check. A policy
change must alter more than the visible catalog count.

The service need not download new native data merely to browse local
products. Source buttons and the Coverage reference links can leave the
application for PDS or Mission Juno; the older blanket claim that no request
leaves the node is therefore not an interface contract.

**Validation and the remaining boundary.** The original API and production
browser gates remain. New unit/integration checks exercise direct failure
exclusion, requests finishing out of order, full parameter identity, actual
band-specific pixels, NetCDF serialization, bounded contour reads and cache
invalidation. The delivery record reports 135 frontend unit tests and a
successful complete production browser gate, including real JIRAM/JunoCam
pixels and native statistics. It separately records the old JunoCam
minimum-count gate that conflicts with accepted exclusions (section 5).
The interface makes missing cadence, unavailable vectors and uncertain
navigation visible; successful browser interaction does not turn those
unknown scientific quantities into validated results.

### 4.17. `config.py`, and the subcommand-registration pattern

**Purpose.** `config.py` resolves the two paths every other module
needs — the local mirror and the read-only published paper data —
through one precedence chain, used identically by both settings:
explicit function argument, then an environment variable
(`JIRAM_MIRROR`/`JIRAM_PAPER_DATA`), then an optional TOML file
(`$JIRAM_CONFIG`, else `~/.config/jiram_catalog.toml`; a missing file
silently contributes no overrides), then a built-in default pointing
at this project group's own shared cluster paths. Every resolver does
its lookup *at call time*, never caching anything at import time — the
module docstring states the reason directly: "so callers (including
tests, via `monkeypatch.setenv(...)`) can change the environment and
see it reflected in the next call," which matters because Python
module-level constants computed once at import would otherwise make
this kind of environment-driven test isolation impossible without
reloading the whole module.

**The subcommand-registration pattern.** Several modules
(`strips.py`, `stats2d.py`, `gui_cmd.py`, `config_cmd.py`) expose their
own CLI surface without touching `cli.py` directly, through a small,
consistent contract: `add_subparser(subparsers)` registers the
module's own `argparse` parser and options onto a shared
`subparsers` action, and calls `parser.set_defaults(func=run)`, where
`run(args)` is the module's own entry point. `cli.py`'s `make_parser`
imports each of these modules and calls `add_subparser` on them at the
very end of parser construction; `cli.main`'s dispatch loop
(`_run`) checks `getattr(args, "func", None)` first and, if a module
registered one, calls it directly — bypassing `cli.py`'s own large
`if args.command == ...` chain entirely for every subcommand a module
owns itself. This is the concrete mechanism behind a rule stated
project-wide in `AGENTS.md` and `CLAUDE.md`: executors never edit
`cli.py`, `pyproject.toml`, or `uv.lock` as part of a delegated task,
because those are exactly the files where two concurrently-developed
milestones would otherwise collide; a new subcommand instead lives
entirely inside its owning module and is wired into the shared parser
by the project lead afterward, as one small, reviewable diff to
`cli.py` rather than by letting every milestone touch the shared entry
point directly. Every module built this way also carries its own
`__main__` block (`if __name__ == "__main__":`), so `python -m
jiram_catalog.strips ...` works identically to `jiram-catalog strips
...` even before the coordinator has wired it into the main CLI — this
is what let each milestone's own validation block run and pass fully
standalone during development, independent of whether `cli.py` had
been updated yet.

**What a modifier must know.**
- The `describe()` function in `config.py` is what both
  `jiram-catalog config` and `python -m jiram_catalog.config_cmd`
  print; it reports, per setting, the resolved value *and* which tier
  of the precedence chain supplied it (`env`/`file`/`default`) — this
  is the tool's own answer to "why is this path what it is," and is
  the first thing to run when a command behaves as if it is reading
  from the wrong mirror.
- `tomllib` (Python 3.11+ standard library) is imported *lazily*,
  inside `load_config_file`, only once the environment variable for a
  setting has been checked and found absent — this keeps the built-in
  defaults working even under a foreign interpreter that lacks
  `tomllib`, which matters concretely because two of the `scripts/`
  helpers deliberately run outside the `uv` project's own pinned
  Python (`docs/configuration.md`).
- A CLI subcommand's own `--mirror`/`--orbits` options still take
  precedence over everything `config.py` would otherwise resolve —
  "explicit argument" is the *first* tier of the chain, not a special
  case layered on top of it.

**Validation gate.** No gate file (this is plumbing, not science); the
generalize-paths milestone's own validation block instead grepped the
entire `src/`, `tests/`, and `scripts/` trees for the literal string
`"kaushiks"` outside `config.py` itself and required zero matches
(`docs/specs/2026-09-05_generalize_paths.md`) — a blunt but effective
check that no user-specific path had been left hard-coded anywhere
else, run alongside the full existing test suite to confirm the
refactor changed no behaviour, only path plumbing.

**Scars.** None — this was a late, mechanical hygiene pass
(`docs/decisions.md` records it as settling nothing scientifically,
only making paths overridable), and its own validation block confirms
it changed no tolerance, no assertion, and no computed value anywhere
in the codebase.


---

### 4.18. JunoCam — archive, camera and quality evidence

**Purpose.** The JunoCam branch brings reflected-light images into the same
catalog and map workspace without borrowing JIRAM assumptions that would
silently change their meaning. For an ocean modeler, this is the difference
between adding another variable from the same model grid and adding an
entirely different observing instrument: the eventual arrays may align,
but the acquisition, calibration and sampling operators do not.

**Start with archive identity.** [junocam/pds.py](src/jiram_catalog/junocam/pds.py)
reads per-volume `INDEX.LBL` and `INDEX.TAB` records. This avoids walking
tens of thousands of individual product directories. The label supplies
column names and ordering, but quoted CSV parsing is tried before the
nominal fixed-width positions because real releases do not obey one
consistent padding convention. Archive directory names are authoritative
for locating volumes; an inconsistent `VOLUME_ID` inside an index does not
become the local directory name. Bare filenames in the early volume indexes
are resolved against a cached directory listing.

A product such as `JNCR_2017033_04C00104_V02` encodes an RDR, day of year,
orbit/filter/sequence identity, and processing version. Its last suffix is
provenance, not another exposure. Two tables deliberately answer different
questions:

| Table | Unit being counted | Why preserve it? |
| --- | --- | --- |
| `manifest_files.parquet` | archive-listed product versions | exact file provenance and supersession history |
| `manifest.parquet` | highest-version product identities | preferred archive inventory |
| `junocam_images.parquet` | indexed local label/product versions | dimensions, timing, filters and paths needed to read pixels |
| policy observation table | versions joined with quality and geometry evidence | current eligibility and preferred-version decisions |

Do not infer level solely from `JNCR_`: global mosaics can share the prefix
and an RDR product label. Processing level and `GLOBAL_MAPS` directory
membership distinguish native framelet products from maps. This boundary
prevents an already mosaicked image from being treated as a camera exposure.

`discover_volumes` now inspects the live PDS directory. If discovery fails,
it reports use of a cached directory snapshot; only when that is unavailable
does it fall back to the historical 1–35 range, explicitly marking
completeness unverified. `LAST_VOLUME = 35` is therefore a fallback bound,
not a claim that no later volume can exist. Coverage presents inventory
stages and source ages so that a metadata count cannot masquerade as a
local-pixel or mapped-product count.

[junocam/mirror.py](src/jiram_catalog/junocam/mirror.py) preserves the archive
path under `<mirror>/junocam/<volume>/`. It retrieves labels before images,
caps simultaneous download shards at four, and handles throttling and
retryable HTTP errors. A readable label's `RECORD_BYTES * FILE_RECORDS`
provides an image-size check. That is a transfer-integrity check; it does
not establish meaningful signal or valid radiometry. The label parser and
indexer, [labels.py](src/jiram_catalog/junocam/labels.py) and
[index.py](src/jiram_catalog/junocam/index.py), preserve those distinctions.

**The native image is a timed sequence of filter strips.** A *framelet* is
one 128×1648 band strip from one camera readout. A *frame* is the simultaneous
readout of the commanded bands. The file cycles through bands inside each
frame, in the label's `FILTER_NAME` order. The reader checks the equation

```text
LINES = n_frames × n_bands × 128
array shape = (n_frames, n_bands, 128, 1648)
```

The index's historical name `n_framelets` means the number of these frame
repeats, not the total number of band strips. `n_framelet_rows` and the band
count disambiguate it. This is the sort of naming detail that matters when
allocating geometry arrays: multiply by the band count exactly once.

[images.read_image](src/jiram_catalog/junocam/images.py) reads 16-bit RDR
samples with `dtype='>u2'` and 8-bit EDR samples with `np.uint8`. It checks
the detector width, line equation and sample count, and rejects summed
products that do not fit the supported camera grid. Only after decoding
does it convert to a working floating-point dtype. JIRAM's `'<f4'` rule
would turn these bytes into meaningless values.

EDR decompanding uses the 256-entry SIS `SQROOT_TABLE`; it is a lookup,
not an approximate square-root formula. For example, encoded 255 maps to
linear value 2879. This recovers the specified count mapping, not the
information discarded by quantization. RDR processing has also rescaled the
signal using the archive's radiometric convention. Consequently, decompanded
EDR and RDR are both linearized count representations, but are not certified
as having interchangeable calibration. Current native JunoCam map variables
retain **DN** units; they are not relabeled as JIRAM radiance or as absolute
I/F merely because they are stored in float32.

**Camera geometry: the half pixel is real.**
[camera.BandCamera](src/jiram_catalog/junocam/camera.py) gets focal length,
pixel size, distortion centers and coefficients from `juno_junocam_v03.ti`.
The instrument-kernel recipe measures from the upper-left pixel corner;
public array indices measure pixel centers from zero. Thus an array pixel
`(x,y)` enters the camera model as `(x + 0.5 - cx, y + 0.5 - cy)`.
A half-pixel error here is a systematic displacement, not harmless indexing
notation.

For ideal focal-plane coordinates, the radial distortion is

```text
r² = x² + y²
(x_distorted, y_distorted) = (x, y) × (1 + k1 r² + k2 r⁴)
```

`undistort` uses the kernel's five fixed-point iterations. `pixel_to_vector`
then forms a unit ray from the undistorted point and focal length;
`project` runs the inverse direction and rejects rays behind the camera.
The photoactive mask keeps 1608 columns after the first 23 dark columns;
the rest of the line includes dark/isolation/overscan regions. These are
physical detector masks, not display crops.

**Timing differs fundamentally from JIRAM.** All bands in one readout share
one epoch. Their focal-plane positions differ, so the same atmospheric
point can enter RED and GREEN in different frames. Geometry must therefore
use the epoch of each contributing frame, not one epoch for the whole tall
archive file. `camera.frame_epochs` implements:

```text
t_i = str2et(START_TIME) + START_TIME_BIAS
      + i × (INTERFRAME_DELAY + INTERFRAME_DELTA) + dt_refined
```

The bias and interframe delta come from the instrument kernel; the delay
comes from the label. `dt_refined` is one fitted offset for the whole image.
There is no additional unconditional half-exposure correction. JIRAM's
rule of using its label `START_TIME` exactly (section 4.4) still applies to
JIRAM; copying that rule into this camera would drop explicit timing terms.

[geometry.py](src/jiram_catalog/junocam/geometry.py) reuses the established
observer-reference, light-time, stellar-aberration and ellipsoid-intercept
helpers. It changes the rays and frame contexts rather than creating a
second definition of Jupiter. Arrays have the same leading frame/band shape
as the decoded image. Work proceeds one band of one frame at a time, but the
returned geometry can still be large; processing multiple full products
simultaneously is not a free consequence of having a generator somewhere.

**Limb fitting is an observation with limitations.**
[limb.py](src/jiram_catalog/junocam/limb.py) compares an observed intensity
edge with the predicted ellipsoid limb. The predicted edge uses an
interpolated zero crossing of the ray-intercept quadratic's discriminant.
Using the first `True` row of an intercept mask would quantize the objective
into whole-pixel steps and spoil the timing fit.

The code detects against nominal geometry, fits an offset, re-detects
against the improved prediction, then fits again. Golden-section search
minimizes a robust absolute residual over bounded timing offsets. Too few
usable limb points leaves the image unrefined; it does not produce a
fabricated zero-error navigation result. The known PJ4 timing offsets of
roughly 1–26 ms and approximately half-pixel residuals are historical
measurements in [the geometry report](docs/reports/junocam_pj4_geometry.md).

That report also records different apparent heights for leading and
trailing limbs, and band-registration evidence favoring a different
interframe correction from the kernel's published value. These are stated
limitations. The implementation retains the kernel value; one fitted
per-image offset cannot remove a per-frame rate error or resolve haze
height. A small limb residual is not, by itself, a wind-accuracy certificate.

**Reprojection and validity.**
[reproject.reproject_image](src/jiram_catalog/junocam/reproject.py) asks where
each map cell projects onto each relevant frame and band, samples the source
bilinearly, and accumulates a mean plus contribution count. It uses the
stored per-frame transforms to invert the same geometry that generated the
footprint. Cells outside the camera strip, outside active columns, on the
hidden hemisphere, or without valid interpolation neighbors do not acquire
observations merely because a map grid has room for them.

[junocam/stacks.py](src/jiram_catalog/junocam/stacks.py) writes
`image(time,band,y,x)`; [junocam/strips.py](src/jiram_catalog/junocam/strips.py)
writes `image(band,y,x)` for one observation. Incidence and emission retain
the band axis. The shared `valid` map can mean that at least one band painted
a cell; it is not proof that every band measured it. Quantitative analysis
intersects this support with the selected band's finite image and available
illumination mask. RGB rendering uses the intersection of channel masks,
preventing a one-channel terminator fringe from appearing as a real color.
Current reflected-light products use an 88° incidence cutoff; JIRAM thermal
nightside emission must remain valid.

**Eligibility: quality evidence is separate from an attractive image.**
[quality.py](src/jiram_catalog/junocam/quality.py) contains measured signal
summaries and descriptive instrument epochs. The decision used by the
application is [policy.py](src/jiram_catalog/junocam/policy.py), configured
in [junocam_quality.yaml](configs/junocam_quality.yaml). Its
`failure-exclusion-v1` assessment returns a status, reasons and separate
navigation/signal/calibration fields. The status is one of `eligible`,
`excluded`, or `unassessed`. The latter two withhold pixels.

A shortened view of the decision is:

```text
documented exclusion or failed measured signal -> excluded
complete clean metrics + supported unaffected evidence -> eligible
otherwise -> unassessed
explicit geometry failure -> excluded
```

Complete clean metrics require affirmative `metrics_ok` and finite,
nonnegative measurements: streak index below 0.3, saturation fraction below
0.02, zero fraction below 0.999, and positive maximum DN. Explicit corruption,
content-free, invalid-radiometry and bloom flags can exclude a product;
label rationale is also checked. These thresholds are operational
screening choices recorded by policy, not estimates of all instrument error.
In particular, a bloom flag is a conservative measured exclusion and need
not identify a documented hardware incident.

The supported legacy sample is PJ4. A new unaffected clearance needs an
individual product/observation identifier, a reason and a source. Describing
a pass as *post-anneal*, assigning an A/B/C grade, or omitting a failure flag
does not clear it. Explicit errata rules identify affected parts of PJ47,
PJ48, PJ49, PJ73 and PJ74 and the content-free PJ75–80 interval. Other broad
periods without affirmative evidence remain unassessed; the implementation
does not guess recovery dates from a heat-treatment label.

Preferred catalog defaults require the latest **archive-known** RDR
version to be eligible. If a newer version is known but not locally
assessed, an older attractive image is not silently promoted as current.
Stored stacks are a separate access case: the loader filters source steps
against eligible versions and selects the preferred available eligible step
per observation within the file, preserving original bytes. This distinction
keeps a provenance-bearing historical product usable without pretending it
is the newest archive product. A corrected label time of a few milliseconds
does not make V01 and V02 independent observations.

The current snapshot contains **72 preferred eligible JunoCam observations**.
The earlier mapped set had 93 stems; 21 methane observations are additionally
withheld by existing bloom evidence. The preserved six-entry polar file is
presented as three unique observations. See the
[delivery record](docs/build_log_2026-09-07.md) for the count definitions and
the unchanged historical count-gate conflict. Neither 72 nor the policy's
legacy allowance is an archive-wide radiometric validation.

**Photometry is an explicit transform, not a new unit label.** Let
`μ0 = cos(i)` and `μ = cos(e)`, for incidence and emission angle. The
shared [normalization implementation](src/jiram_catalog/api/images.py)
applies:

```text
none:       I
Lambert:    I / max(μ0, 0.05)
Minnaert:   I / [max(μ0, 0.05)^k × max(μ, 0.05)^(k−1)]
flat:σ:     I / [Gσ(I × valid) / Gσ(valid)] × mean_valid(background)
```

The angular denominator is dimensionless. Correcting a DN image therefore
leaves a transformed DN field; it does not become a measured I/F map. The
cosine floor limits amplification, and the illuminated-pixel mask removes
the near-terminator region used by the product policy. Lambert and Minnaert
are simple photometric models, not cloud radiative-transfer inversions.
Scientific preparation refuses them for JIRAM thermal radiance. Flattening
is permitted for thermal images, but passes no solar-incidence mask to the
normalizer, so nightside thermal observations are retained.

`flat:σ` needs particular care. For browser images and display-limit samples,
σ is in pixels of the **served or sampled image**. Native scientific
statistics, comparison preparation and quantitative exports apply the same
kernel before spatial subsampling, so σ there is in **native map pixels**.
At stride five, `flat:32` in a display corresponds to a broader native
smoothing scale than `flat:32` in a native diagnostic. These are not the same
filter merely because their selector text matches. If extending the
interface to compare them quantitatively, carry pixel size and stride into
the recipe rather than implying equivalent transfer functions.

A display stretch is another operation again: percentile limits followed
by linear/asinh mapping into image bytes. RGB channels may use separate
limits. Such choices help inspect clouds, but a spectrum must use the
selected physical-band array and recorded normalization, never the colored
canvas or an RGB composite.

**The PDS calibrated collection: a reference with a different observation
operator.** The completed
[bounded audit](docs/reports/junocam_calibrated_assessment_2026-09-07.md)
examined actual labels, three GeoTIFFs and FITS metadata/rows from
`junocam_atm-ml-calib`. Its inventory contains 37,386 tiles from PJ13–36;
those are overlapping tiles, not that many independent exposures. The
sampled TIFFs are 256×256×5, with 62.5 km grid spacing, generated channels
and nil/inapplicable acquisition times. Their LAEA projection uses a
planetographic reference, requiring explicit conversion before use with the
repository's planetocentric maps. A sampled global mosaic spans about
130 minutes, not one simultaneous observation.

The production pipeline includes flattening, projection, mosaicking and
learned transformations toward Hubble-like channels, including UV and
methane predictions. Those steps can alter variance and texture. The audit
also found channel-description, scaling and invalid-value ambiguities;
finite values alone do not establish observed coverage. The native tables
were not changed and no quantitative importer was certified. Coverage links
the collection as a supplemental morphology/reference source. It is a poor
replacement for exact-time native motion inputs or native radiance spectra,
and plausible generated pixels do not establish recovery from instrument
failure. A matched native/derived comparison would be required to measure
its transfer function; visual smoothness alone does not supply that result.

**What to test when changing this branch.** Archive tests protect version
identity and level classification; camera tests protect detector centers,
distortion and the timing equation; geometry/reprojection gates compare to
independent SPICE and the documented PJ4 sample. The new policy tests also
try missing evidence and direct pixel access. This last class is crucial:
a correct hidden-row filter can coexist with an incorrect thumbnail or
historical-movie route unless the routes are tested directly.

### 4.19. Scientific workflow — what the data can support

**Purpose.** [science.py](src/jiram_catalog/science.py) and
[api/science.py](src/jiram_catalog/api/science.py) turn a chosen map product
into a question with explicit prerequisites. Their role is similar to
checking an ocean-model diagnostic's grid staggering, wet mask, units and
sampling interval before applying an otherwise correct estimator. A valid
FFT or correlation routine cannot repair the wrong input interpretation.

The service always enters through policy-aware stack/strip loaders. Requests
identify a catalog strip or resolved stack, not an arbitrary client file
path. A physical band is required when multiple bands exist; `RGB` is a
display composite and is rejected as a quantitative band. Native JunoCam DN
and JIRAM radiance units are preserved with the transformation and sources.
Unknown values become JSON `null`, rather than NaN/Infinity tokens or a
fabricated numerical score.

**Readiness is a sequence of explicit checks.** `stack_readiness` first
selects one band and collapses processing versions by observation identity.
Identity wins over small corrections to label time. It sorts the selected
observations, requires a positive finite `km_per_px`, checks increasing
regular square coordinates when provided, and requires a three-dimensional
`image(time,y,x)` after selection. Datetime integers are explicitly converted
to nanosecond units before differencing; assuming that a pandas integer time
axis always counts nanoseconds was one of the bugs the new tests caught.

The temporal test rejects nonpositive gaps and searches for runs whose gaps
stay within the chosen relative tolerance of the run median. The default is
at least three observations and 5% tolerance. Each candidate run then gets
a one-frame-at-a-time mask intersection:

```text
common_run(y,x) = AND over t [valid_t AND finite(image_t)
                            AND selected-band illumination support]
```

A run with no common valid pixel does not become an export. Masks are
inspected without loading the whole time cube at once; metadata checks do
not first call `prepare_stack` and allocate a second full cube. On success,
the response records chosen band, normalization, units, times, gaps, retained
runs, common-valid fractions, removed versions and provenance.

Passing says that the arrays have the required format, grid, temporal
spacing and some common support. It does **not** establish cloud-feature
persistence, subpixel navigation accuracy, sufficient displacement signal,
or trustworthy winds. For example, a single common pixel is enough to
avoid an empty-mask error, but cannot make a useful full-field velocity
retrieval. The actual JunoCam polar selection fails earlier on cadence:
three observations at roughly 577/243-second intervals do not pass the
regular-triple test. The UI keeps that explanation visible.

`export_stack` repeats preflight before creating the requested destination,
then prepares and writes one retained run at a time. `spec.json` and each
realization's manifest record physical band, native units, normalization
and source provenance; `dataset_manifest.json` inventories the written
realizations, shapes and sampling intervals. The realization NetCDF contains
the image, validity and log-gradient
arrays, plus analysis provenance; there are no invented truth velocities.
The GUI validates destinations within its mirror export directory and uses
source/policy/settings-specific default names. Explicit nonempty output
directories are refused by the GUI job, preserving old exports rather than
mixing their obsolete realizations into a new result. The generic writer is
still a low-level function: callers outside that GUI boundary must manage
their destination and source eligibility deliberately.

**Comparison begins with a grid question.** Two equal-shaped arrays are
not necessarily maps of the same place. `equivalent_grids` requires matching
physical x/y coordinates and resolution, plus identical projection metadata
or verified per-cell latitude/longitude equivalence. Missing coordinates or
differing grids return `incompatible`; this endpoint does not invent a
reprojection or label an array-index shift as a geographic registration.
The frontend may show linked split/blink views for inspection, but the
numerical result keeps this distinction.

For equivalent grids, each native image is normalized first. Masked
normalized cross-correlation then runs on a subsample whose longest side
is at most 512 pixels. FFTs efficiently form the shifted cross-products;
means, variances and overlap counts are recomputed for each shifted mask
intersection. Edges do not wrap around. Search radius and overlap/variance
requirements are bounded, and a peak on the search boundary has its own
status rather than a confident displacement.

The returned `(dy_px, dx_px)` describes **motion from the left image to the
right image**, expressed in native map pixels after multiplying the sampled
integer shift by the sample stride. There is no subpixel refinement in this
endpoint. `sample_stride` and `sample_km_per_px` reveal the measurement
spacing. `common_valid_frac` is the intersection of the native masks, not
an estimate from the coarse correlation grid. Correlation measures pattern
registration; wavelength-dependent morphology, illumination, clouds at
different heights and navigation error can all affect its interpretation.

For an assumed speed `U`, map spacing `Δx` in metres and actual time
separation, the expected displacement is

```text
d_predicted_px = U × |Δt| / Δx
```

This is a planning calculation, not the measured shift. Navigation error
stays null unless supplied. If the user supplies an independent per-image
one-sigma error `σnav` in pixels, the reported velocity contribution is

```text
σvelocity = sqrt(2) × σnav × Δx / |Δt|
```

It is null at zero separation or without that supplied error. The formula
assumes independent errors in the two images; it is not an empirically
validated total wind uncertainty, and does not include all pattern-evolution
or correlation-estimator errors.

**A population is organized by what can be averaged.** `/population` takes
at most 100 strip identifiers and groups by instrument, physical band,
normalization, native resolution class **and native units**. Missing source
identifiers or pass metadata prevent an independence assessment and cause
an explicit exclusion. Missing/withheld inputs are reported in `excluded`
while eligible requested inputs can still be processed.

Higher versions are considered first. A strip sharing any observation
identity with an already retained strip is conservatively excluded, even
if it also contains other sources. Within a group, the existing native
`stats2d.strip_statistics` kernels compute spectra and structure functions.
Their physical k/r grids are interpolated onto a reference grid with NaN
outside support. These are summary-grid interpolations, not a claim that
two original map canvases were co-registered.

The key statistical decision is the averaging hierarchy. For pass `p`,
first average the retained strip spectra belonging to that pass; then give
each pass equal weight:

```text
E_pass,p(k) = mean of retained strip E(k) within pass p
E_population(k) = mean over passes of E_pass,p(k)
SE(k) = sample_std_over_passes(E_pass,p(k)) / sqrt(n_valid_passes(k))
```

The code uses the finite pass count separately at each bin. With fewer than
two passes, `E_stderr` is null. Ten overlapping strips in one perijove thus
do not create ten independent realizations or an artificial ten-sample
error bar. Treating a pass as the replication unit is itself a stated
statistical choice, not proof that atmosphere seen on different passes is
independent under every hypothesis. `n_observations`, `n_passes`, sources
and weights let the user assess that choice.

The older `stats2d.population_statistics` CLI routine discussed in section
4.14 averages strips directly and also provides bicoherence accumulation.
The new scientific API's independent-pass spectrum/S2 summary is a separate
aggregation layer over existing single-strip kernels; it should not be
mistaken for the same uncertainty estimator or advertised as a new GUI
bicoherence estimator.

**Masks and slope fits describe limitations; they do not remove them.**
`mask_diagnostics` measures finite valid fraction, four-neighbor connected
components, the fraction of valid pixels touching a mask boundary, the
1st–99th-percentile image range, and a row-discontinuity ratio. The last
compares the largest supported row-to-row mean absolute difference with the
median of those differences. A real cloud edge can make it large, so the
output asks for inspection rather than diagnosing every discontinuity as
a mosaic seam.

The scale record reports pixel spacing, field extent and a Nyquist wavelength
of twice the pixel spacing. **Effective physical resolution remains null**;
interpolation, optics, navigation and masking prevent grid spacing from
settling it. A fragmented mask can redistribute spectral power even when a
variance correction is applied. Counting its components does not invert that
spectral leakage.

`fit_spectrum` fits `log10(E) = slope × log10(k) + intercept` using only
positive finite bins inside the requested range. At least three bins and a
nonzero k range are required. The API caps the fit at the common Nyquist
bound; user-visible choices of fit range and normalization belong in the
recipe. The reported slope standard error is the ordinary regression error;
bins are correlated by tapering and masks, so this is distinct from the
independent-pass error in the mean spectrum. The UI's normalization
sensitivity comparison helps expose that dependence, without selecting a
preferred physical interpretation automatically.

**Candidates and vector overlays have weaker and stronger contracts.**
Cross-instrument `/matches` uses actual catalog times and seam-aware
latitude/longitude bounding boxes. Its overlap fraction is approximate
spherical-box intersection divided by the smaller box area, taking the best
band-pair overlap where a product has multiple band rows. This finds
plausible contemporaneous targets; it does not establish exact pixel overlap,
a common cloud altitude or registration compatibility.

The vector endpoint is stricter. It examines existing neighboring tracking
NetCDFs only when source-stack association, time, projection, explicit
kilometre coordinates, `m s-1` velocities and `map_xy` basis all match.
It bounds the displayed feature count and reports its sampling stride.
Published TP4 line/sample vectors are understood by the dedicated validation
script, but there is no general association of those files with arbitrary
stacks. The normal current response is therefore `unassessed` with a reason,
not assumed east/north arrows placed on the wrong grid. The browser's arrow
scale uses a stated time interval, converting `u,v` to map-kilometre segments.

**Reproducibility lives beside the number.** Population JSON caches under
`gui_cache/research/` are keyed by source file size/mtime, request settings,
current exclusions, source policy metadata and code-policy/revision fields.
Returned recipes preserve sources and versions, transform, units, masks,
known kernels, fit range and the averaging convention. Plot/numeric/recipe
exports in the frontend carry the analysis context; a pretty chart is not
used as the only durable record. Unknown kernel or navigation information
remains unknown rather than being filled from a broad mission assumption.

**Validation.** [test_review_science_offline.py](tests/test_review_science_offline.py)
checks corrected-version timestamps, a known signed shift, actual selected-band
NetCDF values/units/masks, failed exports creating no destination, equal-pass
means and uncertainty, policy exclusions, seam overlap and vector association.
The prewritten [science gate](tests/test_gate_review_science.py) checks
known-band/cadence cubes, power-law slope and fragmented masks. These tests
protect input semantics and estimator behavior. They add no new physical
wind validation beyond the JIRAM reference work described earlier.

---

## 5. Testing philosophy

There are two distinctions to keep separate: what evidence a test uses,
and who defined its acceptance criteria. **Offline tests** use hand-derived
cases, synthetic fields with known answers or small checked-in fixtures;
they need no mirror, paper data or network. The usual broad command is
`JIRAM_SKIP_GATES=1 uv run pytest -q`. **Physical validation tests** use
the real mirror, kernels or read-only published products, and can establish
agreement with a published mosaic, wind-vector field or independent SPICE
oracle.

**Gates** (`tests/test_gate_*.py`) are acceptance tests written before
implementation and kept read-only to executors. The original physical
gates need cluster data, but the newer review API/science gates use small
synthetic products and can run offline when invoked explicitly. The gate
filename therefore does not itself imply a mirror dependency. Equally, a
synthetic gate can establish cadence handling or estimator arithmetic
without establishing a measured wind's accuracy. A skipped test is not a
passed physical validation.

**Gates are read-only to every executor and every agent, always**
(`AGENTS.md`, `docs/agent_harness.md`) — "passing a gate by editing it
is task failure," stated identically in three separate project
documents. Only the project lead writes or revises a gate, and always
*before* dispatching the milestone the gate will check, specifically
so the gate cannot be shaped after the fact to match whatever the
implementation happens to produce.

**How the original physical gates get their ground truth.** Three main
sources support the JIRAM validation, and knowing which one explains what
a gate is actually capable of catching. (1) The published perijove-4
fixture (`tests/fixtures/pj4_ingersoll2022_map_labels.csv`, 48 rows,
one per Ingersoll et al. 2022 mosaic frame) — used by
`test_gate_pj4.py`, `test_gate_geometry_pj4.py`, `test_gate_geo_pj4.py`,
`test_gate_regions_pj4.py`, `test_gate_strips.py`,
`test_gate_trackability.py` — checks the pipeline against numbers a
peer-reviewed, independently-produced paper actually published. (2)
The published maps and TRACKER4 vector tables themselves, read
directly with `vicar.py` — used by `test_gate_paper_projection.py` and
`test_gate_tracking_pj4.py` — checks pixel-level and vector-level
agreement, not just scalar summary statistics. (3) A *fresh, direct*
SPICE oracle call, made independently of `geometry.py`'s own code path
— used inside `test_gate_geometry_pj4.py`'s
`test_vectorised_grid_matches_spice_oracle_at_sample_pixels` — checks
that the vectorised engine agrees with the textbook one-ray-at-a-time
SPICE call it is supposed to be a fast reimplementation of, which is
the only kind of check that can catch a bug the *vectorisation itself*
introduced (as opposed to a bug in the underlying physics, which
source (1) or (2) would also catch).

**What each gate specifically protects against**, briefly, tying back
to section 4: `test_gate_pj4.py` — that the crawler and label parser
produce the archive's declared frame count and sequence structure
faithfully; `test_gate_geometry_pj4.py` — that the vectorised engine
matches both the label and an independent oracle; `test_gate_geo_pj4.py`
— that geometry augmentation is correct on well-conditioned frames,
*and* (its rewritten form, below) that it is not silently corrupted by
partial-frame contamination; `test_gate_paper_projection.py` — that
the empirically-fit map grid actually reproduces a published mosaic
pixel-for-pixel, not just in a summary statistic; `test_gate_
regions_pj4.py` — that region selection, reprojection, compositing,
movie rendering, and goflow export all compose correctly end to end;
`test_gate_tracking_pj4.py` — that the geometry-and-reprojection
pipeline, exercised through an *independent* tracking tool, reproduces
a published wind field within a stated, honestly-measured tolerance;
`test_gate_strips.py` — that the strip library's chunking and grids
are mutually consistent with the frame geometry that built them;
`test_gate_trackability.py` — that repeat-view detection recovers the
paper's own known revisit cadence; `test_gate_api.py` — that the
FastAPI backend's catalog, stack, strip, and selection endpoints agree
with the Arrow catalog and the NetCDF stacks against real, full-scale
data; `test_gate_frontend.py` — that the built front-end bundle and
the backend it talks to reproduce the same behaviour end to end in a
real, GPU-less browser, not just synthetic fixtures; `test_gate_
cumulative.py` — that cumulative sweep stacks agree with the frame-
and sequence-level stacks at every point the three are required to
coincide.

**The current verification snapshot.** The September 7 delivery records
339 Python tests passed with 49 skipped under the offline command, 135
frontend tests passed, eight focused API/science gate checks passed, and
the full production browser gate passed against the real mirror
([delivery log](docs/build_log_2026-09-07.md)). These counts describe
different suites and should not be summed into one claim about physical
validation. The narrow mask-recipe wording correction made during this
documentation refresh was also followed by the same 339-pass, 49-skip
offline result and a check across the actual 88-degree boundary.

One historical gate conflict is preserved: `test_gate_junocam_products.py`
has two passing checks and one failing count assertion requiring at least
100 library products. The current policy admits 72 independent JunoCam
observations. Reintroducing duplicate versions or withheld images to satisfy
that count would damage the scientific input selection. The gate remains
unchanged and the conflict is reported explicitly; this is not an
unqualified all-gates-pass state.

**The two gates that were wrong, and how that was found.** Both cases
share a shape worth naming explicitly: the *code* under test was not
the thing that was wrong — the gate's own analytical *definition* of
what "correct" meant was measuring the wrong population, or optimising
the wrong target, and in both cases the mistake surfaced because the
gate itself failed, was investigated rather than loosened, and was
then rewritten by the project lead against the same ground truth with
a corrected definition.

*First: the geometry consistency report, section 4.6.* The original
all-orbit geometry consistency numbers folded together frames with
excellent SPICE-vs-label agreement and frames whose disagreement was
inflated purely by being *partial* (limb) frames, where the label's
own "centre" is a visible-area centroid, not a boresight intercept —
two genuinely different quantities that happen to share a column name.
Comparing them without excluding partial frames produces a residual
that looks worse than the geometry engine actually is. The build log
records the fix tersely: "geo gate rewritten by lead (well-conditioned
median 0.0078 deg; all-orbit report median was inflated by
partial/limb frames)" (`docs/build_log_2026-09-04.md`, step 8a'). The
gate in the repository today (`test_gate_geo_pj4.py`,
`test_geo_consistency_on_well_conditioned_frames`) is that corrected
version: it restricts its comparison explicitly to frames fully on the
planet with boresight emission below 40 degrees, and its own docstring
states the reason in one sentence — "the label centre is a
visible-area centroid for partial frames, so those are excluded by
construction."

*Second: the trackability best-baseline definition, section 4.15.* The
original spec's `best_dt_s` rule — the partner whose expected
displacement is closest to a target of 5 pixels — is not wrong on its
face; it failed one specific gate assertion on real data (14 of 15
checks passed on first run, `docs/build_log_2026-09-04.md`, step 9c')
because PJ4's long, densely-spaced north-polar campaign let a
far-in-time sequence coincidentally land closer to the 5-pixel target
than a frame's true nearest repeat. Rather than loosen the gate's
tolerance to let the coincidence through, the definition itself was
changed to "smallest `dt` among trackable partners" — a rule with no
equivalent failure mode — and the gate's own tolerance was widened
from an implicit exact match to 90 seconds, reflecting the real spread
of the (now correctly identified) sequence cadence rather than papering
over the original bug. `src/jiram_catalog/trackability.py`'s
`add_per_frame_stats` docstring narrates this revision in full,
including *why* the old rule could fail, which is exactly the kind of
context a comment justifying a formula rarely bothers to include and
which matters enormously to a future modifier tempted to "simplify"
the rule back to something that sounds more natural.

The general lesson, stated as the project's own rule rather than this
document's editorializing: when a gate fails, the response is to
*investigate why*, not to relax the threshold or edit the gate to
match whatever the code produced — `docs/specs/2026-09-05_tracking.md`
states this directly for its own tolerance ("If a threshold is not
met, do not tune parameters to pass; investigate ... and report what
limits agreement"), and it is the standing rule across every milestone
in this project, not a one-off instruction.

---

## 6. How it was built

This project follows one loop for every milestone, stated in full in
`docs/agent_harness.md`: measure the unknowns first (a reconnaissance
pass reports facts, never designs); the lead writes a spec — goal,
files in scope, a read-only list, every measured fact, every decision
made explicitly, the exact validation block that defines "done"; the
lead writes the gate *before* dispatching the spec, against ground
truth; one executor, fresh context, gets the spec as its entire brief;
the lead verifies (a scope check that the diff touches only the files
the spec named, the gates and full suite run by the lead, not trusted
from the executor's own report, every judgment call read and
adjudicated); and the outcome, the executor, the wall time, and the
gate result are all recorded in the build log. Every spec ends with a
verbatim clause: stop and list the ambiguities rather than choosing an
interpretation, and list every judgment call made, however minor, at
the end of the final report.

**What the executor tiers did in the original September 4–5 build.**
Three tiers appear in that build log, and the pattern across them is informative in
its own right, not just as a record. Reconnaissance and reader-facing
prose consistently went to a lighter, cheaper executor tier — the
initial archive and SPICE/geometry reconnaissance passes together cost
about 230,000 tokens and settled the two facts (label geometry
coverage, mirror-blind adequacy) the entire rest of the design rests
on (`docs/agent_harness.md`, "Experience so far"); the final
documentation pass (`README.md` and every top-level `docs/*.md` file,
1,477 lines total) took 26 minutes and about 297,000 tokens from the
same tier, with the lead's own fact-check afterward catching and
correcting exactly one number (the `orbit`-vs-`orbit_dir` disagreement
count, corrected to 3,900 rows, all traced to `orbit03` labels
claiming `ORBIT_NUMBER = 1`) — `docs/build_log_2026-09-04.md`, step
9h'. Mechanical code from an already-airtight spec went to the
cheapest executor that had already passed a gate on this exact
repository — the archive crawler and label index were delivered this
way, in one pass, against a gate written before the executor started
(`docs/build_log_2026-09-04.md`, step 3). Bounded numerical or
geometric work — anything touching SPICE, a projection, or requiring
fresh-context review of a subtle formula — went to a stronger executor
tier by design, stated as a standing routing rule in
`docs/agent_harness.md`: numerical or geometric work with a
SPICE/projection oracle, and fresh-context review, go to a stronger executor.
In that original build, the milestones after the geometry engine — geo
augmentation, the VICAR reader and reprojection engine, regions and
stacks, classical tracking, `stats2d`, the strip library, the GUI —
went to that stronger tier, each completing in 15 to 45 minutes with
one to three genuine spec ambiguities surfaced and resolved sensibly
per milestone (`docs/agent_harness.md`, "Experience so far").

**The stalls, and the ambiguity stops that were the system working
correctly.** The geometry engine milestone is the clearest single data
point for how this loop is supposed to behave under stress, discussed
already in section 4.4: the cheaper executor tier was dispatched three
times, and on the first two attempts it did exactly the right thing —
it hit a genuine arithmetic ambiguity in the spec and *stopped*,
rather than guessing an interpretation and pressing on — before, on
the third attempt, stalling for two hours and twelve minutes and
producing nothing at all. That third failure mode (silent non-progress,
not a reported ambiguity) is the one the harness has no automatic
detector for; it was caught only because the lead was watching wall
time against the other milestones' typical 15–45 minute completion
window and re-routed the spec to a stronger tier, which then delivered
a working engine — the stellar-aberration fix from section 4.4
included — in sixteen minutes (`docs/build_log_2026-09-04.md`, step
5'). The trackability milestone shows the *other* good failure mode,
discussed in full in section 5: a first pass that reported 14 of 15
gate checks passing, correctly identified by the lead as a wrong
definition rather than a code bug, revised, and re-verified — a
50-minute, roughly 328,000-token pass that included its own
self-correction as part of the delivered work, not a separate redo
cycle (`docs/build_log_2026-09-04.md`, step 9c').

**The byte-order and arithmetic errors executors actually caught.**
Two of the three archive facts in section 2 were caught by executors
working a spec that, at the time, still stated the *opposite* of what
turned out to be true, and both are worth naming as concrete
instances rather than abstractions. The little-endian discovery
(section 2.2) happened *inside* the geometry-augmentation milestone:
the executor gathering evidence for the dual-band half order read
256-line images and found the mismatch empirically — implausible
radiance values of order 1e37 under the documented byte order,
plausible ones under the opposite. The reprojection-fit gate, written
by the lead with the documented byte order, failed for exactly that
reason at the same time and confirmed the finding independently; the
lead corrected that gate and the spec's facts paragraph (which now
records the verification), and the fix propagated forward into every
later module that reads raw pixel data, with the original spec left
as-is and the correction recorded in `docs/decisions.md` rather than
the spec being silently edited after the fact. The stellar-aberration
arithmetic error (section 4.4) is the other clear instance: a specific,
measured, order-of-magnitude improvement (4e-3 deg to 8e-5 deg) from
correcting one line of the original spec's algorithm, found by an
executor checking its own output against the oracle the gate would
later also check against, not by a human noticing the formula looked
suspicious.

**The documentation fact-check.** The final documentation-pass
milestone is a useful case study specifically because its own
validation block *required* checking claims against the actual data
files, not just against the specs that described them — "every column
name in `docs/data_products.md` for `frames_geo.parquet`,
`strips.parquet`, and `trackability_frames.parquet` exists in the
actual file" (`docs/specs/2026-09-05_documentation.md`) — and the one
correction the lead made afterward (the `orbit`/`orbit_dir` count
above) came from exactly that kind of direct verification against the
mirrored Parquet files rather than from re-reading prose more
carefully.

**What the JunoCam expansion changed about verification.** The intervening
implementation history runs through the acquisition and native-product
work (`28cec1e`), geometry (`b385f88`), mapped-product integration
(`10c99f0`), photometry (`053dff1`) and the scientific workspace (`1e2723d`).
The detailed chronology is in the
[September 7 delivery log](docs/build_log_2026-09-07.md). Its teaching value
is in the assumptions these changes forced into explicit contracts.

First, an apparently useful six-frame polar stack became three observations
once processing versions were treated as versions. Its unequal time gaps
then failed the constant-cadence preflight. The right result was to keep a
viewable stack and refuse that export. Second, instrument-health evidence
became a separate condition from successful geometry or an attractive image:
failure exclusions and unassessed states now travel through catalog queries,
direct pixel access and derived products. A geometry grade or an annealing
event cannot stand in for an observation's signal assessment.

Third, the browser's apparent simplicity concealed scientific and systems
costs. Hidden views could still request native statistics; stale responses
could attach old pixels or diagnostics to a new selection; parallel NetCDF
work could touch unsafe shared C-library state. Lazy statistics, full request
identity and explicit I/O serialization address those different problems.
Bounded metadata reads make a display operation cheaper without changing the
native statistical estimators. This is a useful distinction for any large
ocean-model viewer: a responsive control plane does not make a full-field FFT
free, and serialization can make another image request wait.

Finally, the calibrated-collection audit demonstrates why an archive's
product name is a hypothesis about suitability, not the result of an
assessment. Its mosaicking and generated channels make it useful as a
reference while preventing an unqualified substitution into native variance,
spectral or motion analyses (section 4.18). All of these lessons concern
which observations and transformations a result represents, not just whether
the software can open the file.

**What this teaches about delegating numerical work.** Three patterns
recur clearly enough across this build log to state as general lessons,
not specific to this project. First, an executor that *stops* on a
genuine ambiguity rather than guessing is doing exactly what you want,
and should never be penalized for it by routing future similar work
away from that tier — the failure worth reacting to is silent
non-progress, which looks completely different (no report, no
ambiguity list, just elapsed time) and needs a different detector (a
wall-clock budget compared against similar milestones' typical
duration, watched by the party dispatching the work). Second, a gate
built from ground truth is only as good as its own definition of what
that ground truth actually measures — both wrong-gate stories in
section 5 are cases where the *code* was never the bug, and both were
only caught because someone was willing to distrust the gate itself
rather than assume a failing gate always means broken code underneath
it. Third, and most concretely: numerical or geometric work with a
genuine oracle to check against (a published paper's own numbers, an
independent SPICE call, a hand-derived synthetic case with a known
answer) is exactly the class of task where letting an executor work,
then verifying hard against that oracle, catches real, previously
undocumented errors in both directions — errors in the executor's
implementation, *and*, as happened twice in section 2, errors in the
project's own prior understanding of the archive it was building
against.

---

## 7. How to extend it: a worked example

The concrete task: add a new named region, `great_red_spot`, a
tangent-plane patch tracking Jupiter's most famous storm — and, to
exercise a second axis of extension in the same worked example, add a
new statistic to `stats2d.py`: the **skewness of the radiance field**,
`⟨(f - μ)³⟩ / σ³` over valid pixels, a single scalar per strip useful
for flagging strips dominated by a few bright storm cells versus ones
with a more Gaussian cloud-texture distribution. Neither change is
implemented in this codebase — this section is a specification of the
steps, in the same spec → gate → module → wiring → docs order every
real milestone in this project followed, so that a future contributor
(human or agent) extending this tool has a template to follow rather
than having to reverse-engineer the loop from the build log.

**Step 1 — facts, before anything is designed.** Both extensions need
one measured fact apiece before a spec can be written honestly. For
the region: the Great Red Spot's approximate centre latitude and
longitude at a chosen reference epoch (it drifts slowly in longitude
over years — `docs/reports/juno_mission_facts.md`-style literature
reconnaissance, or a quick query against `frames_with_geo` for frames
whose footprint historically covered the expected latitude band, would
settle a usable centre), and which orbits actually have mirrored image
data covering it (`frames_with_geo(mirror, orbits).query("bore_lat.
between(-25, -15)")`, cheap, no new code). For the statistic: a
sanity-check that skewness is even meaningful on strip-sized samples —
run it once, by hand, in a `uv run python` one-liner against a couple
of real strips already on the mirror, and note the typical magnitude
and whether the masked/valid-pixel handling `stats2d.py`'s existing
helpers already provide (`_prepare`, which separates a field into its
`float64` values and its finite-and-flagged validity mask) is
sufficient as-is, or needs a variant.

**Step 2 — the spec.** Following the shape every spec in
`docs/specs/` already uses: working directory and what to read first
(for the region: `regions.py`'s `local_ortho` docstring and
`configs/regions.yaml`'s existing `neb_15n` entry as a template; for
the statistic: `stats2d.py`'s module docstring and its `structure_
function` implementation as the closest existing pattern for a
masked, valid-pixels-only reduction); files in scope (for the region:
`configs/regions.yaml` only — no code touches a *named region entry*,
by design, since `RegionGrid.from_dict` already generalises to any
`local_ortho` centre; for the statistic: `stats2d.py`, plus
`tests/test_stats2d_offline.py`); a read-only list (everything else,
explicitly, matching how every prior spec in this project has scoped
concurrent or later work); the exact API addition
(`skewness(field, valid=None) -> float`, following `structure_
function`'s existing calling convention of accepting either a raw
array or anything with a `.values` attribute, so it composes with
`xr.DataArray` inputs the same way every other function in the module
does); and the validation block that defines done.

**Step 3 — the gate, written before either change is implemented.**
For the region: no numerical gate is really possible without a
published Great-Red-Spot wind or cloud-morphology dataset to compare
against (none is bundled with this project, unlike the PJ4 fixture),
so the honest gate here is structural rather than scientific — a small
offline test that `RegionGrid.from_yaml("great_red_spot")` loads, its
round trip (`pixel_to_latlon` then `latlon_to_pixel`) returns to within
`1e-6` px on visible points exactly as every existing `local_ortho`
region is tested (`tests/test_regions_offline.py`'s existing pattern,
extended with one more region name), and that `select_frames` against
the real mirror returns a non-empty selection for at least one
mirrored orbit — this is weaker than the physical reconstruction gates in section 5 by
necessity, and the spec should say so explicitly rather than implying
a false equivalence with, say, `test_gate_paper_projection.py`. For
the statistic: a genuine numerical gate *is* possible, following
`stats2d.py`'s own established pattern exactly — a synthetic field
with a known, hand-computable skewness (a deliberately asymmetric
distribution, e.g. an exponential-tailed field built the same way the
existing power-law test builds a field with a known spectral slope),
checked to a tight numerical tolerance, plus the existing zero-skewness
check on a symmetric (Gaussian) synthetic field as a negative control.

**Step 4 — the module change.** For the region, this is a four-line
YAML addition to `configs/regions.yaml`, following the `neb_15n` entry
exactly: `name`, `projection: local_ortho`, `km_per_px` (a storm of
this size is well served by something in the 10–20 km/px range, well
inside the existing `RESOLUTION_CLASSES` used by strips, though a
named *region* is not itself constrained to that list — only strips
are), `center`/`center_pixel`, and a `description` field explaining the
choice of centre and epoch the same way every existing entry's
`description` documents its own provenance. No Python code changes at
all — this is the entire point of `RegionGrid.from_dict` generalising
the way it does, and is exactly why section 4.9 called this out as the
one place a genuinely new named target is a one-line addition rather
than a new code path. For the statistic, `skewness` is added to
`stats2d.py` next to `structure_function`, reusing `_prepare` for
masking exactly as every other function in the module does, and wired
into `strip_statistics`'s output Dataset as one new scalar attribute
(not a new dimensioned variable, since it has no `k` or `r` axis to
live on) alongside the existing `variance` attribute it already
carries.

**Step 5 — wiring.** The region needs no CLI change at all — `jiram-
catalog regions` already lists every entry in the registry, and
`region-stack --region great_red_spot ...` works the moment the YAML
entry exists, because `RegionGrid.from_yaml` is a generic lookup with
no hard-coded region name list anywhere in `cli.py`. The statistic
needs one addition to `stats2d.py`'s own `strip_statistics` function
(already inside the module the change lives in, so this is not a
cross-module wiring step in the `cli.py`-avoidance sense section 4.17
described — `stats2d.py` already owns its own subcommand via
`add_subparser`, and a new scalar attribute on its output Dataset
needs no separate CLI plumbing).

**Step 6 — docs.** `docs/data_products.md`'s strip-statistics section
gains one line for the new `skewness` attribute, in the same units-and-
meaning style every other attribute there is documented in;
`docs/architecture.md`'s region-registry mention gains
`great_red_spot` to its list of named regions; `docs/open_items.md`
gains a line noting that no published ground truth exists yet to
gate the new region scientifically, exactly as this document's own
section 7 states above, so a future contributor does not mistake the
structural gate for a scientific validation.

**Applying the example to the expanded workspace.** The region-building
example above follows the JIRAM path. A JunoCam extension must use its own
builder, preserve the physical band axis and source observation identities,
and retain its timing and quality policy. A new browser statistic should be
requested only when its panel is opened, computed from the documented native
field and mask, and tied to the complete source/band/normalization request
identity. Its NetCDF reads belong inside the existing I/O guard. If it joins
a population summary, define the independent unit and missing-bin behavior
before choosing an error bar; the current API uses pass-level replication
(section 4.19). These are extension contracts, not optional presentation
details.

**What this worked example is meant to teach.** The two extensions
were chosen to be asymmetric on purpose. Adding a region needed almost
no code, because `regions.py` was designed from the start to generalise
past its own initial four entries (section 4.9); this is a sign the
original design correctly identified where genuine extensibility would
be needed. Adding a statistic needed real code, but slotted directly
into an existing, well-established pattern (`_prepare`, the module's
recipe of detrend-fill-window-correct) rather than requiring a new one
— also a sign of a module whose internal structure was built to be
extended, not just to solve the one problem in front of it at the
time. A worked example that required inventing a wholly new
architectural pattern to add either feature would have been evidence
of a design that had not anticipated its own growth; that neither
extension needed one is, itself, a finding about the codebase, not
just a tutorial.

---

## 8. Glossary

Planetary-archive and SPICE terms, in the order a new reader is likely
to need them; each entry says what the term means *in this codebase*,
not the fullest possible general definition.

- **PDS / PDS3 / PDS4.** NASA's Planetary Data System, the archive this
  project mirrors from. PDS3 uses plain `KEY = VALUE` labels; PDS4 uses
  XML labels and bundle/collection organization. The JIRAM crawler follows
  the `data_calibrated/orbitNN/...` bundle while reading its legacy `.LBL`
  companions. Native JunoCam uses indexed PDS3 volumes and its own crawler.
  The separate calibrated JunoCam PDS4 collection contains derived maps;
  the archive packaging alone says nothing about interchangeability with
  native camera measurements (section 4.18).
- **RDR / EDR.** Reduced Data Record versus Experiment Data Record.
  Processing level and physical units are distinct properties. JIRAM's
  calibrated images carry radiance. Native JunoCam RDR images decode as
  big-endian unsigned 16-bit values and remain DN in this project's mapped
  products; its reader also supports unsigned 8-bit EDR plus the archive
  decompanding table. An RDR label is not an absolute I/F certification.
- **Frame / framelet / swath.** In JunoCam, a timed detector readout
  contains filter-specific 128-row framelets. Multiple readouts sample the
  scene as the spacecraft spins. A mapped swath combines those contributions
  on a surface grid. These internal frames are not independent repeat visits
  merely because their times differ. JIRAM's frame and sequence conventions
  are described separately in section 2.
- **Observation identity / processing version.** `_V01` and `_V02` can
  name alternate reductions of the same observation. Corrections to a version's
  timestamp do not make it a new exposure. Deduplication protects cadence and
  sample size; latest-known-version policy also prevents an older local copy
  from silently substituting for a newer unassessed archive version.
- **DN / decompanding / I/F.** DN is the detector's digital numerical
  scale. Decompanding reverses the archive's nonlinear count encoding; it
  does not by itself supply absolute radiometry or undo instrument damage.
  I/F is a dimensionless radiometric reflectance convention. This code's
  JunoCam DN products must not be relabeled I/F because they have been
  decompanded, illumination-normalized or visually stretched.
- **Eligible / excluded / unassessed.** The JunoCam access policy's
  distinct evidence states. Eligible means the required current checks have
  supporting evidence; excluded records a known disqualifying condition;
  unassessed records missing evidence. Both latter states withhold pixels.
  Signal health, navigation and absolute calibration remain separate claims.
- **SPICE.** NASA/NAIF's toolkit (and file-format family) for
  spacecraft geometry: where things were, how they were pointed, and
  what time it was, all computed from a set of loaded binary and text
  *kernels*. `spiceypy` is the Python binding this project calls
  directly.
- **Kernel (SPK/CK/FK/IK/SCLK/LSK/PCK).** A SPICE input file, one of
  seven kinds this project loads. **SPK** — trajectory (where the
  spacecraft/planet/sun was). **CK** — attitude (how the spacecraft
  was oriented; "C-kernel" for historical reasons). **FK** — frame
  definitions (how named coordinate frames, like the JIRAM band
  frames, relate to the spacecraft body). **IK** — instrument kernel
  (per-pixel field-of-view geometry, boresight direction, IFOV). **SCLK**
  — spacecraft clock correlation (converts the onboard clock string in
  a label to a SPICE ephemeris time). **LSK** — leap-seconds kernel
  (UTC ↔ ephemeris time). **PCK** — planetary constants (Jupiter's
  radii, rotation state).
- **Furnsh / kernel pool.** SPICE's global, C-library-level state:
  `furnsh` loads a kernel file into that shared pool, and every SPICE
  call afterward implicitly reads from whatever is currently loaded.
  This is why `geometry.KernelSet` exists (to track and cleanly unload
  what one code path loaded) and why every parallel worker in this
  project uses `spawn`, never `fork` (section 4.6): the pool is
  per-process state that a forked child would inherit in a broken,
  half-initialized form.
- **Ephemeris time (ET) / epoch.** SPICE's internal continuous time
  scale (seconds past a fixed reference epoch, accounting for leap
  seconds); `spiceypy.str2et` converts an ISO-format UTC string to it.
  JIRAM uses its label's `START_TIME` exactly, without a mid-exposure
  offset (sections 2 and 4.4). JunoCam's `frame_epochs` adds its instrument
  bias, frame delay and optional limb-refined offset (section 4.18).
- **Boresight.** The optical axis a camera or instrument points along
  — for JIRAM, the direction `(0, 0, 1)` in its own band frame, by
  definition the centre of the field of view.
- **Sincpt.** SPICE's "surface intercept" function: given an observer,
  a target body, an epoch, and a ray direction, find where (if
  anywhere) that ray hits the target's surface. `geometry.py`'s engine
  reimplements the same computation vectorised over an entire pixel
  grid at once, rather than calling `sincpt` once per pixel.
- **Pxform.** SPICE's frame-rotation function: the 3×3 rotation matrix
  that converts a vector from one named coordinate frame into another
  at a given time. The backbone of the frame-chain diagram in
  section 4.4 and the "Sketch" figure that accompanies it.
- **Light time / stellar aberration ("LT+S").** Two related but
  distinct corrections for a moving observer looking at a distant
  target: light time accounts for the finite speed of light (you see
  where the target *was*, not where it is now); stellar aberration
  accounts for the observer's own velocity bending the apparent
  direction of an incoming ray (the same effect that makes rain appear
  to fall at an angle to someone running through it). `"LT+S"` is
  SPICE's aberration-correction flag requesting both; getting the
  *inverse* of the stellar-aberration half consistently applied to
  both the pixel rays and the observer's position vector is section
  4.4's central implementation subtlety.
- **Planetocentric vs. planetographic latitude.** Two different ways
  to define "latitude" on a non-spherical body. Planetocentric is the
  angle from the equatorial plane to the vector from the body's
  *centre* to the surface point — a simple, unambiguous geometric
  definition, and the convention used by this project's native mapped
  products (`docs/decisions.md`). Planetographic
  instead uses the local *surface normal* direction, which for an
  oblate body like Jupiter gives a numerically different value at the
  same physical point. The audited external calibrated JunoCam tiles use
  planetographic coordinates, one reason their grid cannot be treated as a
  native-map replacement without an explicit coordinate transformation.
- **East-positive longitude, `[0, 360)`.** This project's other
  fixed geometric convention, matching the archive's own label
  convention exactly (`docs/decisions.md`): longitude increases toward
  the east, wrapped to stay within a half-open `[0, 360)` degree
  range, never signed or centred on the anti-meridian.
- **System III.** Jupiter's standard body-fixed rotating longitude
  system, tied to the planet's magnetic field rotation rather than any
  visible atmospheric feature — mentioned in early project planning
  (`docs/brainstorm_2026-09-03.md`) as the natural longitude frame for
  a giant planet with no solid surface to define rotation against; in
  practice, `IAU_JUPITER` (the SPICE-defined body-fixed frame this
  project actually uses throughout) *is* the System III frame for
  Jupiter, so the two names refer to the same physical convention.
- **Emission angle / incidence angle / phase angle.** Three angles
  that describe viewing and lighting geometry at one surface point.
  Emission angle: the angle between the local surface normal and the
  direction *to the observer* (0° = looking straight down, 90° = along
  the limb, grazing). Incidence angle: the same, but to the *Sun*
  (0° = sun straight overhead, past 90° = the surface point is in
  shadow, on the night side). Phase angle: the angle at the *surface
  point* between the observer direction and the Sun direction (0° =
  observer and Sun in exactly the same direction from the target,
  i.e. viewing a fully-lit "full" disc; 180° = observer and Sun
  opposite, viewing an unlit disc from the far side of the light).
- **Orthographic / stereographic / equidistant / Lambert
  (azimuthal) projection.** Four different ways to relate a distance
  on a flat map (measured from a chosen pole or centre) to the true
  angular distance (colatitude) on a sphere or ellipsoid; they agree
  to first order near the centre and diverge increasingly toward the
  edge of the map. Orthographic — literally the parallel-ray shadow of
  the curved surface onto a flat plane — is the one the published
  perijove-4 maps this project validates against actually use, despite
  the JIRAM literature's own description of them as stereographic
  (section 4.8's central finding).
- **VICAR.** JPL's older, self-contained image file format (an ASCII
  label at the start of the file, followed by raw pixel records), used
  here only because the published ground-truth maps and wind-vector
  tables happen to be shipped in it, not the PDS-native formats the
  rest of the archive uses (section 4.7).
- **NCC (normalised cross-correlation).** A similarity score between
  two image patches, `1.0` for identical patches (up to a scale and
  offset), used throughout this project both as the classical tracker's
  own match-quality metric (section 4.12) and as the general figure of
  merit for "does our reprojection match the published map" (section
  4.8, 4.10).
- **Normalization / stretch / `flat:sigma`.** Normalization transforms
  the physical scalar field using a documented rule; stretch maps that field
  to display intensities. Lambert and Minnaert address reflected-light
  illumination and are refused for thermal JIRAM. Flat normalization divides
  by a smoothed background while preserving the field's scale; sigma is in
  served pixels for images and native map pixels for scientific preparation.
  Neither operation establishes absolute calibration (section 4.18).
- **Readiness.** A checked contract for a particular export: physical band,
  unique observations, positive nearly constant cadence, usable grid and
  masks. Passing it establishes valid inputs to the requested operation,
  not accurate atmospheric winds (section 4.19).
- **Independent pass / standard error.** Population summaries first average
  strips within each pass, then give passes equal weight. Per-bin standard
  error uses the number of finite pass estimates and is unavailable with
  fewer than two. This reduces pseudoreplication from many strips of one pass;
  it does not prove that different passes are statistically independent.
- **Nyquist / effective resolution.** Twice the map spacing is the sampled
  grid's Nyquist wavelength. Optical blur, navigation and resampling can make
  the effective scientific resolution coarser. The interface states the
  former and leaves the latter unassessed when no measurement supports it.
- **`map_xy` vectors.** Components along the product's own map x and y
  axes, with explicit coordinate and velocity units. They are not silently
  treated as east/north components. The vector reader requires association
  metadata before overlaying a neighboring file.
- **Request identity / provenance.** A result belongs to a source and its
  processing settings, including physical band and normalization. Browser
  request identity prevents late responses from replacing a new selection;
  saved provenance and source/policy signatures make a derived result's
  inputs inspectable and invalidate caches when those inputs change.
- **`geo_ok` / `on_planet_frac` / `bore_*`.** Column-naming conventions
  used consistently across `frames_geo.parquet`,
  `trackability_*.parquet`, and every downstream module's own
  DataFrames: `geo_ok` means the SPICE computation for that frame
  succeeded at all (never a statement about accuracy, only about
  whether an answer exists); `on_planet_frac` is the fraction of a
  frame's pixels that actually intersect the ellipsoid; a `bore_`
  prefix (`bore_lat`, `bore_emission`, ...) always means "evaluated at
  the boresight specifically," distinct from a frame-wide mean, min,
  or max of the same underlying quantity.

**Refresh judgment calls.** The original JIRAM walkthrough and section
numbers are retained as foundations and validation history, while current
JunoCam and scientific-workflow contracts are integrated throughout.
Measured navigation, signal eligibility and absolute calibration remain
separate claims; missing evidence stays unassessed. The derived calibrated
collection is treated as a reference, and the old minimum-count gate
conflict is reported without changing its assertion. These choices favor
traceable inputs and explicit limits over larger inventories or unsupported
scientific certainty.
