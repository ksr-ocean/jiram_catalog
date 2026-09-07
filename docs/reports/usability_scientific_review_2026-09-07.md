# Usability, design, and scientific-value review

Reviewed 2026-09-07. **Assessment and proposed backlog; implementation is not
approved by this document.** Scope: [review spec](../specs/2026-09-07_usability_scientific_review.md).

## Overall judgment

This is a well-structured scientific toolkit with a functional browser, but
its interface still asks the user to understand the pipeline to use it well.
The strongest work is the geometry, explicit conventions, reproducible data
products, and validation record. The weakest parts are discovery, communicating
what is actually available, and guiding a selection into a scientifically
appropriate analysis.

The aesthetics are coherent and restrained: dark neutral panels, a consistent
blue accent, sensible scientific colour maps, and little decoration. However,
almost everything receives the same visual weight. Small text, crowded
controls, long identifiers, extensive borders, and permanently reserved side
panels make the application feel like an engineering console. The data should
occupy more space and the scientific question should determine which controls
appear.

One layout failure is especially tangible: at 1440 × 900, opening a JunoCam
strip's statistics compresses the image viewer to a thin sliver between the
toolbar and the plots. The charts render, but inspecting the field and its
statistics together becomes impractical. See the
[image view](figures/usability_2026-09-07/09_junocam_strip.png) and
[statistics-open view](figures/usability_2026-09-07/10_junocam_statistics.png).

**JunoCam is usable for exploratory viewing of the local PJ4 sample. It is
not yet a complete JunoCam archive browser or a dependable input workflow for
velocity retrieval.** That conclusion rests on actual data and browser
behaviour, not merely on missing planned features.

## Evidence and limits

Read the project rules, architecture, decisions, open items, GUI guidance,
JunoCam research and validation reports, and the relevant Python/React code.
Inspected the existing mirror on `exp-1-47`, served the committed front end on
loopback, and used Chromium with software WebGL at 1440 × 900 and 1366 × 768.
Exercised catalog filtering, JunoCam single-band and RGB stack viewing, and a
JunoCam strip with its statistics panel. Queried existing product metadata and
the cadence selector. The primary browser traversal reported no JavaScript
page errors; separate targeted checks exposed the detail-route failure.
Primary archive and instrument sources were checked on the web.

Screenshots are in [the review evidence directory](figures/usability_2026-09-07/).
These are actual browser captures, not proposed mockups. The initial catalog
capture was taken while the stack list and summary were still loading; its
empty charts are a transient loading state. Cold reads took substantial time
in this session, but no controlled performance benchmark isolated application,
filesystem, or software-rendering costs.

No scientific products were rebuilt, no export or build jobs were submitted,
and no saved selections were changed. Browser reads can populate the existing
GUI cache. No application code, gates, fixtures, dependencies, or generated
bundle were changed. This review did not rerun the scientific acceptance gates
or the full test suite; it is not a new validation of absolute navigation,
photometry, or wind accuracy.

## Organization and intuitiveness

The repository layout is sound. `src/jiram_catalog/` separates acquisition,
indexing, geometry, reprojection, products, statistics, and API routes;
`junocam/` isolates its different camera and image model. `frontend/` is the
source of the committed browser build. Configurations, tests, specs, reports,
and data are separated. The decision log and ground-truth gates are valuable
assets worth preserving.

The documentation is extensive but no longer synchronized with capability.
The main README and architecture foreground JIRAM, while JunoCam acquisition,
quality, geometry, photometry, and multi-band viewers now exist. The document
index omits several recent JunoCam specs/reports. The claim that every feature
is validated against the PJ4 JIRAM reference should not be read as validation
of JunoCam or of every later analysis path. A short capability matrix would
be more useful to a collaborator than another chronological build narrative.

