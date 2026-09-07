# Data products

This is the schema reference for every file `jiram-catalog` reads or
writes. Column lists were checked against the actual Parquet/NetCDF
files under the mirror (`uv run jiram-catalog config` prints the mirror
path in use); mismatches found during that check are called out inline
rather than silently corrected. See `docs/architecture.md` for how these
files relate to one another and `docs/usage.md` for the commands that
produce them.

Conventions used throughout (from `AGENTS.md` and the source): planeto­
centric latitude, east-positive longitude in `[0, 360)`; SI units unless
a column name says otherwise (`_km`, `_deg`, `_s`); arrays are
`(..., y, x)` with row index increasing with +y, plotted with
`origin="lower"`; native JIRAM image radiance is `W m-2 sr-1 um-1`
and the mirrored native JunoCam RDR samples are `DN`; NaN marks "not
computed" or "off-planet", never zero.

## `manifest/manifest.parquet`

One row per file listed in the PDS4 archive (label and data files
together), written by `jiram-catalog manifest`. 9 columns, verified
against the mirror (1,025,991 rows there):

| column | dtype | meaning |
| --- | --- | --- |
| `orbit` | int16 | orbit number from the directory name (`orbitNN`) |
| `basename` | str | file name as listed |
| `ext` | str | extension, no dot, case as listed |
| `family` | str | `IMG`, `SPE`, `LOG_IMG`, `LOG_SPE`, or `OTHER` |
| `kind` | str | `labels` (`.LBL`/`.xml`), `data` (`.IMG`/`.DAT`/`.TAB`), or `other` |
| `product_id` | str | basename without extension |
| `url` | str | archive URL |
| `listed_size_bytes` | int64 | approximate (Apache K/M rounding); -1 if absent |
| `listed_mtime` | str | as listed |

## `index/frames.parquet`

One row per mirrored `JIR_IMG_RDR_*.LBL`, i.e. the archive's own,
label-carried view of each frame (camera products only -- imager, not
spectrometer; see `docs/decisions.md`). Written by `jiram-catalog
index`. 81 columns, verified against the mirror (85,108 rows, matching
the archive-wide product count).

Identity and bookkeeping:

| column | dtype | meaning |
| --- | --- | --- |
| `product_id` | str | archive product id, no extension |
| `orbit` | float64 | label `ORBIT_NUMBER` (see note below) |
| `orbit_dir` | int16 | orbit number from the directory the file was mirrored under |
| `version` | str | e.g. `V02` |
| `family` | str | `IMG` |
| `band` | str | `M`, `L`, `LM`, or `none` -- from `INSTRUMENT_MODE_DESC` / `lines==256` (see decisions) |
| `geom_band` | str | which band group in the label carries numeric geometry: `M`, `L`, or `none` |
| `label_path`, `img_file_name` | str | relative label path; `^IMAGE` pointer target |
| `img_size_bytes` | int64 | `FILE_RECORDS * RECORD_BYTES` |
| `img_present` | bool | whether the data file existed, at that size, when the index was last built for this orbit -- a snapshot, not a live check (see caveat below) |
| `md5_checksum` | str | label `MD5_CHECKSUM` |
| `parse_ok`, `parse_error` | bool, str | label parsed without error; all 85,108 rows have `parse_ok == True` in the current mirror |
| `sequence_number`, `sequence_samples` | int16 | archive `SEQUENCE_NUMBER` / `SEQUENCE_SAMPLES` |
| `command_file_name`, `mission_phase_name`, `instrument_mode_id`, `instrument_mode_desc`, `channel_id`, `frame_type`, `data_quality_id`, `calibration_source_id`, `source_product_id` | str | as labelled |
| `spice_kernels` | str | `SPICE_FILE_NAME` entries joined with `;` |

Time: `start_time`, `stop_time` (datetime64[ns], naive UTC), `exposure_s`
(float64, seconds), `sclk_start`, `sclk_stop` (str).

Image: `lines`, `samples` (int32; 128 or 256, 432), `sample_type` (str,
label says `IEEE_REAL` -- see the byte-order decision), `sample_bits`
(int16, 32), `unit` (str).

