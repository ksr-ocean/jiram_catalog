# Spec: GUI v2 backend (FastAPI service over the existing modules)

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog
(`uv` project; `fastapi`, `uvicorn[standard]`, `httpx` (dev) are
installed; `pyarrow`, `xarray`, `matplotlib`, `imageio` present).
Read first: `docs/specs/2026-09-06_api_contract.md` (the contract; it
is normative), `src/jiram_catalog/gui/data.py` (loaders and caches to
reuse: `catalog_table`, `strips_table`, `trackability_table`,
`open_stack`, `open_strip`, `strip_stats`, `apply_filters`),
`gui/views_poles.py` (graticule helper to reuse), `stacks.py`,
`export_goflow.py`, `movie.py`, `strips.py`, `stats2d.py`,
`config.py`, `gui_cmd.py`, `cli.py`.

## Files in scope
- `src/jiram_catalog/api/__init__.py`, `app.py` (factory
  `create_app(mirror) -> FastAPI`), `catalog.py`, `selections.py`,
  `stacks.py`, `strips.py`, `jobs.py`, `images.py` (PNG encoding),
  `arrow.py` (DataFrame -> Arrow IPC bytes)   (create)
- `src/jiram_catalog/gui_cmd.py` (modify: `gui` now serves the v2 app
  with uvicorn on `--port`/`--address`; `--legacy` serves the Panel v1;
  keep `--no-browser`; add `--reload` for development)
- `src/jiram_catalog/webapp/__init__.py` and `webapp/dist/.gitkeep` (create; the front end build lands in `dist/` from another task)
- `tests/test_api_offline.py` (create)
READ-ONLY: everything else, including `cli.py`, `pyproject.toml`,
`uv.lock`, gates, `gui/` (reuse by import only). Under the mirror root
write only under `<mirror>/gui_cache/` (selections, stats cache) and,
for jobs the user starts, the paths the existing modules write
(`regions/`, goflow export directories). Never write under the
paper-data directory or `~/scratch`. Another agent is concurrently
building the front end under `frontend/`; do not touch it.

## Decisions
- One process, one thread pool for jobs (`concurrent.futures`, 2
  workers); job records in memory plus a JSON mirror under
  `<mirror>/gui_cache/jobs/` so a restart can list finished jobs.
  Stack builds call `stacks.build_stack` in the worker thread with
  `jobs=1`... unless the function spawns its own workers, in which case
  pass its default and document it.
- Catalog Arrow table built once at startup (lazily on first request),
  ETag = hash of the mirror index file mtimes.
- PNG encoding with `imageio` (or `PIL` if present) from a uint8 RGBA
  array; the grayscale value is in R=G=B, alpha 255 where valid.
- Stretch defaults: `p1`/`p99` percentiles over a strided subsample of
  up to 4 time steps and 2e6 values, computed once per stack and cached
  in the meta cache (`<mirror>/gui_cache/meta_<id>.json`).
- Graticule: reuse GUI v1's helper; output GeoJSON in km coordinates.
- Range support for the movie: implement explicitly (parse `Range`,
  return 206 with `Content-Range`, `Accept-Ranges: bytes`) rather than
  relying on `FileResponse`.
- Uvicorn is started in-process (`uvicorn.run(app, host, port)`);
  `--reload` uses the import string.
- Logging via `logging`; no prints.

## Offline tests (`tests/test_api_offline.py`; synthetic mirror in tmp; `httpx` TestClient)
Build a tmp mirror with a synthetic `frames.parquet`/`frames_geo.parquet`
(20 rows), a synthetic 2-step stack NetCDF and a synthetic strip and
`strips.parquet` (use the same synthetic builders as
`tests/test_gui_offline.py` where possible); then: `/api/config`,
`frames.arrow` parses with pyarrow and has the contract's columns and
types; `summary` filters behave (each filter once); selection create
/list/get/delete round trip; `/api/stacks` lists the synthetic stack;
`meta` has times, stretch, graticule; `frame/0.png` decodes to the
served size with headers; movie 404 then, after writing a small fake
file, 206 with `Range: bytes=0-9`; strip `meta`, `image.png`, `stats`
(monkeypatch `strip_stats` to a synthetic Dataset); a job runs to
`done` (use a trivial registered kind for the test) and is listed;
static fallback returns 503 without a bundle and serves `index.html`
when a fake `dist/index.html` exists.

## Validation that defines done
```
JIRAM_SKIP_GATES=1 uv run pytest -q tests/test_api_offline.py
uv run python -m jiram_catalog.gui_cmd --help
uv run pytest -q tests/test_api_offline.py tests/test_gate_api.py
```
READ-ONLY gate `tests/test_gate_api.py` (real mirror, TestClient):
config counts; `frames.arrow` >= 40,000 rows with the contract columns;
summary for `half=M&lat_min=60` has `n > 1000`; the sequence stack
`north_pole_paper/M_orbits4_sequence` lists with `n_time == 25`,
`has_movie` true, its frame 3 PNG decodes with alpha, its movie answers
206 to a range; a strip meta and stats return; a selection round trip
against the real mirror (then deleted).

## Report (at most 40 lines)
Commands and outcomes; response times for `frames.arrow` (cold and
warm), a stack frame PNG, a strip stats call; judgment calls;
ambiguities with the choice made.
