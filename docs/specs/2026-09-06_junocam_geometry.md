# Spec: JunoCam per-framelet geometry engine with limb-fit timing refinement (JunoCam milestone 2)

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog.
Read first: `docs/reports/junocam_navigation_quality.md`,
`docs/reports/junocam_archive_recon.md`, the instrument kernel
`<mirror>/spice/ik/juno_junocam_v03.ti` (its comments contain the
normative camera recipe: per-band distortion centre `cx, cy`,
`k1, k2`, `fl = FOCAL_LENGTH/PIXEL_SIZE`, the `distort`/`undistort`
functions, the framelet-coordinate convention with (0,0) at the upper
left of a 1648-wide framelet, and the timing rule `t_i = START_TIME +
START_TIME_BIAS + i*(INTERFRAME_DELAY + INTERFRAME_DELTA)`), then
`src/jiram_catalog/geometry.py` (reuse `ellipsoid_intercept`, the
aberration helper, `KernelSet`, and the LT+S recipe: observer position
and target epoch from the boresight, inverse stellar aberration on the
rays, rotation to `IAU_JUPITER` at the target epoch), `regions.py`
(`RegionGrid`, `local_ortho`), and the junocam package created by
milestone 1 (`junocam/labels.py`, `junocam/index.py`: index columns
`product_id`, `level`, `path`, `start_time`, `interframe_delay_s`,
`exposure_ms`, `filters`, `n_bands`, `n_framelets`, `lines`, `samples`,
`sample_bits`, `companded`).

## Goal
For a JunoCam RDR image: the direction of every pixel of every framelet
in the camera frame, its intercept with the Jupiter ellipsoid at the
framelet's epoch, and the derived angles; a per-image refinement of the
start-time offset by fitting the observed limb; a reprojection of the
image's bands onto a region grid; and gates that prove the geometry is
right without any archive geometry to lean on.