Spacecraft geometry (float64, degrees/km, label-carried, NaN where
labelled `"N/A"`): `sub_sc_lat`, `sub_sc_lon`, `sub_sc_azimuth`,
`sc_altitude_km`, `target_center_distance_km`, `sub_solar_lat`,
`sub_solar_lon`, `sub_solar_azimuth`, `sc_sun_distance_km`,
`sc_target_pos_{x,y,z}_km`, `sc_target_vel_{x,y,z}_kms`, `ra_deg`,
`dec_deg`, `twist_deg`, `celestial_north_clock_angle_deg`, `q_w`, `q_x`,
`q_y`, `q_z`, `target_name` (str), `target_pixels` (int64),
`target_presence_flag` (str).

Band-group geometry (float64, from the `geom_band` group, NaN where
`geom_band == "none"` or the label says `"N/A"`): `incidence_angle`,
`emission_angle`, `phase_angle`, `slant_distance_km`, `min_lat`,
`center_lat`, `max_lat`, `westernmost_lon`, `center_lon`,
`easternmost_lon`, `h_pixel_scale_m`, `v_pixel_scale_m`,
`north_azimuth`, `line_first_pixel`, `sample_first_pixel`. **This is the
weaker of the two geometry sources in the catalog**: it is present for
only about 25% of frames (absent for orbits >= 39 and for most 256-line
products -- see `docs/decisions.md` and `docs/open_items.md`) and is
kept only as a cross-check against `frames_geo.parquet`.

Sequence columns, computed after parsing (see `docs/architecture.md` for
the segmentation rule): `seq_id` (str), `seq_index` (int32, 0-based),
`seq_n` (int32, row count of the sequence), `seq_gap_s` (float64,
seconds since the previous row in the sequence, NaN for the first).

**Schema-check findings** (not stated in any spec, found while opening
the file):

- `orbit` is `float64` in the mirrored table, not the `int16` the crawl
  spec calls for: 4,697 of 85,108 rows have a null label `ORBIT_NUMBER`,
  and a nullable integer column round-trips through Parquet as float.
  Use `orbit_dir` (never null, always the directory the file lives
  under) for any orbit selection; every downstream module does.
- `orbit` disagrees with `orbit_dir` on 3,900 rows (lead-verified
  2026-09-05): every one of them is a product filed under `orbit03`
  whose label says `ORBIT_NUMBER = 1`. This is an archive property, not
  a parsing bug; `orbit_dir` is authoritative throughout this codebase.
- `img_present` reflects the mirror's state the last time `index` was
  run for that orbit, not the state now: a background job has since
  mirrored image data far beyond the 1,111 orbit-4 files that were
  present when the index was built (30,400 of 85,108 `.IMG` files exist
  on disk as of this writing; that number is a snapshot of a job that
  may still be running, not a fixed fact -- rerun `index` to refresh
  `img_present`, or just check the filesystem).

## `index/frames_geo.parquet`

One row per (frame, band half) -- a 256-line `LM` product contributes
two rows, one per half, because the halves see different parts of the
planet. This is the catalog's own SPICE-derived geometry (see
`docs/architecture.md`), computed by `jiram-catalog geo` and mandatory
for anything the label geometry above cannot cover. 38 columns,
**verified column-for-column against the mirror** (113,565 rows: one
per frame/half in the selected orbits, no mismatches).

| column | dtype | meaning |
| --- | --- | --- |
| `product_id` | str | |
| `orbit_dir` | int16 | |
| `half` | str | `L` or `M` |
| `start_time` | datetime64[ns] | geometry epoch = label `START_TIME` exactly |
| `geo_ok` | bool | SPICE computation succeeded |
| `geo_error` | str | exception text when it did not |
| `on_planet_frac` | float64 | fraction of the 128x432 pixels on the ellipsoid |
| `n_on_planet` | int32 | count of the same |
| `bore_lat`, `bore_lon_east`, `bore_emission`, `bore_incidence`, `bore_phase`, `bore_range_km` | float64 | boresight intercept; NaN if the boresight itself is off-planet even though other pixels are on it |
| `c1_lat`, `c1_lon`, `c2_lat`, `c2_lon`, `c3_lat`, `c3_lon`, `c4_lat`, `c4_lon` | float64 | the four detector-corner intercepts (line,sample) = (1,1),(1,432),(128,1),(128,432) |
| `min_lat`, `max_lat` | float64 | over on-planet pixels |
| `lon_min_east`, `lon_max_east`, `lon_span_deg` | float64 | smallest longitude arc containing all on-planet pixels (unwrapped about the circular mean); `[0, 360]` and `pole_inside=True` when a pole is inside the footprint or the arc exceeds 350 deg |
| `pole_inside` | bool | `max_lat > 89.5` (north) or `min_lat < -89.5` (south) |
| `mean_emission`, `mean_incidence`, `min_emission`, `max_emission` | float64 | over on-planet pixels |
| `median_pixel_km` | float64 | median of `range_km * ifov_rad` over on-planet pixels -- the frame's ground sample distance |
| `sub_sc_lat`, `sub_sc_lon_east`, `sc_altitude_km` | float64 | |
| `local_time_h` | float64 | boresight local solar time, hours, NaN off-planet |
| `dayside_frac` | float64 | fraction of on-planet pixels with incidence < 90 deg |

