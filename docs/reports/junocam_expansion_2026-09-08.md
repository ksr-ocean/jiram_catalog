# JunoCam multi-pass expansion — 2026-09-08

Acquisition and individual assessment are complete: **194 additional preferred
RGB observations** from eight passes are now eligible. Together with the
unchanged 72 PJ4 observations, the live API exposes **266 distinct JunoCam
observations across nine passes**, in a 47,925-row joint catalog. All **102 new
strips and 13 polar stacks** are complete and independently verified. The
GUI exposes 110 preferred JunoCam strips, including the eight retained PJ4
strips, and all 13 new stacks.

## What was acquired and why

The previous single-pass view reflected a deliberately limited native-data
validation sample, not an archive limited to PJ4. The archive contains many
more observations, but an archive entry is not proof that local pixels,
supported navigation, acceptable instrument behavior or useful maps exist.

This batch prioritizes a spread of prime-mission passes with local JIRAM and
navigation support. The lead screened 337 preferred Jupiter RDR labels in
bounded encounter windows and acquired exactly 194 unsummed, complete RGB
products with determined archive timing. Each versioned ID, URL, size and
label/native MD5 appears in the committed
[selection table](junocam_expansion_selection_2026-09-08.csv). The other 143
candidates retain explicit reasons for exclusion from this batch. They were
not all declared defective: some are supported only by a different future
sampling or navigation implementation.

| Pass | Acquisition date UTC | New native RGB observations | Individually cleared | Native bytes |
| --- | --- | ---: | ---: | ---: |
| PJ5 | 2017-03-27 | 12 | 12 | 473,358,336 |
| PJ6 | 2017-05-19 | 32 | 32 | 1,197,318,144 |
| PJ8 | 2017-09-01 | 20 | 20 | 896,090,112 |
| PJ12 | 2018-04-01 | 19 | 19 | 817,618,944 |
| PJ18 | 2019-02-12 | 23 | 23 | 853,057,536 |
| PJ24 | 2019-12-26 | 18 | 18 | 688,521,216 |
| PJ30 | 2020-11-08 | 27 | 27 | 1,098,596,352 |
| PJ34 | 2021-06-08 | 43 | 43 | 1,830,150,144 |
| Added | 2017–2021 | **194** | **194** | **7,854,710,784** |

The native payload is **7.3153 GiB**, plus 498,967 bytes of labels. All 194
image sizes and every label/native MD5 matched. No whole-day download,
superseded version, EDR, methane product or derived ML tile was added.
The [inventory audit](junocam_expansion_inventory_2026-09-08.md) provides
exact windows, archive counts and local kernel/JIRAM checks. PJ24 already has
33 JIRAM strips overlapping the candidate window; native JIRAM files and
date-covering CK/SPK kernels exist for all eight selected passes. That does
not imply identical illumination, cloud height or valid-pixel overlap between
instruments.

## Evidence supporting the individual clearances