## Files in scope (create)
- `src/jiram_catalog/junocam/camera.py`  (camera model from the IK)
- `src/jiram_catalog/junocam/images.py`  (image reader)
- `src/jiram_catalog/junocam/geometry.py`
- `src/jiram_catalog/junocam/limb.py`    (limb detection and refinement)
- `src/jiram_catalog/junocam/reproject.py`
- `tests/test_junocam_geometry_offline.py`
- `scripts/junocam_pj4_geometry_check.py` (runs the gate's computations and writes `docs/reports/junocam_pj4_geometry.md`)
READ-ONLY: everything else (the milestone-1 modules, top-level
`cli.py`, `pyproject.toml`, `uv.lock`, gates). Under the mirror write
only under `<mirror>/junocam/geometry_cache/`. No new dependencies.

## Normative model
- Frames: `JUNO_JUNOCAM` (FK `juno_v12.tf`, fixed to the bus), band
  instrument ids -61501 BLUE, -61502 GREEN, -61503 RED, -61504 METHANE
  (IK). Load the IK with `spiceypy.furnsh` in addition to
  `KernelSet.for_orbits(mirror, [orbit])`; read every parameter with
  `gdpool`; never hard-code the numbers.
- Pixel -> direction in `JUNO_JUNOCAM`: for band `b`, framelet row `y`
  in [0,128) and sample `x` in [0,1648), pixel centre at (x+0.5, y+0.5),
  `cam = undistort((x+0.5 - cx_b, y+0.5 - cy_b))`, `v = (cam[0], cam[1], fl)`
  normalised. Direction -> pixel (`project`): `alpha = v[2]/fl`,
  `cam = distort((v[0]/alpha, v[1]/alpha))`, `x = cam[0] + cx_b - 0.5`,
  `y = cam[1] + cy_b - 0.5`; a pixel is on the band strip when
  `0 <= y < 128` and `0 <= x < 1648`; the 23 dark columns at the start
  of each line and the trailing overscan are flagged by a `photoactive`
  mask (columns 23..23+1608 are photoactive).
- Framelet epoch: `et_i = str2et(START_TIME) + START_TIME_BIAS +
  i*(INTERFRAME_DELAY + INTERFRAME_DELTA) + dt_refined`, the same for
  all bands of frame `i` (the strips are read together); `dt_refined`
  is 0 before refinement. Exposure-midpoint offsets are absorbed by
  `dt_refined`; document this.
- Intercepts: for frame `i`, observer position and target epoch from
  the LT+S recipe of `geometry.py` using the camera boresight
  (`JUNO_JUNOCAM` +Z) at `et_i` (fallback to `spkpos` when the
  boresight misses), inverse stellar aberration on the pixel rays,
  rotation to `IAU_JUPITER` at the target epoch, vectorised
  `ellipsoid_intercept`; per-pixel `lat` (planetocentric), `lon_east`,
  `emission`, `incidence`, `phase`, `range_km`, `on_planet`, as
  float32 arrays shaped `(n_frames, n_bands, 128, 1648)`.
- Limb refinement (`limb.py`): predicted limb = boundary of
  `on_planet` along each column of a framelet (the row where the ray
  first hits the planet, both from above and below); observed limb =
  the row where the column's intensity, smoothed over 5 rows, crosses
  midway between a space level (median of the framelet's off-planet
  pixels per the prediction) and the local planet level (median of the
  first 12 on-planet rows), evaluated only on columns where the
  predicted limb is on the dayside (`incidence < 85 deg` at the first
  on-planet pixel) and away from the strip ends. Residual = observed
  minus predicted row (px, signed). Refinement: minimise the median
  absolute residual over `dt` in [-0.25, 0.25] s with a golden-section
  search (tolerance 0.5 ms), recomputing only the limb framelets on a
  column subsample (every 8th column); report `dt_refined_s`,
  `limb_residual_px_before`, `limb_residual_px_after`, `n_limb_points`.
  Images with fewer than 200 limb points are left unrefined
  (`dt_refined_s = NaN`), flagged.
- Reprojection (`reproject.py`): `reproject_image(image, geo, grid,
  bands=None) -> (data (n_bands, ny, nx) float32 NaN-filled, count (n_bands, ny, nx) uint8)`
  by inverse mapping: for each output cell (lat, lon) -> surface point;
  for each frame, the vector from the frame's observer position to the
  point, visibility (`emission < 90` and in front), rotate into
  `JUNO_JUNOCAM` at that frame's epoch, `project` to each band strip,
  bilinear sample where on-strip and photoactive; average overlapping
  frames per band. Restrict to the bounding box of the image's
  on-planet pixels projected onto the grid. Vectorise over cells;
  loop over frames.
- Cache: per image, the per-frame observer positions, rotation matrices,
  `dt_refined_s` and the limb summary as a small NetCDF under
  `<mirror>/junocam/geometry_cache/<product_id>.nc` so re-projection
  need not repeat the limb fit.

## Offline tests (`tests/test_junocam_geometry_offline.py`; IK only, no CK/SPK)
Camera round trip pixel -> vector -> pixel within 1e-6 px for 1000
random pixels in each band; the band boresights from `getfov`
(`INS-6150N_BORESIGHT`) project to pixels inside the respective strip
and near `(cx, cy)`; the timing rule on a synthetic label; the
photoactive mask; limb detection on a synthetic framelet with a known
edge (residual < 0.2 px) and the golden-section search recovering a
known offset on a synthetic problem; the reader on the two sample
images in `<mirror>/junocam_samples/` (shape `(n_frames, n_bands, 128, 1648)`,
dtype, band order, decompanding of the EDR via the SIS square-root
table into values monotonic with the RDR where both exist).

## Validation that defines done
```
JIRAM_SKIP_GATES=1 uv run pytest -q tests/test_junocam_geometry_offline.py
uv run python scripts/junocam_pj4_geometry_check.py
uv run pytest -q tests/test_junocam_geometry_offline.py tests/test_gate_junocam_geometry.py
```
READ-ONLY gate `tests/test_gate_junocam_geometry.py` reads
`docs/reports/junocam_pj4_geometry.json` written by the script and
checks: at least 20 orbit-4 day-033 RDR images were refined; median
`|dt_refined_s|` < 0.1 and max < 0.25; median limb residual after
refinement < 1.0 px and the 90th percentile < 2.0 px; for 5 images,
red-green and red-blue registration shifts on a 10 km/px local grid
(phase correlation of the reprojected bands over their overlap) <= 1.0
px; per-image geometry time < 60 s; and a live check that
`image_geometry` on one image returns finite `lat` where `on_planet`.

## Report (at most 40 lines)
Commands and outcomes; the distribution of `dt_refined_s` and limb
residuals before/after; registration shifts; timings; judgment calls;
ambiguities with the choice made.