`jiram_catalog.geo.load_geo()` reads the table as-is; `frames_with_geo()`
joins it to `frames.parquet` on `product_id` (index columns first, then
these, unprefixed) -- this joined view, not either file alone, is what
every downstream module (`stacks`, `strips`, `trackability`, the GUI)
actually queries. `<mirror>/index/geo_report.md` (not a product, a
diagnostic) reports the label-vs-SPICE agreement per orbit; see
`docs/decisions.md` for the medians and `docs/open_items.md` for the
orbits where SPICE itself fails (38, 55, 70).

## `index/trackability_pairs.parquet`

One row per candidate repeat-view pair, written by
`scripts/trackability_report.py`. 12 columns, **verified against the
mirror** (31,308 rows):

`product_id_a`, `product_id_b` (str), `orbit_dir` (int16), `half` (str),
`seq_id_a`, `seq_id_b` (str), `dt_s` (float64, seconds between the two
`start_time`), `sep_km` (float64, great-circle boresight separation),
`pixel_km` (float64, `max` of the two `median_pixel_km`), `d_px_10`,
`d_px_30`, `d_px_100` (float64, expected displacement in pixels at
10/30/100 m s-1 -- see `docs/architecture.md` for the formula).

## `index/trackability_frames.parquet`

One row per unit-of-analysis frame (`geo_ok`, `on_planet_frac >= 0.3`,
`bore_emission <= 70`), written by the same script. 18 columns,
**verified column-for-column against the mirror** (29,862 rows):

`product_id` (string), `orbit_dir` (Int16), `half`, `seq_id` (string),
`start_time` (datetime64[ns]), `bore_lat`, `bore_lon_east`,
`median_pixel_km`, `on_planet_frac`, `bore_emission`, `dayside_frac`
(float64), `lat_band` (str, one of the seven bands in
`docs/architecture.md`), `n_partners` (int64), `has_partner` (bool),
`trackable_10`, `trackable_30`, `trackable_100` (bool, `0.5 <= d_px <=
40` at that reference speed), `best_dt_s` (float64, the smallest `dt_s`
among partners trackable at 30 m/s; NaN if none).

## Region stacks (`<mirror>/regions/<region>/*.nc`)

Written by `region-stack`; opened by `movie` and `export-goflow`, and by
the GUI's Poles tab. A stack is every frame that overlapped a named
region, reprojected onto that region's grid and laid along a time axis
(see `docs/architecture.md`, "regime 1"). Verified by opening
`north_pole_paper/M_orbits4_sequence.nc` (25 time steps, 3000x3200):

