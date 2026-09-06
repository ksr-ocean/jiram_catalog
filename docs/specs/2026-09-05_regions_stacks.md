# Spec: region registry, region stacks, movies, and export to the velocity-model layout (milestone 4a)

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog
(`uv` project; package `jiram_catalog`; argparse CLI in
`src/jiram_catalog/cli.py`). Read first: `src/jiram_catalog/reproject.py`
(`PolarStereo` incl. its `projection="orthographic"` mode, `PAPER_GRID`,
`project_to_pixels`, `reproject_frame`), `geometry.py` (`KernelSet`,
`frame_geometry`, `ellipsoid_intercept`), `geo.py` (`load_geo`,
`frames_with_geo`), `index.py` (`load_frames`), `cli.py`, and
`docs/reports/paper_projection_fit.md`.

## Goal
Named regions on Jupiter; for a region, select every frame that
overlaps it, reproject each onto the region's grid, and store the
result as a time stack with masks and metadata; composite frames of one
spin sequence into one snapshot; render a movie; export constant-cadence
triples in the layout the user's optical-flow model reads.

## Files in scope
- `src/jiram_catalog/regions.py`      (create) registry + `RegionGrid`
- `src/jiram_catalog/stacks.py`       (create) selection, stack build, composites, NetCDF I/O
- `src/jiram_catalog/movie.py`        (create)
- `src/jiram_catalog/export_goflow.py`(create)
- `src/jiram_catalog/cli.py`          (modify: add `regions`, `region-stack`, `movie`, `export-goflow`)
- `configs/regions.yaml`              (create)
- `tests/test_regions_offline.py`     (create)
- `pyproject.toml`, `uv.lock`         (`uv add xarray netcdf4 pyyaml matplotlib imageio imageio-ffmpeg`)
READ-ONLY: `tests/test_gate_*.py`, `tests/fixtures/`, all other files
under `src/jiram_catalog/` and `docs/`, `CLAUDE.md`, `README.md`.
Never write under `/expanse/lustre/projects/cla119/kaushiks/JIRAM` or
`~/scratch`. Under the mirror root write only under `<mirror>/regions/`
(create it). Another agent is concurrently creating
`src/jiram_catalog/tracking.py`, `scripts/classical_tracking_pj4.py`
and `tests/test_tracking_offline.py`; do not touch those. A background
job may rewrite `<mirror>/index/frames_geo.parquet` once during the
next hour; if a read of it fails, wait 30 s and retry once.

## Facts
- Mirror root `/expanse/lustre/projects/cla119/kaushiks/jiram_mirror`
  (env `JIRAM_MIRROR` overrides; same resolution as the other commands).
  `index/frames.parquet` (85,108 frames), `index/frames_geo.parquet`
  (one row per frame and band half; columns include `product_id`,
  `orbit_dir`, `half`, `start_time`, `geo_ok`, `on_planet_frac`,
  `bore_*`, `c1_lat..c4_lon`, `min_lat`, `max_lat`, `lon_min_east`,
  `lon_max_east`, `lon_span_deg`, `pole_inside`, `mean_emission`,
  `median_pixel_km`). Image data mirrored for orbits 4 and 24 only
  (`pds4/juno_jiram_bundle/data_calibrated/orbitNN/<product_id>.IMG`,
  float32 LITTLE-endian, 128 or 256 lines x 432; for 256-line products
  the top 128 lines are the L band and the bottom 128 the M band, see
  `geometry.LM_HALF_ORDER`). Use `reproject.read_rdr` if it exists,
  otherwise read with `'<f4'`.
- Frame geometry: `frame_geometry(start_time, half, kernels)` with
  `KernelSet.for_orbits(mirror, [orbit])`; 35 ms per frame. A frame's
  image for a half is the corresponding 128-line block.
