# GUI v2 API contract (shared by the backend and the front end)

Base path `/api`. JSON unless stated. Times are ISO-8601 UTC strings.
Errors: JSON `{"detail": "..."}` with 4xx/5xx. All list endpoints
accept `limit` (default unlimited) only where stated. CORS is not
needed (same origin); the backend serves the built front end at `/`.

## Config and health
- `GET /api/config` -> `{mirror, paper_data, version, counts: {frames_on_planet, strips, stacks, selections}, has_trackability: bool}`
- `GET /api/health` -> `{"ok": true}`

## Catalog
- `GET /api/catalog/frames.arrow` -> Apache Arrow IPC stream
  (`application/vnd.apache.arrow.stream`), one row per band-half row
  of `frames_with_geo` with `geo_ok & on_planet_frac > 0`, columns
  (exact names, types): `product_id` (utf8), `half` (utf8), `band`
  (utf8), `orbit` (int16, from `orbit_dir`), `seq_id` (utf8),
  `start_time_ms` (int64, epoch milliseconds UTC), `bore_lat`,
  `bore_lon_east`, `bore_emission`, `on_planet_frac`, `median_pixel_km`,
  `dayside_frac`, `min_lat`, `max_lat`, `lon_span_deg` (float32),
  `pole_inside` (bool), `c1_lat`, `c1_lon`, `c2_lat`, `c2_lon`,
  `c3_lat`, `c3_lon`, `c4_lat`, `c4_lon` (float32, NaN if missing),
  `has_partner` (bool, false when trackability is absent),
  `best_dt_s` (float32, NaN when absent). Cached in memory after the
  first build; ETag set; supports `If-None-Match`.
- `GET /api/catalog/summary?<filters>` -> `{n, by_lat_band: [{band, n}], by_orbit: [{orbit, n}], by_month: [{month: "YYYY-MM", n}]}`
  where filters are the query parameters `orbit_min`, `orbit_max`,
  `time_min`, `time_max`, `half` (`L`|`M`), `pixel_max_km`,
  `emission_max`, `on_planet_min`, `dayside_only` (bool), `lat_min`,
  `lat_max`, `revisit_only` (bool); missing values in a row exclude it
  for `on_planet_min` and for `lat_min`/`lat_max` (a frame whose
  boresight misses the planet has no latitude and never matches a
  latitude filter; REVISED 2026-09-06); other thresholds keep rows
  whose value is missing (a threshold excludes a row only when its value is
  known and fails it). The seven latitude bands are those
  of the trackability report.
- `GET /api/catalog/frame/{product_id}` -> full row of `frames_with_geo` for that product (all columns, JSON, NaN as null), plus `halves: ["L","M"]` present.

## Selections (shared, stored under `<mirror>/gui_cache/selections/<id>.json`)
- `GET /api/selections` -> `[{id, name, created, n_frames, n_orbits, lat_min, lat_max, note}]`
- `POST /api/selections` body `{name, product_ids: [...], halves?: [...], note?}` -> the record above with `id` (slug of name + short hash). Duplicate names get a numeric suffix.
- `GET /api/selections/{id}` -> record plus `product_ids` and `halves`.
- `DELETE /api/selections/{id}` -> `{deleted: id}`.

