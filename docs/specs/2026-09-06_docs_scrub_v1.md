# Spec: documentation update — the React GUI is the only GUI

Working directory: /expanse/lustre/projects/cla119/kaushiks/jiram_catalog.
The earlier plotting-library-based GUI (v1) was deleted from the code
by another agent (spec `docs/specs/2026-09-06_remove_gui_v1.md`).
Update every current-state document so the React + FastAPI GUI is the
only one described, while the build log and the decision log keep an
honest one-line history of the replacement.

## Files in scope (modify unless stated)
- `docs/gui_usage.md`: rewrite for v2 (same serve/tunnel commands; no
  legacy serve flag; describe the backend/front-end split in one paragraph).
- `docs/gui_design.md`: delete. Fold any still-true intent into
  `docs/gui_v2_notes.md` (append a short "Design intent" section; do
  not rewrite the rest, another agent edits two sentences of it).
- `docs/README.md`, `docs/architecture.md`, `docs/usage.md`,
  `docs/data_products.md`, `docs/open_items.md`, `docs/agent_harness.md`,
  `README.md`, `CONTRIBUTING.md`, `AGENTS.md`: remove or rewrite every
  mention of the deleted plotting-library GUI and its dependencies,
  its legacy serve flag,
  the `gui/` package, `gui_cache` semantics that changed, and the v1
  reactivity open item; describe `src/jiram_catalog/api/` and
  `frontend/` where the GUI layer is described (module map, product
  layout, usage, harness conventions such as "executors do not edit
  frontend build files without rebuilding").
- `docs/decisions.md`: add one entry: "2026-09-06: GUI rebuilt as a
  FastAPI backend and a React/deck.gl front end after the owner's test
  of the Panel version (colour maps not applying, no repaint, movie not
  shown, no hover on rasterised points, aspect drift, confusing
  send-to); the Panel version was removed the same day. Enforced in
  `gui_cmd.py`, `src/jiram_catalog/api/`, `frontend/`."
- `PEDAGOGICAL_REVIEW.md`: rewrite section 4.16 (`gui/`) as "4.16
  `api/` and `frontend/` — the browser" describing the v2 design:
  the contract, Arrow catalog, PNG frames with validity alpha, LUT
  colour maps in the browser, deck.gl OrthographicView and picking,
  the selection tray, jobs, the e2e gate; keep the section's length
  similar; update the module map in section 3.1 and any other v1
  mention (grep). Keep the "scars" flavour: the v1 failures are the
  scar that motivated v2.
- `docs/pedagogy/slides.tex`: update the GUI slides to match, then
  recompile with `latexmk -pdf -interaction=nonstopmode slides.tex`
  in `docs/pedagogy/` so `slides.pdf` is current.
- `docs/specs/2026-09-05_gui_v1.md`: delete (its history is in the
  build log).
READ-ONLY: code, `frontend/`, gates, `docs/build_log_*.md`,
`docs/reports/`, other specs. No executor product names anywhere.

## Validation that defines done
```
a search across README.md, AGENTS.md, CONTRIBUTING.md, docs/*.md,
PEDAGOGICAL_REVIEW.md, and docs/pedagogy/slides.tex for the deleted
plotting-library GUI's dependency names and its old serve flag,
excluding the build log, decisions log, and docs/reports, returns
nothing except the decisions entry
test ! -e docs/gui_design.md && test ! -e docs/specs/2026-09-05_gui_v1.md
test -s docs/pedagogy/slides.pdf
a search for executor product names across README.md, docs/*.md, and PEDAGOGICAL_REVIEW.md returns zero
```

## Report (at most 20 lines)
Files changed/deleted; the grep result; slide count; judgment calls.