- Published grid (`PAPER_GRID`): orthographic view of the ellipsoid onto
  the equatorial plane in body-fixed coordinates, 15 km/px, pole at
  0-based (line 1800, sample 1800) of a 3000 x 3200 canvas, +sample
  along 90 E, counter-clockwise, hemisphere N. Paper maps at
  `/expanse/lustre/projects/cla119/kaushiks/JIRAM/n0N_15km/n0Nx.map`
  (read with `vicar.read_vicar`); the 48 paper frames and their times
  are in `tests/fixtures/pj4_ingersoll2022_map_labels.csv`.
- Orbit-4 sequences of interest: `seq_id` values of the 48 paper frames
  (four sequences, 12 frames each, starting 11:40:03, 11:48:10,
  11:56:17, 12:04:24 UTC on 2017-02-02; spacing 487 s).
- Velocity-model input layout (from the user's repo, read-only): per
  realization a NetCDF4 file `realization.nc` with variables
  `image(frame, y_img, x_img)` float32 (invalid pixels zero-encoded),
  `valid(frame, y_img, x_img)` bool, `loggrad(frame, y_img, x_img)`
  float32 = log10(|grad image|) with the gradient per metre; attrs
  `dx_img_m`, `dt_img_s` (one constant per realization), `units_velocity
  = "m s-1"`; spatial axes last, row index increases with +y, column
  with +x; a sidecar `manifest.json`; dataset root holds `rNNNNN/`
  directories plus `spec.json` and `dataset_manifest.json`. No truth
  velocities are available here: omit `u_mid`/`v_mid`.

## `regions.py`
- `configs/regions.yaml` entries, each with `name`, `projection`
  (`polar_ortho` or `local_ortho`), `km_per_px`, and:
  - `polar_ortho`: `hemisphere` (`N`/`S`), `shape: [rows, cols]`,
    `pole_pixel: [row, col]` (0-based, may be fractional). Convention
    (identical to the paper for N): column <-> body-fixed Y (90 E)
    increasing with column, row <-> body-fixed X (0 E) increasing with
    row; for S use column <-> -Y so that the map is viewed from outside
    the planet with longitude increasing counter-clockwise in both
    hemispheres (document the exact formulas in the docstring).
  - `local_ortho`: `center: [lat0_deg, lon0_east_deg]` (planetocentric),
    `shape: [rows, cols]`, `center_pixel: [row, col]`. Tangent-plane
    orthographic on the ellipsoid: local unit vectors at the centre
    surface point `east`, `north` (north = component of +Z orthogonal
    to the ellipsoid normal), `up` (ellipsoid normal); pixel -> point:
    `p = c + x_km*east + y_km*north`, then intersect the ray from
    `p + 2*a*up` along `-up` with the ellipsoid (use
    `geometry.ellipsoid_intercept`; NaN if it misses); point -> pixel:
    `x_km = (P - c) . east`, `y_km = (P - c) . north`, with
    `visible = (P - c) . up > -tol` (points on the far side are not on
    the grid). `x_km = (col - col0)*km_per_px`, `y_km = (row - row0)*km_per_px`.
- Initial registry: `north_pole_paper` (polar_ortho N, 15 km/px, shape
  [3000, 3200], pole_pixel [1800, 1800] = `PAPER_GRID`), `south_pole`
  (polar_ortho S, 15 km/px, shape [3600, 3600], pole [1800, 1800]),
  `north_pole` (same as south_pole but N), and one mid-latitude example
  `neb_15n` (local_ortho, centre [15, 180], 10 km/px, shape [1200,
  2400], centre pixel [600, 1200]).
- `class RegionGrid`: `from_yaml(name, path=configs/regions.yaml)`,
  `pixel_to_latlon(row, col)`, `latlon_to_pixel(lat, lon_east) -> (row,
  col, visible)`, `lat_lon_grids()` (2-D arrays), `bbox_latlon()` (min/max
  lat and the longitude arc of the grid boundary; for a grid containing a
  pole, the arc is 0-360), `to_dict()`. Must reproduce `PAPER_GRID`
  exactly for `north_pole_paper` (test: 1000 random lat/lon points map
  to the same (line, sample) within 1e-6 px).

## `stacks.py`
- `select_frames(mirror, region, orbits, band, *, max_emission=80.0, min_on_planet=0.02) -> pd.DataFrame`:
  rows of `frames_with_geo` with `half == band` (for `band` in L/M; a
  frame with `band == "LM"` contributes its `half == band` row),
  `geo_ok`, `on_planet_frac >= min_on_planet`, `mean_emission <=
  max_emission`, and whose footprint overlaps the region: prefilter with
  `min_lat/max_lat` and the longitude arc against `region.bbox_latlon()`,
  then exact test by mapping the frame's on-planet pixels (every 4th
  line and sample from a fresh `frame_geometry`) through
  `latlon_to_pixel` and requiring at least one visible pixel inside the
  canvas. Return the selected rows sorted by `start_time` with an added
  column `n_inside` (count of those sampled pixels inside the canvas).
