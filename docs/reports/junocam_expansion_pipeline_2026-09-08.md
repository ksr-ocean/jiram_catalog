# JunoCam expansion: pipeline reconnaissance

This is the pre-acquisition reconnaissance record. See the
[expansion delivery record](junocam_expansion_2026-09-08.md) for subsequent
acquisition, individual clearances, repairs and measured mapped products.

Read-only audit on 2026-09-08 at repository commit
`bf9dcf595de25d1c4cb06f967a969cefbeff6f73`, under the accepted
[pipeline spec](../specs/2026-09-08_junocam_pipeline_recon.md) and
[expansion spec](../specs/2026-09-08_junocam_expansion.md).
Source, policy, tests, native products and servers were unchanged. Measurements
below read Parquet metadata and run selectors; no native image was read or
downloaded, no product was built, and no GUI/server was started. Python used
the existing `uv` environment, Lustre `TMPDIR`, BLAS/OMP threads 1 and Arrow
CPU/IO pools 4. The acquisition list and resource budget remain lead decisions.

The existing path is suitable for a bounded expansion of supported visible
full-resolution RDR products. **Fix strip-index preservation before building.**
Then serialize shared-index writers, acquire an explicit product set, assess
before clearing, verify navigation and build new filenames. Initial expansion
does not require relaxing the exclusion policy or rewriting PJ4 products.

## Observed starting state

Mirror: `/expanse/lustre/projects/cla119/kaushiks/jiram_mirror`.
These are current measurements, distinct from dated implementation reports.

| Object | Read-only result |
| --- | --- |
| Preferred archive manifest | 82,668 products; orbit buckets 0–80 |
| Every-version manifest | 84,332 processing products |
| JunoCam image index | 3,788 rows: PJ4 2,080; PJ58 1,708; all parse successfully |
| Locally indexed native images | 122 PJ4 RDR files; 2,051,915,776 declared bytes in total |
| Quality table | 122 rows; all metrics successful and legacy tier A |
| Geometry table | 122 rows; 112 `geo_ok`; ten methane products have no sampled on-planet footprint |
| Policy assessment, every indexed version | PJ4: 87 eligible, 35 excluded, 1,958 unassessed; PJ58: 1,708 unassessed |
| Preferred eligible products | 72 PJ4 observations: 57 RGB and 15 methane |
| Native `strips/strips.parquet` | 305 rows: 289 JIRAM; 16 PJ4 RGB versions |
| Current preferred RGB strip selector | Eight PJ4 V02 products, `04C00099` through `04C00106`, at ≤30 km/px |
| Current preferred RGB region selector | Three PJ4 V02 products, `04C00099`–`04C00101`, for each northern polar grid; zero for `south_pole` and `neb_15n` |

The 16 native JunoCam strips include both V01 and V02 products. Retaining
native versions and presenting eight preferred strips are compatible results.
The policy assessment took 0.846 seconds in a fresh Python process. Stored
successful geometry timings have median 3.819 seconds and maximum 18.468
seconds; these are historical geometry-call timings, not a new benchmark or
complete survey/build runtimes. No native-file hashes were computed in recon;
record those before the first authorized product mutation.

## Incremental stages and preservation boundaries