Dims `(time, y, x)`. Coords: `time` (datetime64[ns]); per-time
`seq_id`, `n_frames`, `orbit`, `time_first`, `time_last`,
`start_time_iso` (sequence level only; frame level instead carries
`product_id`, `seq_id`, `orbit`, `band`, `half`, `start_time_iso`,
`bore_emission`); `y_km`, `x_km` (1-D); `lat`, `lon_east` (2-D
float32). Variables: `image` (float32, NaN invalid, mean of contributing
frames for a sequence composite), `valid` (bool, any contributing frame
valid for a composite), `emission` (float32, min over contributing
frames). Attrs: `region`, `projection` (JSON of the region's `to_dict()`
-- see `docs/architecture.md` for what that means), `km_per_px`, `band`,
`level` (`frame` or `sequence`), `row0`/`col0` (crop offset into the
region's full canvas), `created_utc`, `software`.

`composite_sequences()` builds the `level="sequence"` file from a
`level="frame"` stack: one time step per spin sequence, `image` averaged
over valid pixels, `valid` = any frame valid, `emission` = min.

## Sequence composite

Not a separate file format -- a region stack with `level == "sequence"`
in its attrs and one time step per `seq_id` instead of one per frame; see
above.

## goflow realization layout (`<mirror>/regions/<region>/goflow_<band>_orbits<spec>/`)

Written by `export-goflow`, for the downstream optical-flow model
(`docs/architecture.md` explains why the layout looks like this).
Verified by opening `goflow_M_orbits4/r00000/`:

- `rNNNNN/realization.nc`: dims `(frame, y_img, x_img)`. Variables
  `image` (float32, invalid pixels zero-encoded, not NaN), `valid`
  (bool), `loggrad` (float32, `log10(max(|grad image|, 1e-30))`,
  gradient by central differences in per-metre units, zero where
  invalid). Attrs: `dx_img_m`, `dt_img_s` (one constant per realization
  -- the run's median frame spacing), `units_velocity = "m s-1"`,
  `units_image = "W m-2 sr-1 um-1"`, `region`, `band`, `level`,
  `projection`, `source = "jiram_catalog"`, `array_order = "C"`,
  `crop_origin`, `created_utc`. **No `u_mid`/`v_mid`**: no ground-truth
  velocity exists for real data, unlike the synthetic datasets this
  layout was designed to match (`docs/reports/goflow_summary.md`).
- `rNNNNN/manifest.json`: `realization`, `n_frames`, `dt_img_s`,
  `dx_img_m`, `shape`, `crop_origin`, `region`, `band`, `level`,
  `frames` (list of `{index, product_id, seq_id, time}`), `gaps_s`.
- `spec.json` (dataset root): `source`, `source_stack` (path to the
  region stack this run was cut from), `region`, `band`, `level`,
  `projection`, `dx_img_m`, `km_per_px`, `dt_tol`, `min_frames`,
  `crop_to_valid`, `units_image`, `units_velocity`,
  `truth_velocities: false`, `created_utc`.
- `dataset_manifest.json` (dataset root): `n_realizations`,
  `realizations` (list of `{name, n_frames, dt_img_s, dx_img_m, shape}`).

A "run" is a maximal stretch of consecutive time steps whose spacing is
constant to within `--dt-tol` (default 5%) of the run's median spacing
and at least `--min-frames` (default 3) long; each run becomes one
`rNNNNN/`.

## Strip NetCDF (`<mirror>/strips/orbitNN/<strip_id>.nc`)

One reprojected swath from one spin sequence (see `docs/architecture.md`,
"regime 2"). Verified by opening `orbit04/04_L_2017033T035859_00.nc`
(453x533, 7 contributing frames):

Dims `(y, x, frame)`. Coords: `y_km`, `x_km` (1-D); `lat`, `lon_east`,
`local_time_h` (2-D float32); `product_ids` (1-D str, one per
contributing frame), `frame_times` (1-D datetime64[ns]). Variables:
`image` (float32, mean of contributing frames' bilinear samples, NaN
where none), `valid` (bool), `emission`, `incidence` (float32, min over
contributing frames), `n_frames` (uint8, contributing count per pixel),
`frame_index` (int16, index into `product_ids` of the last contributing
frame, -1 where none). Attrs (26): `strip_id`, `orbit`, `seq_id`,
`chunk_index`, `band`, `n_frames`, `time_start`, `time_end`, `time_mid`,
`center_lat`, `center_lon_east`, `km_per_px`, `resolution_class`,
`projection` (JSON), `valid_frac`, `dayside_frac`, `median_emission`,
`lat_min`, `lat_max`, `lon_min_east`, `lon_max_east`, `lon_span_deg`,
`pole_inside`, `capped`, `created_utc`, `software`.

## `strips/strips.parquet`

The strip library index, one row per strip. 28 columns, **verified
column-for-column against the mirror** (289 rows, orbits 4 and 24, both
bands): the same 25 of the 26 strip-NetCDF attrs above that are indexed
(every one except `capped`) -- `strip_id`, `orbit`, `seq_id`,
`chunk_index`, `band`, `n_frames`, `time_start`, `time_end`, `time_mid`,
`center_lat`, `center_lon_east`, `km_per_px`, `resolution_class`,
`projection`, `valid_frac`, `dayside_frac`, `median_emission`,
`lat_min`, `lat_max`, `lon_min_east`, `lon_max_east`, `lon_span_deg`,
`pole_inside`, `created_utc`, `software` -- plus `path` (relative to
the mirror root), `rows`, `cols`.

`load_strips(mirror, ...)` filters this table (latitude overlap,
time window, `resolution_max_km`, `min_valid_frac`, `dayside_min`,
`band`, `orbits`); `read_strip(mirror, strip_id | path)` opens the
NetCDF.

## Strip statistics NetCDF (`stats2d.strip_statistics`, cached at `<mirror>/gui_cache/stats_<strip_id>.nc`)

Verified by opening `gui_cache/stats_04_L_2017033T035859_00.nc`. Dims
`k` (isotropic shell centres), `kx`, `ky` (1-D spectra), `r` (structure-
function lags). Variables: `E` (`k`, shell-integrated variance density),
`count_k` (`k`, lattice points in the shell), `inside_disc` (`k`, bool,
whether the shell lies inside the Nyquist disc -- see
`docs/decisions.md` for why every shell out to the lattice corner is
kept rather than dropped), `P_x` (`kx`), `P_y` (`ky`) (1-D spectra along
the two axes), `S2`, `S3` (`r`, structure functions, orders 2 and 3),
`count_r` (`r`, valid pairs per lag). Attrs: `strip_id`, `km_per_px`,
`band`, `orbit`, `time_mid`, `valid_frac`, `conventions`
(`conventions-v1`), `dx_m`, `dk`, `k_nyq`, `variance`,
`spectrum_valid_frac`, `window_power`, `n_lines_x`, `n_lines_y`,
`max_lag_px`, `rows`, `cols`.

Units: `k`/`kx`/`ky` are angular wavenumbers, rad m-1; `E` integrates
(over `k`, `dk`-weighted, inside the disc) to the field's variance, not
kinetic energy -- see `docs/decisions.md` for the departure from the
velocity-field convention this module otherwise follows verbatim.
`population_statistics()` produces the same shape of Dataset averaged
over every strip of one resolution class, plus `n_strips`, `km_per_px`
attrs and a standard error alongside each mean.

## GUI cache (`<mirror>/gui_cache/`)

The 2026-09-07 research extension uses `research/population_<hash>.json`
for matched groups, per-pass uncertainty, fit ranges, mask diagnostics and
recipes. Hashes include sources, modification times, settings, policy and
software revision. `research/movie_<hash>.mp4` holds atomically written,
physical-band movies of the eligible stack view. New export directories
under `exports/` include filtered source/settings identity; their `spec.json`
and realization manifests retain native image units, physical band,
normalization and provenance. Readiness reports independent observations,
gaps and usable cadence runs before writing. A realization is a cadence run
containing at least three frames; it can supply more than one sliding triple.

JunoCam Arrow/detail rows additionally carry observation identity, numeric
version, preferred status, quality status/reasons and trackability status.
Missing trackability is unassessed. The underlying source index is preserved;
the ordinary mapped catalog includes only eligible preferred observations.
Coverage/archive endpoints expose metadata and staged processing counts even
when images cannot be served. Units `DN`, measured radiance and generated
HST-equivalent `I/F` are distinct products. The ML sample audit stays under
`junocam/calibration_review/` and is not added to native observation tables.

Other GUI-managed files are `stats_<strip_id>.nc` (the legacy file described
just above, one per strip with computed statistics), `meta_<key>.json`
(a stack's display stretch and graticule, cached on first open),
`selections/<id>.json` (named frame selections saved from the
selection tray, `POST /api/selections`), `jobs/` (a JSON record per
background job, mirrored so a restarted server can still report what
the last run produced), and `exports/` for requested goflow datasets.
Current movies and population results use the `research/` paths above;
figure downloads are produced in the browser. See
`docs/gui_usage.md`.

## SPICE kernel mirror (`<mirror>/spice/{lsk,pck,fk,ik,sclk,spk,ck}/`)

Not a data product in the same sense -- an input the geometry engine
needs, managed by `jiram-catalog kernels`. See `docs/architecture.md`
for what each kind is and `docs/configuration.md` for how the mirror
root is chosen. `<mirror>/spice/manifest/naif_juno_kernels.csv` is a
one-time inventory of everything available on NAIF's servers
(`docs/reports/spice_kernel_coverage.md`), not something any command
maintains.