- `build_stack(mirror, region, frames, band, *, crop=True, margin_px=16) -> xarray.Dataset`:
  for each frame (kernels loaded once per orbit, frames processed
  sequentially in one process; `--jobs` may parallelise across orbits
  using `spawn` workers) read the image block for the band, compute
  `frame_geometry`, `reproject_frame` onto the region canvas, and also
  reproject the per-pixel emission angle (bilinear from `geo.emission`)
  so each stack pixel carries its emission. Dataset dims `(time, y, x)`;
  coords `time` (datetime64[ns]), `y_km`, `x_km` (1-D, from the region),
  `lat`, `lon_east` (2-D float32, from `lat_lon_grids`), and 1-D
  per-time coords `product_id`, `seq_id`, `orbit`, `band`, `half`,
  `start_time_iso`, `bore_emission`; variables `image` (float32, NaN
  where invalid), `valid` (bool), `emission` (float32, NaN where
  invalid); attrs `region` (name), `projection` (JSON of
  `region.to_dict()`), `km_per_px`, `band`, `level` (`frame`),
  `row0`/`col0` (crop offset into the full canvas; 0 when not cropped),
  `created_utc`, `software` (`jiram_catalog <version>`). With
  `crop=True` the dataset covers the union bounding box of all valid
  pixels plus `margin_px`.
- `composite_sequences(stack) -> xarray.Dataset`: group by `seq_id`,
  average `image` over valid pixels (mean), `valid` = any, `emission` =
  min over contributing frames; `time` = the group's first `start_time`;
  per-time coords `seq_id`, `n_frames`, `orbit`, `time_first`,
  `time_last`; attrs as the input with `level = "sequence"`.
- `write_stack(ds, path)` / `read_stack(path)`: NetCDF4 via xarray with
  zlib compression, 1-frame chunks; `image` and `emission` stored with
  `_FillValue` NaN.
- CLI `region-stack --region NAME --orbits SPEC --band L|M [--level frame|sequence] [--out PATH] [--no-crop] [--max-emission 80] [--jobs N]`:
  default `--out <mirror>/regions/<region>/<band>_orbits<SPEC>_<level>.nc`.
  Prints the selection count, stack shape, valid-pixel fraction, and the
  output path. `regions` subcommand lists the registry with grid sizes
  and extents in km and degrees.

## `movie.py`
`jiram-catalog movie STACK.nc --out FILE.mp4|.gif [--fps 4] [--pct 1 99] [--cmap gray]`:
one frame per time step, `image` displayed with `origin="lower"`
(row index increases with +y), NaN transparent over a dark background,
robust percentile stretch computed once over the whole stack, a title
with the time stamp (UTC) and the `seq_id`/`product_id`, and a scale
bar in km. MP4 via `imageio` with the `imageio-ffmpeg` backend (no
system ffmpeg exists on this node); GIF via `imageio` when the suffix is
`.gif`. Frames rendered with matplotlib (`Agg` backend) at a fixed dpi
such that the longer canvas side is at most 1600 px.

