# Spec: remove the Panel GUI (v1) and make the React GUI the only one; strip statistics toggle

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog.
Read first: `src/jiram_catalog/api/*.py` and `tests/test_api_offline.py`
(they import helpers from `jiram_catalog.gui.data` and
`jiram_catalog.gui.state`: `gui_cache_dir`, `strips_table`,
`has_trackability`, `lat_band`, `LAT_BAND_NAMES`, `trackability_table`,
`open_strip`, `contour_paths`, `stack_stretch`, `strip_stats`,
`stack_paths`, `graticule_for`, `catalog_table`, `open_stack`),
`src/jiram_catalog/gui_cmd.py`, `frontend/src/views/` (the Strips
view), `frontend/e2e/`.

## Part A: remove v1 (Python)
1. Create `src/jiram_catalog/api/data.py` and move into it, unchanged in
   behaviour, every function the api package and its tests use from
   `gui/data.py` and `gui/state.py` (list above; include their private
   helpers and the latitude-band constants). Keep names. Drop anything
   that imports panel, holoviews, hvplot, bokeh, datashader or param;
   the moved code must import none of them.
2. Update imports in `api/*.py` and `tests/test_api_offline.py` to
   `jiram_catalog.api.data`.
3. Delete `src/jiram_catalog/gui/` entirely and
   `tests/test_gui_offline.py`. (The lead has already removed
   `tests/test_gate_gui.py`.)
4. `gui_cmd.py`: remove `--legacy` and every reference to the Panel app;
   the `gui` subcommand serves the FastAPI app only. Update its module
   docstring.
5. `grep -rn "panel\|holoviews\|hvplot\|bokeh\|datashader\|import param" src tests scripts`
   must return nothing (case-insensitive on the package names; a
   `jiram_catalog.api` docstring mentioning "the earlier Panel front
   end" must be rewritten too).
Do not edit `pyproject.toml`/`uv.lock` (the lead removes the packages
after you report); the code must not depend on them any more.

## Part B: strip statistics toggle (front end)
In the Strips view, the statistics panel (three Plotly figures) is
hidden by default behind a "Show statistics" toggle button placed in
the view toolbar; when hidden, the strip image viewer takes the full
main area height. The toggle state lives in the zustand store and
persists to localStorage. The `#debug-state` JSON gains
`stats_visible: boolean`. Update the e2e test that checks the
statistics plots to click the toggle first, and add one assertion that
the viewer's canvas is taller with statistics hidden than shown.
Rebuild the bundle (`npm run build`) so `src/jiram_catalog/webapp/dist`
is current; update `docs/gui_v2_notes.md` (state model, Strips view)
in the two sentences that change.

## Files in scope
`src/jiram_catalog/api/**`, `src/jiram_catalog/gui/**` (delete),
`src/jiram_catalog/gui_cmd.py`, `tests/test_api_offline.py`,
`tests/test_gui_offline.py` (delete), `frontend/src/**`,
`frontend/e2e/**`, `frontend/tests/**`, `src/jiram_catalog/webapp/dist/**`,
`docs/gui_v2_notes.md`. READ-ONLY: everything else (including
`cli.py`, `pyproject.toml`, `uv.lock`, gates, `docs/` otherwise).

## Validation that defines done
```
grep -rni "panel\|holoviews\|hvplot\|bokeh\|datashader\|import param" src tests scripts   # nothing
JIRAM_SKIP_GATES=1 uv run pytest -q
uv run pytest -q tests/test_api_offline.py tests/test_gate_api.py
cd frontend && npm run typecheck && npm test && npm run build
uv run pytest -q tests/test_gate_frontend.py
```

## Report (at most 25 lines)
Files moved/deleted/changed; all validation tails; judgment calls;
ambiguities with the choice made.