| Stage and existing entry point | Selection and resulting write | Preservation consequence |
| --- | --- | --- |
| `junocam.pds.build_manifest(root, volumes, refresh)` | Replaces selected volumes in both manifests; deduplicates highest version for `manifest.parquet` | Unselected volumes retained. Every-version file commits before preferred file; no transaction spans the pair. |
| `junocam.mirror.mirror_files(root, orbits, level, kinds, doy, jobs)` | Reads every-version manifest; downloads labels/data under `junocam/JNOJNC_*/...` | Complete files skipped. Incomplete targets removed and downloaded again; broad orbit/day selection includes superseded versions. |
| `junocam.index.build_index(root, orbits, jobs)` | Parses present labels; replaces every indexed row of selected orbits | Unselected PJ4/PJ58 rows retained. Missing labels or parse failures can replace a previously successful selected-orbit snapshot. |
| `junocam.quality.build_quality(root, orbits, jobs)` | Measures selected, parsed, locally present RDR images; replaces selected-orbit quality rows | Unselected rows retained. Failed new measurements are recorded; the previous successful selected-orbit assessment is not retained as a separate revision. |
| `junocam.geo.build_geo(root, orbits, jobs)` | Computes selected locally present RDR geometry with `refine=False, cache=False`; replaces selected-orbit geometry rows | Quality clearance is not required for this assessment stage. Failed calculations replace earlier selected-orbit results. |
| `junocam.strips.build_library(root, orbits, bands, ...)` | Policy/geometry/scale selector, then one NetCDF per versioned product ID and shared strip-index update | **Current update replaces all JunoCam products of each selected orbit and deletes unrepresented files. Unsafe for partial reruns or multiple band selections.** |
| `junocam.stacks.select_images(...)`, `build_stack(...)`, shared `stacks.write_stack(...)` | Eligible images overlapping a named grid; one `time,band,y,x` dataset | Named output is atomically replaced. A new pass-specific filename preserves older stacks; reusing an existing filename replaces it. |
| API catalog and derived-product readers | Policy is reapplied; catalog/index cache keys include metadata/policy signatures | New observations need eligible policy and geometry. Native files are retained when access is withheld. Dataset handles and statistics caches have additional same-path replacement hazards below. |

All these Parquet writers use a fixed sibling `.parquet.tmp` followed by
rename, without an interprocess transaction lock. They preserve a complete
old file until rename, but concurrent commands can collide on the temporary
file or lose another command's rows. **Run one index-writing command at a
time**, including JIRAM and JunoCam library builders sharing
`strips/strips.parquet`. Worker pools inside one command are supported;
independent commands touching the same index are not safely composable.

### Concrete strip defects and the smallest repair

In [junocam/strips.py](../../src/jiram_catalog/junocam/strips.py),
`update_index` removes every selected-orbit JunoCam row, unlinks replaced
paths absent from the successful result set, and then sweeps each selected
orbit directory for every other `.nc`. Examples follow directly from source:

- Build RGB then methane for one orbit: the second update deletes the RGB
  products, even though the observation IDs differ.
- One worker fails on a rerun: previously successful products absent from
  this run's successful subset are deleted if any other worker succeeds.
- A new metadata table or policy selects only preferred V02 products:
  rebuilding PJ4 deletes retained V01 files. Narrowing a science selector
  must not become permission to prune the native library.

Replace whole-orbit replacement and directory sweeping with an upsert of
successful product identities; preserve unrelated rows/files and failed
products. Pruning, if ever needed, should be an explicit separate operation.
The lead should fix preservation gates before an executor changes this code.

`_strip_task` writes to `<product_id>.nc`, with no band set in its name.
A successful RED-only rerun of an RGB observation overwrites its RGB file.
For this bounded expansion, use a consistent RGB band set and reject
incompatible same-ID band collisions, or introduce distinct IDs/paths for
additional band sets while retaining old IDs. Do not silently narrow a file.

The shared [write_strip](../../src/jiram_catalog/strips.py) already writes
`<product_id>.nc.tmp` and renames only after serialization succeeds: failure
before rename preserves the prior NetCDF. The narrower defect is that
`_strip_task` derives its index row **after** that rename; a subsequent
metadata error reports failure after pixels have changed. Derive and validate
the index row before writing, close the dataset in `finally`, and preserve the
old file on any pre-commit error. Fixed temporary names still require the
serialized execution above. A successful file commit and a later failed
shared-index commit are not one transaction; retain such files for recovery,
never delete them as an automatic failure response.

## Acquisition, quality and navigation ordering