## `export_goflow.py`
`jiram-catalog export-goflow STACK.nc --out DIR [--dt-tol 0.05] [--min-frames 3] [--crop-to-valid]`:
find maximal runs of consecutive time steps whose spacing is constant
within `dt_tol` (relative to the run's median spacing) and at least
`min_frames` long; each run becomes a realization `rNNNNN/` with
`realization.nc` holding `image(frame, y_img, x_img)` float32 (NaN ->
0), `valid(frame, y_img, x_img)` bool, `loggrad(frame, y_img, x_img)`
float32 = `log10(max(|grad image|, 1e-30))` with the gradient computed
by central differences in units per metre (`dx_img_m = km_per_px *
1000`), invalid pixels 0 in `loggrad` too; attrs `dx_img_m`,
`dt_img_s` (median spacing, seconds), `units_velocity = "m s-1"`,
`units_image = "W m-2 sr-1 um-1"`, `region`, `band`, `level`,
`projection`, `source = "jiram_catalog"`; and a `manifest.json` with the
frames' `product_id`, `seq_id`, time stamps, and the run's dt. Dataset
root gets `spec.json` (region, band, level, dx, dt tolerance, source
stack path) and `dataset_manifest.json` (list of realizations with
frame counts and dt). With `--crop-to-valid`, crop to the bounding box
of pixels valid in all frames of the run.

## Offline tests (`tests/test_regions_offline.py`; no kernels)
- `north_pole_paper` equals `PAPER_GRID` (round trip on 1000 points).
- `polar_ortho` S and `local_ortho` round trips (pixel -> latlon ->
  pixel within 1e-6 px on visible points); far-side points are not
  visible; the centre pixel maps to the centre lat/lon.
- `composite_sequences` on a synthetic stack (two sequences, NaN
  patterns) gives the expected means, `valid` any, `emission` min.
- Constant-cadence run detection on synthetic time axes (one run of 3,
  one of 5 with a gap, a jittered run within/outside tolerance).
- `loggrad` on a synthetic image with known gradient.
- Movie writer produces an `.mp4` and a `.gif` from a tiny synthetic
  stack (2 frames, 32x32) and `imageio` reads back the same number of
  frames.

## Validation that defines done
```
uv sync
JIRAM_SKIP_GATES=1 uv run pytest -q tests/test_regions_offline.py
uv run jiram-catalog regions
uv run jiram-catalog region-stack --region north_pole_paper --orbits 4 --band M --level frame
uv run jiram-catalog region-stack --region north_pole_paper --orbits 4 --band M --level sequence
uv run jiram-catalog movie <mirror>/regions/north_pole_paper/M_orbits4_sequence.nc --out <mirror>/regions/north_pole_paper/M_orbits4_sequence.mp4
uv run jiram-catalog export-goflow <mirror>/regions/north_pole_paper/M_orbits4_sequence.nc --out <mirror>/regions/north_pole_paper/goflow_M_orbits4
uv run pytest -q tests/test_regions_offline.py tests/test_gate_regions_pj4.py
```
The READ-ONLY gate `tests/test_gate_regions_pj4.py` checks: the frame
stack contains all 48 paper frames; for n01a the stack image (offset by
`row0/col0`) correlates with the paper map at >= 0.98 over pixels valid
in both; the sequence stack has at least the four paper sequences with
`n_frames == 12` and more valid pixels than any single frame; the
export produced at least one realization of >= 3 frames with `dt_img_s`
within 5% of 487 s, `dx_img_m == 15000`, finite `loggrad` where valid;
the movie exists with as many frames as time steps.

## Report (at most 50 lines)
Commands and outcomes; selection counts and stack shapes; valid
fractions; the n01a correlation; export runs found (frames, dt); movie
size and duration; every judgment call; any ambiguity with options and
the choice made (implement everything else).
