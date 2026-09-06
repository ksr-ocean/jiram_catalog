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
