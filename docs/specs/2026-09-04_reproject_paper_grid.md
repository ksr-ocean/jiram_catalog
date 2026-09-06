# Spec: VICAR reader, reprojection engine, and empirical fit of the paper's polar grid (milestone 3)

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog
(`uv` project; package `jiram_catalog`). Read first:
`src/jiram_catalog/geometry.py` (engine: `KernelSet.for_orbits`,
`frame_geometry`, `pixel_directions`, `FrameGeometry` with per-pixel
`lat`, `lon_east`, `on_planet`, plus `obspos_km`, `et`, `trgepc`,
`frame`, `ifov_rad`), `docs/specs/2026-09-04_geometry_engine.md` (its
contract; note the revised step 3), and `tests/fixtures/pj4_ingersoll2022_map_labels.csv`.

## Goal
1. Read JPL VICAR files. 2. Reproject a JIRAM frame onto a map grid by
exact inverse mapping through the camera model. 3. Recover, by fitting,
the polar map projection used for the 48 published per-frame maps of
Ingersoll et al. 2022 (perijove 4), so later mosaics can be compared to
the paper pixel for pixel.

## Files in scope
- `src/jiram_catalog/vicar.py`        (create)
- `src/jiram_catalog/reproject.py`    (create)
- `scripts/fit_paper_projection.py`   (create)
- `docs/reports/paper_projection_fit.md` and `docs/reports/paper_projection_fit.json` (created by the script)
- `tests/test_reproject_offline.py`   (create)
READ-ONLY: `tests/test_gate_*.py`, `tests/fixtures/`, all other files
under `src/jiram_catalog/` and `docs/`, `CLAUDE.md`, `README.md`,
`pyproject.toml`, `uv.lock` (no new dependencies: numpy, pandas,
spiceypy only; use `numpy.fft` for correlations, no scipy). Never write
under `/expanse/lustre/projects/cla119/kaushiks/JIRAM` (the paper data;
read-only) or `~/scratch`; do not write under the mirror root at all.
Another agent is concurrently editing `geometry.py`, `geo.py`, `cli.py`
and `scripts/lm_half_order.py`; do not touch those.

## Facts (measured 2026-09-04)
- Paper maps: `/expanse/lustre/projects/cla119/kaushiks/JIRAM/n0{1,2,3,4}_15km/n0Na.map … n0Nl.map`
  (48 files). VICAR: 12800-byte ASCII label (`LBLSIZE=12800 FORMAT='REAL'
  … ORG='BSQ' NL=3000 NS=3200 NB=1 … INTFMT='LOW' REALFMT='RIEEE'`) then
  3000 x 3200 float32 LITTLE-endian, line-major. Each file is ONE raw
  frame reprojected (label `TARGET_PIXELS=55296`); background is exactly
  0.0; footprint ~200,000 nonzero pixels; values 0.006–0.55 (radiance).
  All 48 share one common grid: e.g. n01a (CENLAT 83.15, CENLON 305.7)
  has footprint centroid (line 2109, sample 1371), n01e (88.94, 268.4)
  (1795, 1721), n01l (79.89, 132.3) (1269, 2385). Label keys per file:
  `IMAGE_TIME` (= archive START_TIME), `PRODUCT_ID`, `CENLAT`, `CENLON`
  (planetocentric, east), `MINLAT`, `MAXLAT`, `NORTHANG`, `MPS=15.0`
  (km/px), `MPOX=MPOY=3800`, `DU=-90`, `MPROJ=4`, `XOFF=YOFF=0`. The
  meaning of MPOX/MPOY/DU/MPROJ is undocumented (JPL docs offline); the
  fit below must not assume them.
- The fixture CSV holds these labels for all 48 frames (`file`,
  `PRODUCT_ID`, `IMAGE_TIME`, …). Archive RDR frames for orbit 4 are at
  `/expanse/lustre/projects/cla119/kaushiks/jiram_mirror/pds4/juno_jiram_bundle/data_calibrated/orbit04/<PRODUCT_ID>.IMG`
  (128 x 432 float32 LITTLE-endian despite both archive labels claiming MSB/IEEE_REAL, verified 2026-09-05: read with dtype '<f4'; read big-endian the values are garbage ~1e37 with NaNs; M band). Kernels:
  `KernelSet.for_orbits(mirror, [4])`, mirror root
  `/expanse/lustre/projects/cla119/kaushiks/jiram_mirror`. Engine epoch =
  `IMAGE_TIME`, band `"M"`.