1. Freeze the exact candidate IDs, versions, URLs, supported dimensions,
   label-declared image bytes and evidence decisions. Snapshot existing
   shared tables and record native/PJ4 product hashes before expansion writes.
2. Acquire metadata and the approved native set. `mirror_files` currently
   has no product-ID allowlist, preferred-only option or byte-limit option.
   `--level RDR --doy DDD` still selects every RDR version on that orbit/day;
   `--kinds labels,data` obtains all selected-orbit EDR/RDR labels. A small
   explicit-product selector, validated against the manifest, is needed if
   the lead's acquisition list is narrower. Do not rewrite the live manifest
   just to make a download subset.
3. Validate complete labels and declared byte counts, then run the selected
   new-orbit index. `img_present` is a size-checked snapshot; indexing before
   acquisition leaves newly downloaded pixels absent until indexing repeats.
4. Run quality metrics and the unrefined geometry survey while the products
   remain unassessed. Inspect errors, unsupported formats, signal metrics,
   documented failures and actual coverage before explicit clearance.
5. Add only evidence-supported exact product/observation clearances, with
   rationale and source. Re-evaluate policy; confirm excluded and remaining
   unassessed products cannot provide pixels. Legacy tier A, a nominal epoch,
   a post-anneal label or a successful geometry calculation is not clearance.
6. Select eligible preferred supported products, verify kernel/time coverage,
   build new strips and appropriate pass-specific region stacks, then inspect
   native masks and navigation evidence. Keep unsuccessful refinements
   explicitly unknown. No new motion claim follows from having three files.
7. Use a fresh backend/browser for final API/GUI verification after writes
   finish; record source IDs, effective policy, dimensions, units and limits.

The low-level mirror completeness fallback accepts a nonempty `.IMG` when a
sibling label cannot supply the declared size; a failed label download does
not prevent the data attempt. This can overstate download success. The later
index's size-checked `img_present` limits exposure, but acquisition acceptance
must require a complete parsed label and exact size, plus supplied checksums
where available. A fail-closed completion check is a small improvement;
archive/native hashes should be recorded independently. `bytes_downloaded`
in `MirrorResult` sums selected on-disk bytes including already complete
files, so it is not a measured network-transfer byte count.

### Existing command forms, for the subsequent authorized execution

These are templates, **not executed commands or approval of an acquisition
set**. Replace `PASSES`, `DDD` and `REGION` using the lead's execution spec.

```bash
uv run jiram-catalog junocam mirror --orbits PASSES --level RDR --kinds labels --jobs 3
uv run jiram-catalog junocam mirror --orbits PASSES --level RDR --kinds data --doy DDD --jobs 3
uv run jiram-catalog junocam index --orbits PASSES --jobs 2
uv run jiram-catalog junocam quality --orbits PASSES --jobs 2
uv run jiram-catalog junocam geo --orbits PASSES --jobs 1
# After documented clearance and the preservation repair:
uv run jiram-catalog junocam strips --orbits PASSES --bands RED,GREEN,BLUE --quality-min A --max-pixel-km 30 --jobs 1
uv run jiram-catalog junocam region-stack --region REGION --orbits PASSES --bands RED,GREEN,BLUE --quality-min A --max-emission 80 --max-pixel-ratio 3 --jobs 1
```

The existing mirror data template is broader than an exact-ID plan; use the
accepted allowlist repair instead when required. Do not include `4`, `58` or
`all` in rebuilding commands merely for convenience. Region-stack additionally
supports `--out`, `--no-crop`, `--margin-px` and `--config`; keep output beneath
the mirror and retain crop defaults unless the fixed scientific grid demands
otherwise. Avoid reusing `junocam_*_orbitsall_frame.nc`. Dynamic manifest
discovery supports newer published volumes, but explicit CLI volume parsing
still enforces the historical volume ceiling; a future-volume input repair
can be separated from this bounded expansion.

## Supported formats, kernels, timing and scientific interpretation

