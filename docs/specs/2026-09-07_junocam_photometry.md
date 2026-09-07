# Spec: JunoCam photometry and display — night masking, scale cutoffs, dayside stretch, illumination normalisation

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog.
Read first: `docs/specs/2026-09-07_junocam_products.md`, `src/jiram_catalog/junocam/{stacks,strips,geo,reproject,geometry}.py`,
`src/jiram_catalog/api/{stacks,strips,images,data}.py`, `src/jiram_catalog/stats2d.py`,
`frontend/src/**` (Poles and Strips viewers, ImageView, stretch state), the
API contract and its amendment.

## Why (measured 2026-09-07 on the orbit-4 JunoCam products)
The polar RGB stack marks the whole 3000x3200 canvas valid at most time
steps, night side included; band medians are 6-181 DN against 99th
percentiles of 1,100-2,800, so any percentile stretch saturates the
dayside. The stack's cached meta has no `stretch`. The 72 strips have a
median pixel scale of 200 km (whole-disk views), only ~10% are the
close swaths (<= 13 km) the library is for. No illumination
normalisation exists, so limb darkening dominates the display and
would dominate spectra.

## Changes (normative)
### Products (`junocam/stacks.py`, `junocam/strips.py`, `junocam/geo.py` only where noted)
1. Store `incidence` per band in JunoCam stacks and strips (same shape
   as `emission`), from the per-pixel geometry.
2. `valid` for JunoCam products = painted AND `incidence < NIGHT_INCIDENCE_DEG`
   (module constant, 88.0) in at least one band; `image` outside
   validity is NaN. Keep `valid` band-free as before. Add attr
   `night_masked_deg`.
3. `select_images`: `MAX_PIXEL_RATIO` default 3.0 (was 25); CLI flag
   unchanged. `junocam strips`: new option `--max-pixel-km` default
   30.0; images coarser than that are skipped and counted.
4. Rebuild the paper-region RGB stack and the orbit-4 strips with the
   new defaults (validation block).

### API (`api/stacks.py`, `api/strips.py`, `api/images.py`, `api/data.py`)
5. `meta.stretch` for banded products: per band, `p1`/`p99` over VALID
   pixels only, computed after the default normalisation (below);
   always present; `meta.norm_default` = `"lambert"` for JunoCam and
   `"none"` for JIRAM; `meta.norm_options` lists the supported names.
6. New query parameter `norm` on `frame/{t}.png`, `frame/{t}/rgb.png`,
   and strip `image.png`: `none`; `lambert` = image / max(cos(i), 0.05)
   where `i` is the incidence angle; `minnaert:k` = image /
   (max(cos(i),0.05)^k * max(cos(e),0.05)^(k-1)) with `k` in [0.3, 1.2]
   (default 0.7); `flat:sigma` = image / (Gaussian low-pass of the
   image over valid pixels with sigma px, normalised to unit mean;
   implemented with separable numpy convolution on the valid-filled
   field, no scipy). Pixels with `i >= 88` are invalid regardless.
   `vmin`/`vmax` apply to the normalised values; when absent, the
   per-band `stretch` of the requested `norm` is used (cache the
   stretch per norm name in the meta cache).
7. Optional `stretch=linear|asinh` (default linear) for the PNG mapping.
8. `strips/{id}/stats?band=&norm=` and `stats2d.strip_statistics(ds, band=, norm=)`:
   apply the same normalisation before computing; default `norm` =
   the strip's `norm_default` attr (`lambert` for JunoCam, `none` else);
   the cached stats file name carries the norm.

### Front end (`frontend/src/**`, bundle, docs/gui_v2_notes.md, docs/gui_guide.md)
9. An **Illumination** selector in the Poles and Strips viewers: None /
   Lambert / Minnaert (with a k slider 0.3-1.2) / Flatten (sigma
   slider 8-256 px), initialised from `meta.norm_default`; a
   Linear/Asinh stretch toggle; per-band stretch sliders initialised
   from `meta.stretch[norm][band]`, re-initialised when the norm
   changes, "link bands" off by default for JunoCam.
10. `#debug-state` gains `norm` and `stretch_mode`; e2e: changing the
    illumination selector on the JunoCam stack changes `norm` and the
    sampled pixel; the JunoCam strip statistics request carries the
    norm.

## Files in scope
`src/jiram_catalog/junocam/{stacks,strips,geo}.py`, `src/jiram_catalog/api/{stacks,strips,images,data}.py`,
`src/jiram_catalog/stats2d.py` (norm argument only), `tests/test_junocam_products_offline.py`,
`tests/test_api_offline.py`, `tests/test_stats2d_offline.py` (add cases only),
`frontend/**`, `src/jiram_catalog/webapp/dist/**`, `docs/gui_v2_notes.md`, `docs/gui_guide.md`.
READ-ONLY: everything else. Under the mirror write only the rebuilt
JunoCam stack and strips, the strips index, `gui_cache/`.

## Validation that defines done
```
JIRAM_SKIP_GATES=1 uv run pytest -q
uv run jiram-catalog junocam region-stack --region north_pole_paper --orbits 4 --bands RED,GREEN,BLUE --quality-min A --jobs 4
uv run jiram-catalog junocam strips --orbits 4 --jobs 4
uv run pytest -q tests/test_gate_junocam_photometry.py tests/test_gate_junocam_products.py tests/test_gate_api.py
cd frontend && npm run typecheck && npm test && npm run build
uv run pytest -q tests/test_gate_frontend.py
```
READ-ONLY gate `tests/test_gate_junocam_photometry.py`: the rebuilt
stack has `incidence`, at least 5 time steps, `valid` implies
`incidence < 88` in some band, and `valid` fraction per step < 0.6;
`meta.stretch` present per band for `none` and `lambert`; the RGB
composite PNG at `norm=lambert` has fewer than 3% of valid pixels at
255 in every channel and more than 50% between 30 and 225; all JunoCam
strips have `km_per_px <= 30`; on 3 strips the fraction of variance in
the 64-px low-pass of the RED band drops by at least 40% under
`lambert` relative to `none`; `stats?band=RED&norm=lambert` returns
curves different from `norm=none`.

## Report (at most 35 lines)
Commands and outcomes; counts (images kept, strips kept), the low-pass
variance fractions before/after; judgment calls; ambiguities with the
choice made (implement everything else).