- Jupiter radii (pck00010): equatorial 71492 km, polar 66854 km.
  Planetocentric latitude everywhere. Longitudes east-positive 0–360.
- `.tp4` files (TRACKER4 output) are also VICAR: `LBLSIZE=1728`,
  float32, `NL` x `NS` = 114242 x 8 (or similar); the reader must be
  generic (parse NL, NS, NB, FORMAT, INTFMT/REALFMT, ORG, LBLSIZE, and
  the optional EOL labels flag `EOL=1`, which you may treat as
  unsupported with a clear error if encountered).

## `vicar.py`
`read_vicar(path) -> tuple[np.ndarray, dict[str, str | float | int]]`:
parse the label as `KEY=VALUE` tokens (values may be quoted strings,
numbers, or parenthesised tuples; keep repeated keys such as `TASK` as a
list under the key), determine dtype from `FORMAT` (`BYTE` uint8,
`HALF` int16, `FULL` int32, `REAL` float32, `DOUB` float64) and byte
order from `INTFMT`/`REALFMT` (`LOW`/`RIEEE` little-endian, `HIGH`/
`IEEE` big-endian; `VAX` unsupported -> `ValueError`), skip `NLB`
binary header lines and `NBB` binary prefix bytes, and return an array
shaped `(NB, NL, NS)` squeezed to 2-D when `NB == 1`. Provide
`read_vicar_label(path) -> dict` too.

## `reproject.py`
- `class PolarStereo`: parameters `pole_line: float`, `pole_sample: float`
  (0-based pixel coordinates of the pole in the output image),
  `km_per_px: float`, `lon0_deg: float` (the east longitude of the
  meridian that points along the +sample axis), `clockwise: bool`
  (True if east longitude increases clockwise when viewed with line
  increasing downward), `hemisphere: str` (`"N"`/`"S"`),
  `radius_km: float` (sphere radius used by the projection; the fit
  decides). Methods `latlon_to_pixel(lat, lon_east) -> (line, sample)`
  and `pixel_to_latlon(line, sample) -> (lat, lon_east)`, vectorised,
  using the spherical polar stereographic projection true-scale at the
  pole: `rho = 2 R tan((90 - |lat|)/2)` (km), `x = rho sin(lon - lon0)`,
  `y = rho cos(lon - lon0)` with the handedness sign, then
  `sample = pole_sample + x/km_per_px`, `line = pole_line - y/km_per_px`
  (define the exact sign convention in the docstring and make the two
  methods exact inverses; test it).
- `project_to_pixels(geo: FrameGeometry, lat, lon_east) -> (line, sample, visible)`:
  the inverse camera model. Convert (lat, lon) planetocentric to the
  ellipsoid surface point in IAU_JUPITER (radii from
  `spiceypy.bodvrd`), form the geometric vector from `geo.obspos_km` to
  the point, rotate it into the band frame with the inverse of the
  chain the engine used (IAU_JUPITER at `geo.trgepc` -> J2000 -> band
  frame at `geo.et`, and re-apply the forward stellar aberration so the
  ray is the apparent direction, using the same velocity source as the
  engine), then apply the pinhole model inverse of `pixel_directions`:
  `x = dx/dz`, `y = dy/dz`, `line = lines/2 + 0.5 - x/ifov`,
  `sample = samples/2 + 0.5 - y/ifov` (1-based, fractional). `visible`
  is True when the surface normal faces the observer (emission < 90
  deg) and `dz > 0`. Add a round-trip test in the gate's spirit:
  `project_to_pixels(geo, geo.lat[i,j], geo.lon_east[i,j])` returns
  `(i+1, j+1)` within 1e-3 px for all on-planet pixels of one frame
  (this is the correctness oracle for the inverse model; put it in the
  offline tests using a live frame if kernels are available, skipping
  otherwise, AND run it in the fit script).
- `reproject_frame(image, geo, grid: PolarStereo, shape, *, method="bilinear") -> (out, weight)`:
  for every output pixel compute (lat, lon) via `grid.pixel_to_latlon`,
  then `(line, sample, visible)` via `project_to_pixels`, then sample
  `image` bilinearly at fractional (line-1, sample-1); pixels outside
  the detector, not visible, or hitting NaN input get `out = 0` and
  `weight = 0`; otherwise `weight = 1`. Restrict computation to the
  bounding box of the frame's footprint projected through
  `grid.latlon_to_pixel(geo.lat, geo.lon_east)` padded by 3 px, for
  speed. `out` and `weight` have `shape`.

## `scripts/fit_paper_projection.py`
1. Load kernels for orbit 4, the fixture, and all 48 `.map` files and
   RDR frames.