- [images.read_image](../../src/jiram_catalog/junocam/images.py) accepts
  full-resolution framelets with 1,648 samples and exactly
  `n_framelets × n_bands × 128` lines. RDR is read as big-endian unsigned
  16-bit; EDR 8-bit counts can be decompanded, but production selectors use
  RDR. On-board summed products are explicitly unsupported. Require
  `sampling_factor=1`, compatible sample type and exact dimensions in the
  acquisition acceptance checks.
- Geometry itself does not enforce the same native-shape checks: six local
  816-sample PJ4 methane versions have `geo_ok=True` despite the full image
  reader rejecting that format. All six are currently excluded for bloom,
  so they do not expose unsupported pixels. Treat unsupported geometry rows
  as unusable, and add a shape guard if such candidates enter the execution
  scope; broadening summed-product support is a separate model change.
- [KernelSet.for_orbits](../../src/jiram_catalog/geometry.py) obtains dynamic
  CK/SPK names from the **JIRAM** index's `spice_kernels`, then resolves local
  files. It does not derive coverage from JunoCam labels. Every named dynamic
  kernel for PJ4–46 was present in the local filesystem, but PJ38 names zero
  CKs; empty names are not evidence of available attitude. Require actual CK
  and SPK coverage over each candidate's corrected first-to-last frame epoch,
  not just successful static loading or file existence. Passes with no JIRAM
  rows need an explicit independently resolved kernel set.
- Geometry/strip/stack multiprocessing uses `spawn` and per-worker kernel
  caches; SPICE state is process-global. Keep SPICE out of Python threads.
  Quality/index pools use the platform default multiprocessing context and
  do not perform geometry. Download threads are capped at four. The API's
  NetCDF lock is process-local and does not protect CLI writes or a second
  backend process.
- JunoCam epochs use label `START_TIME`, IK `START_TIME_BIAS`, frame number
  times `(INTERFRAME_DELAY + INTERFRAME_DELTA)`, and the fitted image offset.
  Versioned product identity propagates into filenames and product coordinates.
  Stack `time` remains label start time, with separate `dt_refined_s`; strip
  start/end/mid are label start plus relative readout spans, not a fully
  corrected per-pixel timestamp. The cache stores fitted transforms/summary,
  not full geometry arrays. Fit failure leaves the applied offset zero and
  the refinement result unknown; do not relabel that as zero error.
- [geometry._signature](../../src/jiram_catalog/junocam/geometry.py) includes
  product ID, band set, frame count, label time, interframe delay and aberration
  convention, but not kernel or image-content fingerprints. Replacing pixels
  or kernels for the same ID can reuse a stale fit. New IDs with a fixed kernel
  inventory avoid this edge case; later reprocessing needs cache signatures
  or explicit scoped cache invalidation, with old products preserved.
- Methane `RATIONALE_DESC` can contain `INS-61504_DISTORTION_Y` overrides;
  31 EDR and 31 RDR PJ58 labels already do so locally. For example,
  `JNCR_2024034_58M00031_V01` gives 405.48. Current `band_cameras` uses only
  IK values, and worker metadata does not propagate this label override.
  This confirms the companion evidence audit's methane-model limitation.
  Initial RGB scope avoids it; methane expansion needs a separate override
  propagation/model/cache test before its maps are trusted.
- The native intensity unit is **DN**, despite the variable long name saying
  radiance. Lambert/flattening options do not turn DN into calibrated radiance
  or repair throughput failures. Match band, normalization, scale, latitude,
  illumination and mask support for cross-pass texture comparisons; do not
  interpret differences in raw spectral amplitude as calibrated variability.
  Neither kernel-derived pixel size nor the Nyquist limit establishes optical
  effective resolution. Motion requires independently supported cadence,
  overlap and navigation uncertainty.

## Resource limits and cache consequences