The three views make sense after reading the guide, but their labels describe
internal products: **Catalog / Poles / Strips**. The Poles view already lists a
`neb_15n` stack. I would use **Explore / Time series / Image library**, with
short explanations and presets underneath. Keep the current CLI names and file
formats; this is a change in presentation, not a request to reorganize the
scientific engine.

The selection tray is a useful persistent object. Its fixed 300-pixel column
is wasteful when empty, especially beside the viewer's additional 320-pixel
metadata column. “Show in Strips” transfers only selected **orbits**, not the
selected footprints or observations. That action should say “Show library
images from these orbits,” or carry the actual region/time/instrument query.

## JunoCam: what exists locally

These numbers were measured from the mirror during this review, not copied
from the historical README. An archived product version is not necessarily a
new observation.

| Layer | Observed state | Meaning for use |
| --- | --- | --- |
| Archive manifest | 82,668 preferred EDR/RDR product records, including 41,334 RDR records; 84,332 records when versions are included | Broad archive discovery already exists below the GUI. The browser does not expose this inventory as a searchable list of unprocessed observations. These are manifest records, not locally available images. |
| Label index | 3,788 EDR/RDR rows: 2,080 for PJ4 and 1,708 for PJ58 | Metadata coverage exceeds image coverage; this is not a mission-wide index of mirrored labels. |
| Image presence in that index | 122 PJ4 RDR images; no EDR pixels or PJ58 pixels marked present | PJ58 labels do not imply viewable PJ58 imagery. Presence was read from the index, not reverified with archive-wide checksums. |
| Geometry and quality tables | 122 PJ4 rows; 112 `geo_ok`, 10 with no sampled on-planet pixel; all 122 graded A | The visible catalog represents 112 versions of 93 distinct observation stems. A is a local heuristic grade. |
| Browser | 47,771 combined catalog rows, including those 112 JunoCam entries; 5 stacks; 305 strips | The large headline count is mostly JIRAM and mixes band-half rows with whole JunoCam swaths. |
| JunoCam polar stack | One RGB stack on the paper region, 6 steps at 15 km/pixel | Only 3 distinct observations: products 04C00099, 04C00100, 04C00101, each in V01 and V02. |
| JunoCam strips | 16 strip products representing 8 observation stems; the other 289 strips are JIRAM | A JunoCam strip and its statistics successfully opened. Versions also inflate the apparent library size. |

The six stack timestamps are:

```
2017-02-02 12:29:08.530    04C00099 V01
2017-02-02 12:29:08.538    04C00099 V02
2017-02-02 12:38:45.408    04C00100 V01
2017-02-02 12:38:45.416    04C00100 V02
2017-02-02 12:42:48.170    04C00101 V01
2017-02-02 12:42:48.185    04C00101 V02
```

The existing `constant_cadence_runs` function returns **no runs** on this time
axis with its defaults. Removing versions alone would not create a regular
three-image sequence: the distinct observations have unequal separations.
This says nothing about whether other JunoCam observations could form useful
tracking pairs or triples.

## JunoCam: concrete problems

