# JIRAM catalog front end (GUI v2)

React 18 + TypeScript + deck.gl, built by Vite into the Python package.

    npm ci                 # install (Node 22+; package-lock.json is committed)
    npm run dev            # dev server on :5173, /api proxied to :5006
    npm run typecheck      # tsc --noEmit, strict
    npm test               # vitest unit tests (projections, filters, LUTs, store)
    npm run build          # writes ../src/jiram_catalog/webapp/dist
    npx playwright test    # e2e; set E2E_BASE_URL to a running backend

Start a backend first with `uv run jiram-catalog gui --port 5006 --no-browser`.
Architecture and state model: `docs/gui_v2_notes.md`.

## Scientific workspace

The visible workflows are Explore, Time series, Image library, Compare and
Coverage. The original `catalog`, `poles` and `strips` state IDs and test hooks
remain stable. Views stay mounted across navigation. The selection tray can be
collapsed without dropping its contents; map density and tray preferences use
the guarded local-storage helpers.

`api/research.ts` defines the additive coverage and science contracts.
`CoverageView` browses archive metadata without implying pixel eligibility.
`DetailCard` is shared by Explore and Coverage and shows source versions,
quality reasons and instrument thumbnails only where permitted. JunoCam
instrument-failure exclusion is enforced by the backend; the interface exposes
no override. Latitude filtering defaults to footprint overlap and sends the
same mode to the server's summary query.

`CompareView` loads existing physical-band maps, links their cameras and asks
the science service for compatible-grid registration and navigation limits.
Common-mask display requires matching served image dimensions and bounds.
Registration is image alignment, not a measured atmospheric velocity.
`PopulationPanel` requests independent per-pass statistics for selected library
images, exposes diagnostics and normalization sensitivity, and exports the
complete recipe. Figures have named SVG/PNG controls despite the hidden Plotly
modebar. RGB display requires a separate physical band for quantitative stack
exports and movies.

Run the accepted-review browser gate without editing it:

    E2E_BASE_URL=http://127.0.0.1:5173 npx playwright test review.spec.ts

`research.spec.ts` adds coverage, selection persistence, explicit RGB analysis
band and comparison-recipe checks. Backend diagnostics and cold-start timings
belong in the implementation report rather than being hidden by test retries.