[NASA/JPL reports normal camera operation through the first 34 orbits](https://www.nasa.gov/missions/juno/nasa-shares-how-to-save-camera-370-million-miles-away-near-jupiter/).
That affirmative instrument-era evidence was combined with each exact product's
label/errata audit, supported dimensions, determined timing, verified bytes,
measured RGB signal and successful geometry. It was never used as an epoch
whitelist. The YAML names only the 194 approved product IDs; legacy PJ4 and
all existing failure exclusions remain intact. PJ58 and other unassessed or
failure-affected observations remain withheld.

Every new image passed the existing overall checks and the same finite
thresholds independently in each RGB band: streak below 0.3, saturation below
0.02 and zero fraction below 0.999, with positive image maximum and no bloom
flag. The largest per-band streak was 0.00579, saturation medians were zero
at the declared 16-bit maximum, and the largest zero median was 0.53011.
Image maxima spanned 2,368–29,519 DN. An independent agent reproduced these
results and inspected seven native examples, including the largest zero
fractions and smallest maxima; each band contained meaningful cloud/limb
structure. Dark-background zeros were not classified as instrument failure.

All 194 geometry calculations succeeded, with finite ground scales and
on-planet footprints. Their median ground samples span 3.04–103.10 km/pixel;
being healthy does not mean every observation resolves small cloud structure.
The [individual assessment table](junocam_expansion_assessment_2026-09-08.csv)
records each result. No acquired observation was withheld by the specified
measurement/geometry criteria; the metadata screening had already removed
unsupported or unassessed acquisition candidates.

These are evidence-based acceptance checks, not a claim that every pixel is
perfect. Framelet medians can hide localized defects, and the declared-code
saturation metric does not certify absence of clipping at every earlier
processing stage. Successful SPICE placement and a determined archive timing
offset do not establish a per-image absolute pointing uncertainty. See the
[source and timing audit](junocam_expansion_evidence_2026-09-08.md).

## Mapped products and temporal support

All 102 selected strips were built with zero failures at the existing
30 km/pixel native-sampling cutoff; 92 coarser but otherwise eligible
observations were not stripped. The files occupy **26,926,789,342 bytes
(25.078 GiB)**. The lead independently read every saved strip, checking
identity, band coordinates, DN units, full valid-mask fractions and positive
finite sampled signal in each physical band. Exact counts and bytes are in
the [product table](junocam_expansion_products_2026-09-08.csv).

| Pass | New RGB strips | Coarser observations not stripped |
| --- | ---: | ---: |
| PJ5 | 5 | 7 |
| PJ6 | 12 | 20 |
| PJ8 | 15 | 5 |
| PJ12 | 13 | 6 |
| PJ18 | 9 | 14 |
| PJ24 | 7 | 11 |
| PJ30 | 14 | 13 |
| PJ34 | 27 | 16 |

Polar builds use the existing grids at 15 km output sampling, physical
RED/GREEN/BLUE bands, night masks and product `START_TIME` coordinates. Output
spacing is not native resolution, and these observation start times are not
per-pixel acquisition times. No observations from separate passes are joined
into a supposed motion sequence.

All **13 polar stacks** have now been written, retaining **62 observations**.
The [stack inventory](junocam_expansion_stacks_2026-09-08.csv) includes paths,
dimensions, retained sources, refinement outcomes and the three skipped builds.
PJ18 product `JNCR_2019043_18C00029_V01` and PJ24 product
`JNCR_2019360_24C00024_V01` painted no sunlit cells in the selected north grid
and were omitted from those stacks; their healthy native observations remain
available in the catalog. Southern selections for PJ18 and PJ24 were empty;
PJ30 had only one southern candidate, below the two-observation build minimum.

| Pass | North stack observations | South stack observations |
| --- | ---: | ---: |
| PJ5 | 2 | 2 |
| PJ6 | 6 | 3 |
| PJ8 | 3 | 3 |
| PJ12 | 2 | 3 |
| PJ18 | 9 | — |
| PJ24 | 7 | — |
| PJ30 | 9 | — |
| PJ34 | 10 | 3 |

The lead read all 13 saved files, verified the source IDs, physical bands,
DN units, strictly increasing times, complete valid masks and positive finite
signal in every retained observation/band, and independently recomputed the
RED/native readiness results. The stacks contain 62 distinct source IDs and
occupy 5,295,391,665 bytes (4.932 GiB). A north-map contact sheet covering all
eight passes showed coherent cloud structure; its independent display stretches
are for inspection only.

At the existing 5% interval tolerance, **seven runs** in four northern stacks
have at least three observations and nonempty common RED-band support:

| Pass | Zero-based frames | Observations | Median interval (s) | Common stored canvas (%) |
| --- | --- | ---: | ---: | ---: |
| PJ18 | 5–8 | 4 | 150.090 | 0.546 |
| PJ24 | 4–6 | 3 | 303.297 | 7.038 |
| PJ30 | 0–4 | 5 | 180.250 | 30.284 |
| PJ30 | 4–6 | 3 | 150.244 | 31.531 |
| PJ30 | 6–8 | 3 | 180.344 | 0.609 |
| PJ34 | 0–2 | 3 | 240.621 | 42.656 |
| PJ34 | 6–9 | 4 | 180.726 | 6.505 |

Runs can share endpoints and are not seven independent populations. These
fractions measure pixels on the stored map canvas, not physical surface area;
cropping can change the denominator. Two runs occupy less than 1% of the large
canvas and need especially careful selection of a common analysis region.
The shared `valid` field is a union across bands: use the chosen physical
band's finite pixels when constructing a common mask.

For PJ34 north, an additional independent audit verified RED common support
of 4,094,932 and 624,444 map pixels. These regions span approximately
59.78–87.91°N and 59.78–73.61°N, respectively; neither includes the pole.
Native sampling changes from 41.61 to 10.50 km/pixel across this stack despite
its constant 15 km map grid, so early observations are oversampled. All ten
saved fits report successful refinement; their median absolute residuals
range from 0.392 to 1.318 detector pixels. Those fit residuals are not an
absolute map-position error bound or a validated cloud-motion uncertainty.

The [cadence table](junocam_expansion_cadence_2026-09-08.csv) records exact
source IDs, observation start times, interval ranges, common support and
limb-refinement status for every run. Strips and stacks together represent
**126 distinct mapped source observations**; they share sources and must not
be summed as separate samples. The total new strip/stack payload is
32,222,181,007 bytes (30.009 GiB).

Across those 126 mapped sources, **103 have successful limb refinement** and
**23 were refused because fewer than 200 usable limb points were available**.
The latter retain the existing nominal SPICE/camera timing with zero additional
fitted offset. Their fitted offset and residual remain unknown (`NaN`), not a
claim of zero navigation error. These are healthy instrument observations
with less constrained navigation, not new instrument or radiometric failures.
The [navigation table](junocam_expansion_navigation_2026-09-08.csv) identifies
each source, point count, fit result and reason. Of the 62 polar sources,
55 have successful fits; all 13 PJ34 polar sources do. A fit's success still
does not establish absolute pointing or motion accuracy.

The first five-observation PJ30 run includes one unrefined source,
`JNCR_2020313_30C00006_V01`. Every source in each of the other six listed runs
has a successful fit. The cadence result is unchanged: fit status is an
additional navigation qualification, not part of the existing cadence gate.

## Nearby JIRAM candidates and matching repairs

With a fixed search of **±300 seconds** and at least **0.25 intersection /
smaller spherical bounding-box area**, 174 of the 194 new observations have
a returned JIRAM candidate; **140 have a physical M-band candidate**. There
are 2,639 returned pairs, including 1,876 carrying M. The largest response
has 20 candidates against a limit of 100, so none of these requests was capped.
Per-pass counts are in the [matching table](junocam_expansion_matches_2026-09-08.csv).
The lead independently recounted all saved responses and reproduced one
actual API example from every new pass.

The real-catalog audit exposed two API defects. Empty optional JIRAM footprint
arrays hid valid corner coordinates, suppressing candidate matches. Paired
JIRAM products also reported their combined `LM` product label instead of
the physical detector half whose footprint qualified. The small repair restores
the existing corner fallback and reports only qualifying L/M halves. Fixed
regression cases reproduce both defects; the overlap formula, timing limits,
observation deduplication and quality policy are unchanged. Identical catalog
and policy inputs yielded 18 observations with candidates before the repair
and 174 afterwards; the before/after records retain source hashes.

These are **metadata candidates**, not validated pixel pairs. The box approximation
can overstate overlap, especially for broad polar footprints, and does not
test native JIRAM file availability, common valid pixels, comparable native
resolution, illumination or cloud altitude. Several close-in-time examples
have coarse JunoCam sampling. Inspect the actual physical-band images and
their masks before using a pair for registration or statistics.

## Scientific use and limits

For an initial cross-instrument strip comparison, PJ24 has the most immediate
local support: its existing JIRAM strip library covers the encounter window.
The other seven added passes have local JIRAM native images and navigation
metadata, but this expansion did not build their JIRAM strips. For repeated
northern cloud views, start with the measured PJ30 and PJ34 runs and inspect
the common band mask, source sampling and illumination before selecting a
region. The earlier passes add distinct dates for morphology comparisons.

The expansion provides distinct passes for morphology and appropriately
matched population comparisons. Match band, resolution, illumination,
compression/processing and valid area before comparing texture statistics.
Native planetary RDR already includes the archive's commanded-exposure and
solar-distance scaling; dividing again without reconstructing that recipe
would be wrong.

The instrument team documents mission-long optical-response evolution,
distinct from the later electronics failures. The published fits have scatter
and no per-image uncertainty; they are not applied automatically here. Earlier
missing throughput factors are not evidence of unit response, and clean
operation does not make cross-pass colour or spectral amplitude invariant.
Native values remain DN, not newly certified I/F.
[Instrument-team analysis](https://meetingorganizer.copernicus.org/EPSC-DPS2025/EPSC-DPS2025-1226.html),
[PDS cumulative ERRATA B.10](https://planetarydata.jpl.nasa.gov/img/data/juno/JNOJNC_0035/ERRATA.TXT).

The previously inspected ML-calibrated collection remains a separate reference.
Its generated channels, coarse grids and missing tile times do not replace
these native timed observations; see the
[calibrated-collection assessment](junocam_calibrated_assessment_2026-09-07.md).

## Preservation, reproducibility and local use

The processing audit found that partial JunoCam strip builds deleted omitted
products from the same pass. That writer now upserts successful IDs and
preserves omitted rows/files. It validates metadata before atomic replacement
and rejects an incompatible existing band set. A fixed synthetic gate first
reproduced eight failures, then passed all nine cases after the repair.

Operational records live under `<mirror>/junocam/expansion_2026-09-08/`:
the frozen selection, retrieved source documents/hashes, acquisition checks,
quality and geometry results, individual assessment, policy snapshot, scripts,
logs and pre-expansion index/product records. The acquired labels/images remain
in their ordinary volume paths. The saved old image/quality/geometry rows were
preserved. Final checks confirmed all **320 pre-existing NetCDF paths, sizes
and modification times**, and all **305 historical strip-index rows**, are
unchanged. The stored strip index now has 407 rows; preferred-version filtering
exposes 399 through the API, including 110 JunoCam strips. The stack API lists
18 products, including all 13 new stacks.

Products were generated before the delivery commit. Their recorded Git HEAD
can therefore identify the base revision rather than a clean checkout of the
final changes. `delivery/source_snapshot.json` and `delivery/source_changes.patch`
retain the actual Python/configuration hashes and source changes; the delivery
record also identifies the final pushed commit. Original product provenance
is preserved rather than rewritten retrospectively.

Code, documentation and small audit tables go to GitHub. Native imagery,
navigation kernels and derived scientific NetCDFs remain in the external
mirror. A laptop needs the relevant mirror contents and its updated indexes,
with the existing relative directory structure. Then
`uv run jiram-catalog gui --mirror /path/to/jiram_mirror` serves locally; no SSH tunnel is required for a
local mirror. Refresh the GUI and reset an old PJ4-only filter to see the
expanded catalog. Dense maps and full-resolution statistics remain substantial
memory/CPU operations.

Validation: **357 offline tests passed**, 52 skipped with mirror gates disabled;
all three existing JunoCam geometry gates and all three existing product/API
gates passed. The three fixed expansion gates passed across the acquisition
and final integration runs. Live catalog, mapped images, physical bands,
readiness, a native-statistics request and withheld-data access were checked
against the mirror. Every new strip and polar stack received independent
saved-file checks; complete outcomes and browser scope are in the
[delivery log](../build_log_2026-09-08.md).

Judgment calls: selected a bounded eight-pass RGB sample with JIRAM/navigation
support instead of mirroring every pass; retained ordinary response evolution
as a quantitative limitation while excluding documented failures and unassessed
products; required each colour band to pass; kept existing grids and physics;
preserved historical products and made new builds additive; report cadence and
common support as measured rather than promising wind-ready observations;
repair actual-catalog matching defects and keep approximate candidates separate
from verified physical overlap; retain healthy observations without enough
limb points while explicitly recording their nominal-navigation status.