The 30 km/px strip cut uses measured native median ground sampling. Region
selection limits that scale to three times the requested grid spacing,
requires an on-planet footprint and rejects boresight emission above 80°.
Both use existing eligibility rules; raising `quality-min` permissiveness
cannot bypass the exclusion policy. Region selection's negated `>` predicates
can admit unknown scale/emission values; current successful PJ4 metadata is
finite, but new-pass acceptance should explicitly require finite values.
All production maps require incidence below 88° in at least one contributing
band; scientific requests must also use the selected band's valid support.

Strips cap each canvas dimension at 6,000 pixels, record `capped`, and retain
the quantized grid spacing: the cap can crop spatial coverage. Inspect the
resulting footprint and valid area, not only the source footprint. Existing
PJ4 files reach 6,000 × 6,000. Three float32 image/angle arrays for RGB plus
float32 latitude/longitude/local-time and a mask already require about
1.76 GB at that size, before count arrays, temporary reprojection buffers,
native input, compression and copies. Do not treat 6,000 as a cheap image.

For `P` native pixels, full geometry allocates six float32 arrays plus a mask
(about `25P` bytes). Although the survey docstring suggests a 1/256
computation, `_geo_task` computes full geometry; `geo_row` then casts five
full planes to float64 **before** slicing. Those retained views add roughly
`40P` bytes. At the largest current local product, 16,128 × 1,648 pixels,
these arrays alone approach 1.73 GB. A small slice-before-cast repair would
remove the extra full-size float64 arrays without changing survey values;
a truly sampled geometry engine is a larger optional optimization.

For `N` images, `B` bands and a stack window `H×W`, the parent stack's core
buffers require approximately `(13B+1)NHW` bytes before coordinate arrays,
worker results or cropping copies. At RGB on the full 3,000×3,200 paper grid,
ten image steps require 3.84 GB of these buffers alone. Batch by pass/region,
retain default crop and start builds at `--jobs 1`; measure first-product peak
RSS/wall time before raising concurrency. No new build-time benchmark was
performed here, so these lower bounds are not a final allocation request.

API catalog and strip-table caches observe metadata signatures. Open stack
and strip datasets, however, are cached by path/ID without source-stat
invalidation. Disk strip statistics are keyed by strip ID, band and norm,
without a native-content signature. A backend restart fixes old open handles
but **does not invalidate stale disk statistics after a same-ID rebuild**.
For initial expansion, create new IDs/paths and retain existing products;
reprocessing needs source-aware cache keys or explicit scoped invalidation.
Native statistics can take minutes and hold the NetCDF route lock while
health/jobs/catalog remain responsive. Bounded Compare FFT sampling does not
bound native normalization I/O. Include this behavior in resource planning.

## Fixed acceptance checks for the lead's execution spec

Existing tests were inspected, not modified or run during recon.
`test_junocam_offline.py` covers label parsing, size checks, broad mirror
selection, versions and metrics; `test_junocam_geometry_offline.py` covers
camera/timing/refinement known answers; `test_junocam_products_offline.py`
covers grids, cuts, band axes, normalization and index migration. These do
not exercise successful-subset strip upserts or band-collision preservation.
`test_review_catalog_offline.py` covers fail-closed policy, preferred-version
behavior and cache signatures; the integration tests cover NetCDF route
serialization, cache/read bounds and failed movie-file preservation.

Write new gates before repair or expansion, with fixed expected identities
and independent acceptance data:

1. **Preservation:** same-pass RGB and methane/single-band updates retain
   unrelated files/rows, unselected PJ4/PJ58 metadata and every JIRAM row.
   Failed workers and failed NetCDF serialization preserve previous product
   bytes and rows. Existing V01 products survive a V02-only selector.
2. **Acquisition:** exact allowlisted IDs/versions only; no accidental EDR or
   older-version pixels; truncated/missing labels and wrong-sized images
   cannot count as complete. Compare measured acquired bytes/IDs with the
   frozen acquisition record, not the misleading transfer-summary field.