| ID | Finding and evidence | Consequence |
| --- | --- | --- |
| JC-01 | Catalog rows display `1970-01-01 00:00:00`. `api/data.py:junocam_catalog` supplies `start_time` but omits `start_time_ms`; `api/catalog.py:catalog_frame` reindexes it into the common schema, and `api/arrow.py:_column` fills missing integers with zero. [Capture](figures/usability_2026-09-07/08_junocam_dates.png). | Displayed UTC and client-derived year/CSV values are wrong. Server summary dates can disagree with the table. |
| JC-02 | `GET /api/catalog/frame/JNCR_2017033_04C00105_V02` returns HTTP 404, “unknown product_id,” although the catalog contains it. `frame_detail` searches only `frames_with_geo`, the JIRAM table. | The product link cannot open JunoCam details. There is no working path there to inspect its label, provenance, or thumbnail. |
| JC-03 | The manifest layer already distinguishes latest products from all versions, but `junocam/stacks.py:image_table` consumes the all-version image index. The existing stack contains the version pairs above; the JunoCam strip table also visibly lists V01/V02 pairs. | Versions can masquerade as temporal samples and inflate population sample counts. Keep them for provenance but select one preferred version per observation by default. |
| JC-04 | `api/data.py:junocam_catalog` sets `has_partner=False`, `trackable_30=False`, and `best_dt_s=NaN` because the existing trackability table is JIRAM-only. Checking the live “same-pass revisit only” box reduces JunoCam from 112 to 0. | “Not assessed” is presented as a negative result. It conceals potential JunoCam science. |
| JC-05 | “Export triples” is enabled for the six-step RGB stack. The current cadence selector returns none. Separately, `export_goflow.py:write_realization` assumes a three-dimensional single-band image and fixed JIRAM radiance units; the API export job neither selects the displayed JunoCam band nor passes its normalization. | The control does not establish scientific or format readiness. A future regular multi-band stack would also need an explicit adapter. No export was submitted in this review. |
| JC-06 | Quality uses epoch, median streak index, and fraction at the storage maximum; the post-anneal epoch extends from PJ57 to PJ99. Zero fraction and noise are measured but not part of the tier rule. `quality_tier('post_anneal', 0, 0)` returns A. | An image lacking scene signal can pass this rule; A is not a guarantee of useful navigation, radiometry, or texture. This is a demonstrated rule weakness, not a claim that the current PJ4 set contains such failures. |
| JC-07 | `lib/filters.ts` applies latitude cuts to the boresight. JunoCam swaths can cover a region far from that point. The L/M half and JIRAM revisit controls remain available when JunoCam is selected. | A user seeking imagery covering a region can exclude relevant swaths, or accidentally empty the catalog with an irrelevant filter. |

## How well the component fits the real archive

The PDS EDR/RDR products are strips of sequential filter framelets, not ready
RGB photographs. The SIS documents decompanding and exposure/solar-distance
scaling of RDR data, with a white reference corresponding to 10,000 DN; its
processing description says flat-field correction is not implemented.
Consequently, a visually attractive RGB stretch or a Lambert correction is
not evidence of absolute photometric calibration. The repository's separate
camera model, per-band reprojection, DN metadata, and illumination controls
are appropriate foundations. [PDS product specification](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0031/DOCUMENT/JUNO_JNC_EDR_RDR_DPSIS.HTM).

