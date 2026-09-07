# Analysis readiness, comparisons and reproducible statistics

Accepted under `2026-09-07_review_implementation.md`. Implements backend
D03, D09, D10 and matching support for D12. No physical-model changes.

## Owned files

New `src/jiram_catalog/science.py`, `src/jiram_catalog/api/science.py`,
`src/jiram_catalog/export_goflow.py`, new `tests/test_review_science_offline.py`.
Read-only everything else, especially gates, fixtures, frontend, api/data.py,
api/stacks.py, app.py and other executors' files. Lead integrates routes and
existing export jobs after delivery. Use existing api/data for policy-aware
stack/strip opening and existing normalization functions; do not bypass them.

## Exact contract

Pure module:
`prepare_stack(dataset, band=None, norm='none') -> xr.Dataset`: one chosen
physical band, preserve correct native units, valid finite and illuminated
pixels, preferred observation version per time, 3D image. Multi-band requires
an explicit band, RGB is not a quantitative band. Apply API-equivalent
normalization, annotate it in provenance. Reject unsupported transforms and
avoid loading whole stacks for metadata preflight; process one frame at a time
when masks are required.
`stack_readiness(dataset, band=None, norm='none', dt_tol=0.05,
min_frames=3) -> dict`: ready, reasons, n_observations, n_versions_removed,
band, norm, units, km_per_px, times, gaps_s, runs (first,last,dt_s,
common_valid_frac,n_frames), n_realizations, provenance. Unique identity and
strictly positive regular cadence; no run with no common mask. Distinguish
format/cadence readiness from unvalidated wind accuracy. Sources/units and
normalization travel to exported spec/manifest. `export_stack` accepts band
and norm, rejects nonready before writing; JIRAM existing 3D defaults kept.

`mask_diagnostics(image, valid, dx_m) -> dict`: valid fraction, connected
components, boundary fraction, scene dynamic range, seam/row discontinuity
indicator, cautions and effective scale information. Do not claim an arbitrary
mask diagnostic mathematically corrects leakage.
`fit_spectrum(k,E,k_min=None,k_max=None) -> dict`: slope, intercept,
n_bins, k_min/max, standard_error or null, caveats. Positive valid bins only,
>=3 required, default inside Nyquist if known caller provides bound.

API module exposes `router` (prefix `/api/science`) and JSON-safe outputs:
* GET `/stacks/{stack_id:path}/readiness?band=&norm=&dt_tol=&min_frames=` ->
  stack_readiness output; stack path resolution via existing api/stacks.
* POST `/compare` body {left:{kind:'stack'|'strip',id,t:0,band,norm},
  right:same, speed_m_s:30, navigation_error_px:null} ->
  {compatible, reasons, dt_s, common_valid_frac, registration:{dy_px,dx_px,
  correlation,status}, predicted_displacement_px, navigation_error_px,
  velocity_uncertainty_m_s, left:{...metadata},right:{...metadata},provenance}.
  Co-register only equivalent grids, or use actual lat/lon coordinates to a
  common grid with documented bounded interpolation. Different grids with no
  reliable coordinates report incompatible, not a bogus pixel shift. NCC
  shifts are image registration, not claimed winds; unknown navigation stays
  null. Use bounded sampling with explicit sample_stride and scale units.
* GET `/stacks/{stack_id:path}/vectors?t=0` -> {status,units:'m s-1',
  provenance,features:[{x_km,y_km,u,v}]} load existing tracking NetCDF products
  near region, only if coordinates/time/basis match; none -> unassessed with
  reason. No arbitrary file path or assumed vector basis. Research known
  tracking output via read-only source. Published vectors may be exposed when
  metadata are sufficient; otherwise report why not with documented fallback.
* POST `/population` body {strip_ids:[...],band,norm,k_min?,k_max?} ->
  {groups:[{instrument,band,norm,resolution_class,n_observations,n_passes,
  k,E,E_stderr,S2,r_m,fit,diagnostics,provenance}],excluded:[{id,reason}],recipe}.
  Group by instrument, band, normalization and native resolution class; dedup
  versions and refuse ambiguous multi-band. Average independent per-pass
  means, standard error across passes (null with <2 passes), not fake
  independence of overlapping/duplicate images. Use existing stats kernels;
  cap <=100 requested strip IDs, background/caching if needed. Return JSON
  or CSV/figure-client export recipe with sources, versions, kernels when
  known, software revision, units, transform, masks, and chosen fit range.
* POST `/matches` body {product_id,max_dt_s:3600,min_overlap:0.1,limit:20}
  -> {items:[{product_id,instrument,dt_s,overlap_fraction,overlap_method,
  native_pixel_km,bands}],reference,limitations}. Cross-instrument candidates
  from catalog swath range overlap with longitude seam handling; report
  bounding-box approximation explicitly, never claim exact pixel overlap.

Prefer bounded cache products in `<mirror>/gui_cache/research/` keyed by
source mtime/size, settings and code policy version. No arbitrary client
output paths for new endpoints. Scientific values must remain finite/null
in JSON. Empty or invalid datasets have explicit reasons and no fake score.

## Validation

Lead gate uses synthetic known shifts/power law/duplicate timestamps and
banded constant-cadence cubes. Add offline endpoint tests. Test export actual
file dimensions, band/units/normalization/provenance, and no directory creation
on readiness failure. Avoid tests that only mirror internal implementation.
Report API contract differences immediately to lead/frontend executor.
End report with judgment calls.

If any part of this spec is ambiguous or underdetermined, do NOT choose an interpretation. Stop, list the ambiguities and the options, and make no further changes. Also list every judgment call you made, however minor, at the end of your final message.
