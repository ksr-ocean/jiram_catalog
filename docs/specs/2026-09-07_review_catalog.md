# Catalog, identity, provenance and exclusion

Accepted under `2026-09-07_review_implementation.md`. Implements D01, D02,
D04, D05 and backend portions of D06/D12.

## Owned files

`src/jiram_catalog/junocam/policy.py` (new), `junocam/pds.py`,
`junocam/stacks.py`, `junocam/quality.py`, `configs/junocam_quality.yaml`,
`src/jiram_catalog/api/catalog.py`, `api/data.py`, `api/coverage.py` (new),
`tests/test_review_catalog_offline.py` (new). Existing gates, fixtures, specs,
CLI, pyproject, lockfile, frontend and api/app.py are read-only.
Do not mutate mirror products. The lead integrates your coverage router.

## Facts and exact decisions

The JunoCam catalog lacks start_time_ms. The detail endpoint only loads
JIRAM. The existing JunoCam polar product has six steps from three stems,
with V01/V02 pairs differing 8–15 ms. Broad post_anneal status does not prove
an image unaffected; no signal can pass the old tier. The archive manifest
already offers all-version and preferred tables, but builders use all versions.

Provide `observation_id(product_id: str) -> str` removing a trailing _Vnn
only. `preferred_rows(table, id_column='product_id')` returns highest numeric
version per observation, preserving distinct times/observations and stable
chronological order; never deduplicate merely by time. All-version provenance
is retained in source indices, with older unaffected versions accessible by
explicit detail/version inspection, not counted as independent observations.

Provide `assess_observation(row: Mapping) -> dict` with keys `status`
(eligible/excluded/unassessed), `reasons` list[str], `navigation`,
`signal`, `calibration`, `policy_version`. `policy_version='failure-exclusion-v1'`.
Use source-labelled exclusion rules in config plus per-product rationale and
measured flags: known failure/invalid radiometry/content-free/saturation,
corruption, blooming and failed metrics cannot be eligible; absent evidence
is unassessed. Legacy PJ4 rows with metrics_ok, finite valid metrics and
meaningful max_dn/zero fraction may remain eligible. The lead will confirm
strictness from the owner's clarification; implement data-driven exclusions,
not an irreversible global hard-coded cutoff. Unassessed data withheld from
normal pixels/catalogs/builds. Unknown throughput outside documented support
is NaN/unknown rather than a claimed 1.0; preserve older quality tests where
possible but new scientific semantics take precedence with explicit report.

`eligible_images(mirror)` returns the image/quality/geometry information with
assessment, preferred rows default. Apply exclusion and version choice to
JunoCam catalog, stack builder, strip library, and `api/data.py` cached stack
reads: filter time steps by eligible identity, keeping the highest version
within the stack. Old files stay intact. Direct access to excluded strip data
must fail; avoid hiding problems as generic empty arrays. Enforce policy in
all api/data paths including explicit product metadata/thumbnail reads.

Catalog Arrow gains additive `observation_id`, `trackability_status`
(assessed/unassessed), `quality_status`, `quality_reason`, `native_version`,
`is_preferred`. Dates are real epoch ms. JIRAM trackability assessed only when
its source table exists. Do not invent JunoCam partner scores. Latitude cuts
support `latitude_mode=coverage|boresight` default coverage: intersect swath
lat range, using min/max and fallback boresight; retain old JIRAM behavior if
ranges absent. Exact polygon region selection stays in frontend.

`GET /api/catalog/frame/{product_id}` supports either instrument, supplies
instrument, observation_id, version, versions (IDs), source_url, label_url,
quality_assessment, provenance, halves ([''] for JunoCam), bands, and rationale.
Blocked observations may return exclusion metadata with no preview. Add
`GET /api/catalog/frame/{product_id}/thumbnail.png?band=...` using bounded
sampling of eligible existing raw images (not a new reprojection); label it
instrument framelets. JIRAM thumbnail splits half correctly. Never read a path
supplied by the client. Existing detail scalar fields remain compatible.

`api/coverage.py` exposes `router`, prefix `/api`:
* GET `/coverage` -> {policy_version, generated_utc, sources:[{name,updated_utc}],
  rows:[{instrument,orbit,archive_known,labels_indexed,pixels_local,
  geometry_available,quality_assessed,eligible,excluded,unassessed,
  stacks,strips,status}], policy:{summary,exclusions}, references:[...] }.
  Counts are unique observation stems, explicitly mark snapshot img_present.
* GET `/archive?instrument=JunoCam&orbit=4&q=...&offset=0&limit=100` ->
  {total,items:[{product_id,observation_id,orbit,start_time,bands,target,
  source_url,label_url,status,reason}],offset,limit}. Browse safe metadata of
  archive-known/unprocessed entries; no invalid-pixel preview. Limit <=200.
* GET `/references` -> {items:[{id,title,url,kind,description,quantitative_ready,
  coordinate_note}]} includes Mission Juno maps and PDS derived calibration
  record as external references with honest processing/convention notes.
The lead may augment references after examining source documentation.

Discover archive volume directories from root listing when no explicit
volumes passed, with cached/offline fallback visibly logged; retain explicit
volume CLI selection. Do not fetch massive images.

## Gate/verification

Lead gate: numeric preferred version selection, misleading timestamp duplicates,
black/saturated/unknown exclusion, usable PJ4 eligibility. Add meaningful
endpoint tests with small synthetic tables including both instruments and
ensure blocked images cannot be fetched even directly. Supply changed-file
list, checks, assumptions, integration names. End with judgment calls.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