The NAIF kernel explicitly specifies distortion and frame timing corrections,
including a 61.88 ms start bias, 1 ms interframe addition, and possible timing
jitter. The implementation accounts for these and optionally refines timing
from the limb. Its existing PJ4 report gives a median post-fit limb residual
of 0.571 pixel on the 28 products with sufficient limb, and documents remaining
registration/rate effects. This supports exploratory placement; it does not
establish equivalent accuracy for every pass, methane image, or cloud motion.
[NAIF camera kernel](https://naif.jpl.nasa.gov/pub/naif/JUNO/kernels/ik/juno_junocam_v03.ti),
[local PJ4 validation report](junocam_pj4_geometry.md).

Late-mission quality needs product-level evidence. The archive explicitly
identifies nearly black, content-free PJ74 images whose RDR radiance mapping
is invalid; it also distinguishes usable images from damaged portions of
other passes. The local broad epoch rules cannot represent this adequately.
Instrument-team measurements show different throughput losses by colour,
which public image colour balancing can hide. These facts justify revising
the quality design before extending the same defaults to later passes.
[PDS release 33 errata](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0033/ERRATA.TXT),
[instrument-team throughput study](https://meetingorganizer.copernicus.org/EPSC-DPS2025/EPSC-DPS2025-1226.html).

There are useful external products that the app does not expose. Mission
Juno's map archive provides cylindrical and polar collections through PJ72;
its global products use 10 pixels/degree with System III longitude context.
These would make useful orientation and morphology references, with their
projection, coordinate convention, composition times, and processing credits
preserved. They should not automatically become quantitative pixel truth.
[Mission Juno maps](https://www.missionjuno.swri.edu/junocam/think-tank/maps-archive).

There is also a **PDS4 machine-learning calibration bundle**, version 1.1,
covering 2018-05-24 through 2021-09-03, with calibrated-image and mosaic
collections. This is a separate derived archive, not a replacement for the
primary PDS3 EDR/RDR volumes. It was missed by the earlier reconnaissance's
broad “no separate PDS4 bundle” conclusion. Its directory could not be fetched
in this review, so product schemas and numerical suitability remain
unverified. [PDS bundle record](https://pds.nasa.gov/ds-view/pds/viewBundle.jsp?identifier=urn:nasa:pds:junocam_atm-ml-calib&version=1.1).

The associated research describes frequency constraints intended to preserve
small-scale structure. For this project's spectra and optical-flow work, I
would evaluate that claim on independent examples before adopting these
products: compare displacement, spectral slopes, and uncertainty against the
originals. This is a proposed validation, not evidence that the method damages
or preserves all relevant signals. [Calibration-method paper](https://arxiv.org/abs/2511.22668).

| Intended use | Readiness judgment |
| --- | --- |
| Inspect PJ4 cloud morphology, single bands, RGB | Usable, with a cramped interface and limited sample. |
| Find available JunoCam observations across the mission | Incomplete: local mapped rows are PJ4-only and absent processing stages are not visible. |
| Compare repeat observations or derive winds | Not ready as an integrated workflow: identity, timing, overlap, registration, and export preflight need work. |
| Explore spatial texture statistics within an image | Promising; normalization, seams, mask geometry, native resolution, and fit range must accompany results. |
| Compare brightness or spectral amplitudes across passes | Not established by the present processing and quality grades. |
| Compare visible cloud morphology with JIRAM | A valuable next workflow, provided time, viewing geometry, resolution, and different measured quantities remain explicit. |

## Prioritized design backlog

P0 means address before trusting the affected scientific workflow. P1 improves
routine research use. P2 extends capability after those foundations work.
Each item needs its own accepted implementation spec and appropriate gates.

| Priority / ID | Proposed change | Scientific or usability benefit; completion criterion |
| --- | --- | --- |
| P0 / D01 | Repair JunoCam catalog dates and product details (JC-01/02). | UTC agrees between labels, table, tooltip, summary and CSV; every catalog product opens an instrument-aware inspector with preview, source links, bands, and processing metadata. |
| P0 / D02 | Make observation identity and preferred version explicit (JC-03). | Default stacks and populations contain one preferred version per observation; older versions remain selectable for comparison. Tests cover reprocessed timing labels and duplicate prevention. |
| P0 / D03 | Add analysis readiness and export preflight (JC-04/05). | Show assessed/unassessed/unsuitable separately. Before export, report unique observations, cadence, common illuminated area, selected band, normalization, units, grid and expected output count. Explain why this current stack produces zero triples. Validate the downstream file contract. |
| P0 / D04 | Replace the single quality grade with explainable dimensions (JC-06). | Display navigation confidence, usable signal, saturation/wraparound, missing data, seams, and calibration status. Use documented per-product errata and measured scene metrics; benchmark against representative good and damaged observations. Unknown throughput stays unknown, rather than appearing as confirmed unity response. |
| P1 / D05 | Add a coverage and readiness view by instrument/pass. | Distinguish archive-known, labels indexed, pixels local, geometry available, quality assessed, and products built. PJ58 clearly reads “labels only.” Refresh/index age is visible. Discover new archive volumes instead of relying indefinitely on `LAST_VOLUME=35`. |
| P1 / D06 | Make exploration follow a question. | Presets for repeat cloud views, polar morphology, single-pass texture, and cross-instrument context. Use footprint-region intersection for “covers this region,” with optional boresight filtering clearly named. Hide inapplicable band-half/revisit controls. Expose thumbnails and label rationale so a pass is understandable without decoding filenames. |
| P1 / D07 | Give the image priority in the layout. | Collapsible selection and metadata drawers; a compact primary toolbar; advanced illumination/RGB controls on demand; 14–16 px primary UI text and clearer section spacing. Preserve a useful minimum image height when statistics open. At 1366 × 768, the main image and essential controls fit without the current columns crowding them. Add keyboard-operable rows, dialog focus/Escape handling, and visible focus states. |
| P1 / D08 | Redesign map density and geographic cues. | Start broad views with coverage density, then reveal selected/hovered footprints; keep an exact-outline mode. Label latitude and east-positive longitude and provide a meaningful legend. A selected JunoCam swath remains identifiable in the full catalog. Fit-to-valid-area and adjustable graticule density improve image inspection without changing stored grids. |
| P1 / D09 | Build a comparison workspace for tracking and morphology. | Linked pan/zoom, blink and split views, locked or explicitly independent stretches, common-mask overlay, time differences, native/output resolution, and registration residuals. Add published/derived vector overlays with units and provenance. Show projected displacement and navigation uncertainty before interpreting a velocity. |
| P1 / D10 | Turn statistics into a reproducible analysis product. | Expose population comparisons with independent observation counts, resolution/band/illumination matching, uncertainty appropriate to repeated passes, mask/seam diagnostics, fit-range selection and sensitivity to normalization. Distinguish radiance/reflectance texture spectra from velocity-energy spectra. Export figure plus numeric data and recipe, including units, grid, masks, versions, kernels and software revision. |
| P1 / D11 | Refresh the entry documentation and naming. | A JIRAM/JunoCam capability matrix, current example workflows, named limitations and validation scope; links to recent specs/reports. Explain Explore, Time series and Image library in terms of tasks. Keep source schemas and instrument adapters consistent so new instruments cannot omit shared fields unnoticed. |
| P2 / D12 | Integrate reference maps and evaluate derived calibration products. | Link out first; then add optional comparison layers with source/projection/time information. Gate quantitative use of learned calibration with spectrum and displacement checks. Match JIRAM/JunoCam by region and time with resolution and illumination context, rather than treating their bands as interchangeable measurements. |

For implementation order, I would start with D01–D04, then combine D05–D08
into a clearer exploration experience. D09 and D10 offer the largest further
scientific return for cloud tracking, the downstream optical-flow project,
and population statistics. D12 should follow a specific research question,
rather than trigger a bulk mirroring effort by itself.

## Judgment calls and ambiguities resolved

- Interpreted “my projects” using the documented cloud-tracking, optical-flow,
  and spatial-statistics goals. Other options were general outreach imagery
  or a broad planetary-data portal; neither was assumed to be the priority.
- Distinguished browsing usability, numerical correctness, and scientific
  readiness rather than assigning one numerical quality score. Working RGB
  rendering alone does not settle the other two.
- Used observation stems to identify processing versions; equal timestamps
  would miss the 8–15 ms label revisions. Preferred-version selection is
  proposed for analysis, while retaining every archived version for provenance.
- Preserved the existing camera, latitude, and projection decisions. Proposed
  more validation and clearer confidence display rather than changing the
  physical model during a design review.
- Treated broad quality epochs as prior information, not sufficient rejection
  or acceptance of every image. Both blanket exclusion of damaged-era passes
  and blanket acceptance of post-anneal data lose useful distinctions.
- Favoured an adaptable desktop scientific workspace over a mobile-first or
  decorative redesign. Screenshots support the layout judgment; no user study
  or calibrated accessibility audit was performed.
- Kept implementation, new products, and archive expansion out of this review.
  The owner requested an assessment and a durable list; accepting a design
  and executing it are separate subsequent steps under the repository rules.