2. For each frame compute `geo = frame_geometry(IMAGE_TIME, "M", kernels)`
   and the footprint centroid in the paper map (mean line/sample of
   nonzero pixels) and the centroid of the frame's on-planet pixels in
   the projection plane: `(x_km, y_km)` from the spherical stereographic
   formulas with `lon0 = 0`, `clockwise = False`, for a trial radius.
3. Solve the linear least-squares map `[line, sample]^T = A [x, y]^T + b`
   over the 48 centroid pairs (`A` 2x2, `b` 2x1). Convert `A` into
   `km_per_px`, `lon0_deg`, `clockwise`, and the pole pixel `b`; report
   the two singular values of `A` (they must agree within 1%, otherwise
   the projection is not a similarity of a stereographic plane and the
   report must say so). Do this for radius candidates 66854, 71492, and
   the polar-region conformal radius `sqrt(a*c)`; keep the one with the
   smallest centroid RMS residual.
4. Refine per frame by image registration: reproject the RDR frame with
   the fitted `PolarStereo` onto a 3000x3200 canvas, then find the
   integer + sub-pixel shift between our image and the paper map by
   phase correlation (`numpy.fft`, parabolic peak refinement) over the
   footprint bounding box; record `(dline, dsample)` and the normalised
   cross-correlation (NCC) between the two images over pixels where
   both are nonzero, after the shift. Also record the median ratio of
   paper to ours over those pixels.
5. If the median of the 48 shifts is not within 0.25 px of zero, apply
   it to the pole pixel and repeat step 4 once.
6. Write `docs/reports/paper_projection_fit.json` with keys
   `radius_km`, `km_per_px`, `lon0_deg`, `clockwise`, `hemisphere`,
   `pole_line`, `pole_sample`, `singular_values`, `centroid_rms_px`,
   and `frames`: a list of 48 records `{file, product_id, dline, dsample,
   ncc, median_ratio, n_overlap}`; and a readable
   `docs/reports/paper_projection_fit.md` with the same numbers, a
   short description of the method, the best/worst frames, and how
   `MPOX/MPOY/DU/MPS/NORTHANG` relate to the fitted values if any
   relation is evident (state it as observation, not fact).
7. Also expose the fitted grid in code: `reproject.py` must define
   `PAPER_GRID: PolarStereo` with the fitted values (write them into the
   module after the fit, rounded to 1e-4), so the gate can import it.

## Offline tests (`tests/test_reproject_offline.py`)
- VICAR reader on a synthetic file you write in a tmp dir (both byte
  orders, NB=1 and NB=2, NLB/NBB > 0) and on
  `/expanse/lustre/projects/cla119/kaushiks/JIRAM/n01_15km/n01a.map`
  (shape, nonzero count 197089, `IMAGE_TIME` parsed) and one `.tp4` file
  from `/expanse/lustre/projects/cla119/kaushiks/JIRAM/JIRAM velocity_vectors at 45 km/`
  (shape reported, read succeeds).
- `PolarStereo` round trip on 1000 random (lat, lon) in the hemisphere:
  `pixel_to_latlon(latlon_to_pixel(.))` within 1e-9 deg; the pole maps to
  `(pole_line, pole_sample)`; a point at lat 80 and lon `lon0` lies on
  the +sample axis; handedness flips the sign of the cross-track
  coordinate.
- `project_to_pixels` round trip on a live frame if
  `JIRAM_SKIP_GATES != "1"` and kernels load, else skip.

## Validation that defines done
```
uv sync
JIRAM_SKIP_GATES=1 uv run pytest -q tests/test_reproject_offline.py
uv run python scripts/fit_paper_projection.py
uv run pytest -q tests/test_reproject_offline.py tests/test_gate_paper_projection.py
```
(Other gate files may be failing for reasons unrelated to you while
another agent works; run only the two files above and say so.) The
READ-ONLY gate `tests/test_gate_paper_projection.py` requires: 48 frame
records; max |shift| <= 1.0 px; min NCC >= 0.90; singular values within
1%; `PAPER_GRID` equal to the JSON; and a live reprojection of n01a
matching `n01a.map` with NCC >= 0.90 and centroid offset <= 1 px.

## Report (at most 50 lines)
Commands and outcomes; the fitted parameters and residual statistics
(centroid RMS, shift median/max, NCC min/median, median ratio); which
radius won and by how much; the observed relation, if any, between the
fitted pole/scale/orientation and MPOX/MPOY/DU/MPS/NORTHANG; every
judgment call; any ambiguity with options and the choice you made.