3. **Screening:** new products remain unassessed before documented clearance;
   positive evidence plus successful metrics allows only intended IDs.
   Documented/measured failures still override clearance. Unknown and failed
   products return no thumbnail, strip, stack, movie or export pixels.
4. **Navigation:** require supported dimensions and real epoch coverage;
   report per-image limb-fit success/residual and band registration, including
   failures. Do not generalize the PJ4 geometry report to new passes.
5. **Native products:** preserve the old product inventory/hashes, add a
   frozen minimum set across the lead's chosen passes, and check actual
   finite per-band pixels, incidence/masks, nonempty footprints, capped area,
   versioned IDs, timestamps, units, refinement metadata and selected grids.
6. **Scientific usage:** matched multi-pass population groups have distinct
   pass identities and explicit single-pass uncertainty behavior. Readiness
   uses actual chronological cadence/common support; no manufactured triples.
   Keep unresolved navigation uncertainty unknown.
7. **API and GUI:** on a fresh production server, verify archive/local/eligible/
   mapped counts separately; choose an actual new-pass observation in Explore,
   a new strip in Image library, and a new stack in Time series. Wait for
   decoded, nontransparent pixels that match current band/norm/RGB settings.
   Compare two explicitly chosen physical bands, inspect readiness reasons,
   and verify blocked metadata cannot open imagery. Preserve PJ4/JIRAM access.
   Use a small real strip for functional statistics; do not silently reduce
   scientific resolution to make a test pass.

Preserve historical gates. `test_gate_junocam_products.py` still expects at
least 100 GUI catalog entries and PJ4 stacks with at least five steps; those
assertions predate preferred-version/policy filtering. A new expansion may
make the aggregate count pass, but counts alone prove neither independence
nor retention. New fixed identity/preservation gates should establish those
properties. Existing physical PJ4 geometry/photometry gates remain regression
evidence, not new-pass validation. Run the required offline suite and relevant
live gates in the later execution phase and report historical conflicts
explicitly rather than changing assertions.

## Documentation and completion record

Update current data claims in `docs/gui_guide.md`, `docs/open_items.md`,
Coverage captures and the expansion build record after acceptance. Record
preferred eligible observations separately from local versions, native strip
files and API-visible strips. The current guide's 72-observation statement is
dated 2026-09-07; preserve that historical meaning or replace it with a dated
new inventory, not an unqualified count. `PEDAGOGICAL_REVIEW.md` and
`docs/pedagogy/slides.tex` explicitly describe `1e2723d`; retain those historical
numbers unless performing a clearly dated documentation refresh. New counts
do not remove the documented methane, navigation, calibration or cadence
limitations. The screenshot helper's positive JunoCam count wait has no upper
ceiling and should remain usable after expansion.

## Judgment calls and unresolved execution choices

- Treated source-provable deletion/overwrite behavior as defects; concurrent
  writer collisions, same-ID cache reuse and unknown metadata are conditional
  hazards, not claims of observed corruption in this mirror.
- Recommended initial supported RGB products rather than silently adding
  summed/methane camera modeling. Which observations are unaffected is for
  the evidence audit and the lead's explicit clearance record.
- Used metadata selectors and stored timing fields rather than reading native
  pixels or running builds/tests. Resource figures are source-derived lower
  bounds; an execution pilot must measure actual time and peak memory.
- Recommended serialization and new pass-specific outputs as the smallest
  operational boundary; a general transaction manager, streaming stack
  builder and sampled navigation engine are larger alternatives, not required
  recon changes.
- Kept existing native/PJ4 files as preservation requirements even where the
  preferred API view omits older versions. Upsert must not equate hidden data
  with disposable data.
- Left candidate IDs, evidence scope (exact versions versus observation
  stems), acquisition byte cap, final grid choices, repair design and resource
  allocation for the accepted execution spec. No ambiguous scientific
  clearance or product choice was made by this read-only audit.
