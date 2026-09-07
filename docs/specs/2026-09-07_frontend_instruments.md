# Spec: front end — instruments, footprints, bands, colour composites (JunoCam milestone 3b)

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog/frontend
(and the committed bundle under `src/jiram_catalog/webapp/dist`).
Read first: the amendment at the end of `docs/specs/2026-09-06_api_contract.md`
(normative), `docs/gui_v2_notes.md`, `frontend/src/**`, `frontend/e2e/**`.
The backend implementing the amendment is being built concurrently
under `src/jiram_catalog/api/`; until it lands, work against the
contract and unit-test with mocked responses; run the e2e suite when
`GET /api/config` reports `counts.junocam_images > 0`.

## Changes
- Catalog: an **instrument** filter (JIRAM / JunoCam / both) and a
  **band** filter whose options depend on the instrument(s) in view;
  a **quality** filter (A only / A+B / all) defaulting to A+B. JunoCam
  rows draw as footprint outlines (`PolygonLayer`, stroked, filled at
  low alpha, seam-split into separate polygons when the outline crosses
  0/360) in both cylindrical and polar views; JIRAM rows stay points;
  hover and click work on both; colour legend by instrument is a new
  colour-by option. Coverage charts use the server summary with the
  new filters. The tray shows counts per instrument.
- Poles: stacks with a `band` dimension show a **band selector** and an
  **RGB composite** option when RED, GREEN and BLUE are present; the
  composite uses `frame/{t}/rgb.png` with per-band stretch sliders (one
  shared pair by default, "link bands" toggle); the lookup-table colour
  maps apply only to single-band display. The mode selector hides
  modes whose siblings cannot exist for the instrument (JunoCam has
  only `frame`). The build dialog gains instrument, bands, and quality.
- Strips: instrument and band filters in the table; band selector and
  RGB option in the viewer; the statistics panel computes for the
  selected band (`stats?band=`).
- `#debug-state` gains `instrument_filter`, `band`, `composite` (bool),
  `n_footprints`.
- Tests: vitest for the seam-splitting of outlines, filter logic with
  instrument/band/quality, RGB stretch state; e2e: instrument filter
  changes `n_filtered` and `n_footprints > 0` for JunoCam; hover on a
  footprint shows a tooltip with the product id; opening the JunoCam
  north-pole stack shows the band selector, switching to RGB sets
  `composite=true` and changes the sampled pixel; a JunoCam strip shows
  statistics for GREEN.

## Files in scope
`frontend/src/**`, `frontend/tests/**`, `frontend/e2e/**`,
`src/jiram_catalog/webapp/dist/**` (rebuild), `docs/gui_v2_notes.md`
(the paragraphs that change), `docs/gui_guide.md` (Catalog/Poles/Strips:
the new controls, no new screenshots required). READ-ONLY: everything
else. No new dependencies.

## Validation that defines done
```
cd frontend && npm run typecheck && npm test && npm run build
uv run pytest -q tests/test_gate_frontend.py        # when the backend reports junocam_images > 0
```
The READ-ONLY gate `tests/test_gate_frontend.py` runs the whole
Playwright suite, so the new e2e tests are part of it.

## Report (at most 35 lines)
Commands and outcomes; bundle size; what could not be exercised;
judgment calls; ambiguities with the choice made (implement everything else).
