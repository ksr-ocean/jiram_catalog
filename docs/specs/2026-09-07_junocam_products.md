# Spec: JunoCam products through the existing layers (JunoCam milestone 3a, backend)

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog.
Read first: the amendment at the end of `docs/specs/2026-09-06_api_contract.md`
(normative), `src/jiram_catalog/junocam/` (milestone 1 and 2: index,
quality, `geometry.image_geometry`, `reproject.reproject_image`,
`limb`), `stacks.py`, `strips.py`, `stats2d.py`, `api/*.py`,
`api/data.py`, `geo.py` (for the summary conventions: longitude arc,
pole_inside), `cli.py` (registration pattern).

## Goal
JunoCam images become rows of the catalog with footprints and quality,
and sources for region stacks and strips with a `band` dimension; the
API serves them under the amended contract. Nothing changes for JIRAM
users.

## Files in scope
- `src/jiram_catalog/junocam/geo.py` (create): `junocam geo --orbits`
  builds `<mirror>/junocam/index/junocam_geo.parquet`, one row per RDR
  image with `img_present`: `image_geometry(row, kernels, refine=False)`
  on a subsample (every 4th frame, every 8th line and sample) to get
  `on_planet_frac`, swath-centre boresight values (`bore_lat`,
  `bore_lon_east`, `bore_emission`, `bore_incidence`) at the central
  frame, `median_pixel_km` (range x IFOV = pixel size / focal length
  over on-planet samples), `dayside_frac`, `min_lat`, `max_lat`,
  `lon_min_east`, `lon_max_east`, `lon_span_deg`, `pole_inside` (rules
  of `geo.py`), and a footprint outline `fp_lon`/`fp_lat` (up to 64
  vertices: the boundary of the on-planet mask of the subsampled
  swath, traced in image order and thinned); `geo_ok`, `geo_error`;
  spawn workers, kernels per worker per orbit, cache-free.
- `src/jiram_catalog/junocam/stacks.py` (create): `select_images(mirror, region, orbits, *, quality_min="A", max_emission=80.0)`
  from `junocam_images` x `junocam_quality` x `junocam_geo` (RDR, tier
  <= quality_min, footprint overlap test as in `stacks.select_frames`
  using the outline), and `build_stack(mirror, region, images, bands, *, refine=True, jobs)`
  producing an `xarray.Dataset` with dims `(time, band, y, x)`, coords
  `time`, `band` (band names), `x_km`, `y_km`, `lat`, `lon_east`,
  per-time `product_id`, `seq_id` (= product id), `orbit`, `quality_tier`,
  `dt_refined_s`; variables `image`, `valid`, `emission` (per band,
  since bands see slightly different geometry), `n_frames` (uint8
  overlap count); attrs as JIRAM stacks plus `instrument="JunoCam"`,
  `bands`, `level="frame"`. Uses `image_geometry(refine=True)` (cached
  by product) and `reproject.reproject_image`. Writes with
  `stacks.write_stack` (extend it to accept the `band` dim) to
  `<mirror>/regions/<region>/junocam_<bands>_orbits<SPEC>_frame.nc`.
- `src/jiram_catalog/junocam/strips.py` (create): one strip per image
  (a swath is already a contiguous strip): local_ortho grid centred on
  the swath's on-planet centroid, resolution class from
  `median_pixel_km`, canvas from the footprint (cap 6000 px), dims
  `(band, y, x)`, same variables/attrs as JIRAM strips plus
  `instrument="JunoCam"` and `bands`; rows appended to the same
  `<mirror>/strips/strips.parquet` with `instrument` and `bands`
  columns (JIRAM rows get `instrument="JIRAM"`, `bands=band` on the
  next rebuild; write a small migration that adds the two columns to an
  existing index if absent), files under `<mirror>/strips/junocam/orbitNN/`.
  `strip-stats` and `stats2d.strip_statistics` accept a `band` argument
  for multi-band strips (default first band).
- `src/jiram_catalog/junocam/cli.py` (modify: add `geo`, `region-stack`, `strips` sub-subcommands)
- `src/jiram_catalog/stacks.py` (modify only `write_stack`/`read_stack` to carry a `band` dimension when present; existing behaviour unchanged)
- `src/jiram_catalog/stats2d.py` (modify: `strip_statistics(ds, band=None)`; select the band before computing; existing signature keeps working)
- `src/jiram_catalog/strips.py` (modify: `load_strips` gains `instrument=None`, `band=None` semantics for multi-band rows; `read_strip` resolves JunoCam paths)
- `src/jiram_catalog/api/catalog.py`, `api/data.py`, `api/stacks.py`, `api/strips.py`, `api/app.py` (modify per the amendment)
- `tests/test_junocam_products_offline.py` (create), `tests/test_api_offline.py` (add cases)
READ-ONLY: everything else, including the top-level `cli.py` (the
junocam sub-subcommands are wired inside `junocam/cli.py`, which the
lead already registered), `pyproject.toml`, `uv.lock`, gates,
`frontend/`. Under the mirror write only `junocam/index/junocam_geo.parquet`,
`regions/north_pole_paper/junocam_*.nc`, `strips/junocam/**`, the
strips index, `junocam/geometry_cache/`, `gui_cache/`.

## Validation that defines done
```
JIRAM_SKIP_GATES=1 uv run pytest -q
uv run jiram-catalog junocam geo --orbits 4 --jobs 8
uv run jiram-catalog junocam region-stack --region north_pole_paper --orbits 4 --bands RED,GREEN,BLUE --quality-min A --jobs 4
uv run jiram-catalog junocam strips --orbits 4 --jobs 4
uv run pytest -q tests/test_gate_junocam_products.py tests/test_gate_api.py tests/test_gate_strips.py tests/test_gate_regions_pj4.py
```
READ-ONLY gate `tests/test_gate_junocam_products.py`: `junocam_geo`
has >= 100 orbit-4 rows with `geo_ok`, finite bounds, footprints of
>= 8 vertices; the north-pole JunoCam RGB stack exists with >= 15 time
steps, dims `(time, band, y, x)`, bands RED/GREEN/BLUE, finite pixels,
and its per-time `dt_refined_s` finite for >= half; the strips index
has >= 50 JunoCam rows for orbit 4 with three bands and files that
open; `strip_statistics(ds, band="RED")` works; the API's
`frames.arrow` has >= 100 JunoCam rows with `instrument`, `bands`,
`quality_tier`, non-empty `fp_lon`, and the JIRAM rows unchanged in
count; `summary?instrument=JunoCam` counts them; the stack listing
carries `instrument` and `bands`; `frame/0.png?band=RED` and
`frame/0/rgb.png` decode; a JunoCam strip's `stats?band=GREEN` returns
curves.

## Report (at most 40 lines)
Commands and outcomes; counts and timings (geo, stack, strips);
judgment calls; ambiguities with the choice made (implement everything else).