## Stacks (files under `<mirror>/regions/<region>/*.nc`)
- `GET /api/stacks` -> `[{id, region, band, level, path, n_time, shape: [ny, nx], km_per_px, size_bytes, has_movie: bool, movie_path}]` where `id` = `<region>/<file stem>`.
- `GET /api/stacks/{id}/meta` -> `{id, region, band, level, km_per_px, x_km: [min,max], y_km: [min,max], shape, times: [...], per_time: [{i, product_id, seq_id, orbit, n_frames?, bore_emission?}], stretch: {p1, p99} (computed once on a subsample), graticule: GeoJSON FeatureCollection of LineStrings in km coordinates (latitude circles every 2 deg, longitude meridians every 30 deg, split at the seam), attrs}`.
- `GET /api/stacks/{id}/frame/{t}.png?vmin=&vmax=&max_px=1600` -> 8-bit grayscale PNG of `image[t]` linearly stretched between `vmin` and `vmax` (defaults = meta stretch), invalid pixels transparent (RGBA with alpha 0), downsampled by integer striding so the longer side is <= `max_px`; response headers `X-Rows`, `X-Cols` (served size), `X-Stride`, `X-Bounds` (`xmin,xmax,ymin,ymax` in km for the served image, row 0 = ymin), `Cache-Control: max-age=3600`.
- `GET /api/stacks/{id}/frame/{t}/emission.png?max_px=` -> same but emission angle 0-90 mapped to 0-255.
- `GET /api/stacks/{id}/movie` -> the MP4 with HTTP Range support (206 on `Range: bytes=0-1023`); 404 when absent.
- `POST /api/stacks/{id}/movie` body `{fps?, pct?: [1, 99], cmap?}` -> starts a render job; returns `{job_id}`.
- `POST /api/stacks/build` body `{region, band, level, orbits?: [...], selection_id?, max_emission?}` -> `{job_id}`; the job runs `stacks.select_frames`/`build_stack` (restricted to the selection's product ids when `selection_id` is given) and writes the file with `stack_output_path`.
- `POST /api/stacks/{id}/export` body `{out_dir?, dt_tol?, min_frames?, crop_to_valid?}` -> `{job_id}` (goflow export).

## Strips
- `GET /api/strips.arrow` -> Arrow IPC of `strips.parquet` (all columns; datetimes as int64 ms with suffix `_ms`).
- `GET /api/strips/{strip_id}/meta` -> attrs, `x_km`/`y_km` ranges, shape, `stretch`, `graticule` (as for stacks), `local_time_contours` (GeoJSON, every 2 h).
- `GET /api/strips/{strip_id}/image.png?vmin=&vmax=&max_px=` -> as the stack frame.
- `GET /api/strips/{strip_id}/stats` -> `{k, E, k_x, P_x, k_y, P_y, r_m, S2, S3, attrs}` from `stats2d.strip_statistics`, cached under `<mirror>/gui_cache/stats_<strip_id>.nc` (reuse `api.data.strip_stats`).

## Jobs (thread pool, in-process)
- `GET /api/jobs` -> `[{id, kind, status: queued|running|done|failed, progress: 0-1, message, started, finished, result}]`
- `GET /api/jobs/{id}` -> one record. `result` for a movie job is `{path}`; for a build job `{stack_id, path}`; for export `{out_dir, n_realizations}`.
- `DELETE /api/jobs/{id}` -> cancels if queued (running jobs finish).

## Static
- `GET /` and any non-`/api` path -> the built front end (`index.html` fallback for client routing) from `src/jiram_catalog/webapp/dist/`; 503 with a plain-text hint when the bundle is missing.

## Amendment 2026-09-07: instruments, bands, footprints (JunoCam)
- `GET /api/config.counts` gains `junocam_images`.
- `frames.arrow` gains columns: `instrument` (utf8: `JIRAM` or `JunoCam`),
  `bands` (list<utf8>; JIRAM: one entry, the half `L`/`M`; JunoCam:
  the FILTER_NAME order; CORRECTED 2026-09-07 from a `;`-joined string
  because methane-only products exist), `quality_tier` (utf8: `A`/`B`/`C`, `A` for
  JIRAM rows), `fp_lon` and `fp_lat` (list<float32>, up to 64 vertices
  of the on-planet footprint outline in east longitude and
  planetocentric latitude, split into separate polygons on the seam by
  the client; empty lists for JIRAM rows). JunoCam rows: one per RDR
  image with geometry (`junocam_geo.parquet`), `half` = `""`, `orbit`,
  `seq_id` = product id, `start_time_ms`, `bore_*` = swath-centre
  values, `on_planet_frac`, `median_pixel_km`, `dayside_frac`,
  `min_lat`, `max_lat`, `lon_span_deg`, `pole_inside`, corners NaN,
  `has_partner` false, `best_dt_s` NaN.
- Summary filters gain `instrument` (`JIRAM`|`JunoCam`|absent for both),
  `bands` (a band name that must be present: `L`, `M`, `RED`, `GREEN`,
  `BLUE`, `METHANE`), `quality_min` (`A`|`B`|`C`, default `B`: rows of
  tier `C` are hidden unless `quality_min=C`).
- `GET /api/stacks` items gain `instrument` and `bands` (list); stacks
  with a `band` dimension expose `meta.bands`; `frame/{t}.png` accepts
  `band=<name>` (required when the stack has several bands) and
  `frame/{t}/rgb.png?vmin_r&vmax_r&...` returns an RGB composite
  (per-band linear stretch, defaults from `meta.stretch[band]`).
- `POST /api/stacks/build` gains `instrument` (default `JIRAM`),
  `bands` (list; JunoCam default `["RED","GREEN","BLUE"]`),
  `quality_min` (default `A`).
- `strips.arrow` gains `instrument` and `bands`; strip `meta.bands`;
  `image.png?band=`; `stats?band=` (default: the first band).
